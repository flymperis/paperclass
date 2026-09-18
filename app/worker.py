"""Single-consumer queue: classify one document at a time.

The GPU can only hold one Ollama model resident at once, so a strict FIFO
matches vibehealth's own accepted constraint. The queue is DB-backed (the
`classification_runs` table doubles as the queue) rather than in-memory, so a
restart never silently drops a waiting document - stale PROCESSING rows are
flipped back to QUEUED on startup instead, since classification is idempotent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

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
        prev_tags = json.loads(run.applied_tag_ids or "[]")
        prev_type = run.applied_document_type_id

    cfg = runtime_config.load()
    model, dpi = cfg.ollama_model, cfg.classify_dpi

    start = datetime.now()
    try:
        file_bytes = await paperless.download(paperless_id)
        result = await classify(ollama, model, taxonomy, file_bytes, dpi)
        applied_type, applied_tags, note = await apply(paperless, taxonomy, paperless_id, result, prev_tags, prev_type)
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
            run.applied_document_type_id = applied_type
            run.applied_tag_ids = json.dumps(applied_tags)
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
