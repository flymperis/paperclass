"""Single-consumer queue: classify one document at a time.

The GPU can only hold one Ollama model resident at once, so a strict FIFO
is an accepted constraint here, not an oversight. The queue is DB-backed (the
`classification_runs` table doubles as the queue) rather than in-memory, so a
restart never silently drops a waiting document - stale PROCESSING rows are
flipped back to QUEUED on startup instead, since classification is idempotent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from . import runtime_config
from .classifier import classify
from .db import engine
from .models import ClassificationRun, RunStatus
from .ollama import Ollama
from .paperless import Paperless
from .taxonomy import Taxonomy
from .writeback import apply

log = logging.getLogger("paperclass")

_wake = asyncio.Event()


def wake() -> None:
    _wake.set()


def enqueue(paperless_id: int) -> tuple[str, int]:
    """Queue `paperless_id` for real classification + write-back, unless a run
    for it is already in-flight or was just processed.

    The single source of truth for turning a Paperless document id into a
    durable QUEUED `ClassificationRun` row - shared by the Paperless webhook
    handler (new documents) and the manual "Apply" action on /test (on-demand
    reclassification of an existing document), so there is exactly one place
    that decides whether to queue, dedupe, or skip.

    Returns `(status, run_id)` where `status` is one of "queued" (a new run
    was inserted and the worker was woken), "already_queued" (a QUEUED or
    PROCESSING run for this id already exists), or "recently_processed" (a
    run for this id was created within the last 60s, regardless of outcome -
    guards against double-fired webhooks/double-clicks).
    """
    with Session(engine) as session:
        in_flight = session.exec(
            select(ClassificationRun)
            .where(ClassificationRun.paperless_id == paperless_id)
            .where(ClassificationRun.status.in_([RunStatus.QUEUED, RunStatus.PROCESSING]))
        ).first()
        if in_flight is not None:
            return "already_queued", in_flight.id

        recent_cutoff = datetime.now() - timedelta(seconds=60)
        recent_dupe = session.exec(
            select(ClassificationRun)
            .where(ClassificationRun.paperless_id == paperless_id)
            .where(ClassificationRun.created_at >= recent_cutoff)
            .order_by(ClassificationRun.id.desc())
        ).first()
        if recent_dupe is not None:
            return "recently_processed", recent_dupe.id

        run = ClassificationRun(paperless_id=paperless_id, status=RunStatus.QUEUED)
        session.add(run)
        session.commit()
        session.refresh(run)

    wake()
    return "queued", run.id


def reset_stale() -> None:
    with Session(engine) as session:
        stale = session.exec(select(ClassificationRun).where(ClassificationRun.status == RunStatus.PROCESSING)).all()
        for run in stale:
            run.status = RunStatus.QUEUED
            session.add(run)
        if stale:
            session.commit()
            log.info("reset %d stale processing run(s) back to queued", len(stale))


def _next_queued() -> int | None:
    with Session(engine) as session:
        run = session.exec(
            select(ClassificationRun).where(ClassificationRun.status == RunStatus.QUEUED).order_by(ClassificationRun.id)
        ).first()
        if run is None:
            return None
        run.status = RunStatus.PROCESSING
        session.add(run)
        session.commit()
        return run.id


async def worker_loop(paperless: Paperless, ollama: Ollama, taxonomy: Taxonomy) -> None:
    while True:
        run_id = _next_queued()
        if run_id is None:
            _wake.clear()
            await _wake.wait()
            continue
        try:
            await _process(run_id, paperless, ollama, taxonomy)
        except Exception:
            log.exception("worker: unhandled failure processing run %s", run_id)


async def _process(run_id: int, paperless: Paperless, ollama: Ollama, taxonomy: Taxonomy) -> None:
    with Session(engine) as session:
        run = session.get(ClassificationRun, run_id)
        paperless_id = run.paperless_id

        prior_run = session.exec(
            select(ClassificationRun)
            .where(ClassificationRun.paperless_id == paperless_id)
            .where(ClassificationRun.status.in_([RunStatus.CLASSIFIED, RunStatus.NEEDS_REVIEW]))
            .order_by(ClassificationRun.id.desc())
        ).first()

        if prior_run:
            prev_tags = json.loads(prior_run.applied_tag_ids or "[]")
            prev_type = prior_run.applied_document_type_id
            prev_correspondent = prior_run.applied_correspondent_id
        else:
            prev_tags = []
            prev_type = None
            prev_correspondent = None

    cfg = runtime_config.load()
    model, dpi = cfg.ollama_model, cfg.classify_dpi
    blacklist = runtime_config.correspondent_blacklist()

    start = datetime.now()
    try:
        file_bytes = await paperless.download(paperless_id)
        result = await classify(ollama, model, taxonomy, file_bytes, dpi, blacklist)
        applied_type, applied_tags, applied_correspondent, note = await apply(
            paperless, taxonomy, paperless_id, result, prev_tags, prev_type, prev_correspondent
        )
        status = {
            "classified": RunStatus.CLASSIFIED,
            "needs_review": RunStatus.NEEDS_REVIEW,
            "error": RunStatus.ERROR,
        }[result.status]

        with Session(engine) as session:
            run = session.get(ClassificationRun, run_id)
            run.status = status
            run.document_type = result.document_type
            run.tags = json.dumps(result.tags)
            run.correspondent = result.correspondent
            run.title = result.title
            run.applied_document_type_id = applied_type
            run.applied_tag_ids = json.dumps(applied_tags)
            run.applied_correspondent_id = applied_correspondent
            run.confidence = result.confidence
            run.raw_model_output = result.raw
            run.reason = note or result.reason
            run.model = model
            run.duration_s = (datetime.now() - start).total_seconds()
            run.finished_at = datetime.now()
            session.add(run)
            session.commit()
            log.info("doc %s -> type=%s status=%s (%.1fs)", paperless_id, result.document_type, status, run.duration_s)
    except Exception as exc:
        log.exception("classification failed for doc %s", paperless_id)
        with Session(engine) as session:
            run = session.get(ClassificationRun, run_id)
            run.status = RunStatus.ERROR
            run.reason = str(exc)[:300]
            run.finished_at = datetime.now()
            session.add(run)
            session.commit()
