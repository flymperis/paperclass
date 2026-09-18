"""Receives Paperless Workflow webhook calls and enqueues a classification run.

Paperless's Webhook action has no clean document-id placeholder - only
`{{ doc_url }}`, which we parse an id out of - so this endpoint accepts
either a `paperless_id` field directly (handy for our own manual testing) or
a `doc_url`/`document_url`/`url` field to regex the id from. It also accepts
either JSON or form-encoded bodies, and logs the raw payload when
LOG_RAW_WEBHOOKS=true, since the exact shape Paperless sends should be
verified empirically rather than assumed.

Confirmed empirically against a real Paperless v3.1.3 Workflow webhook action
with `as_json: true`: the request body is a JSON-encoded STRING containing
the templated JSON text (i.e. double-encoded), not a plain JSON object - so
a str result from the first `json.loads()` gets parsed a second time.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from ..config import get_settings
from ..db import engine
from ..models import ClassificationRun, RunStatus
from ..security import verify_webhook_secret
from ..worker import wake

log = logging.getLogger("paperclass")
router = APIRouter()

DOC_ID_RE = re.compile(r"/documents/(\d+)/")


def _extract_paperless_id(body: dict) -> int | None:
    if "paperless_id" in body:
        try:
            return int(body["paperless_id"])
        except (TypeError, ValueError):
            pass
    for key in ("doc_url", "document_url", "url"):
        value = body.get(key)
        if isinstance(value, str):
            m = DOC_ID_RE.search(value)
            if m:
                return int(m.group(1))
    return None


def _parse_body(raw: bytes) -> dict | None:
    """Returns a dict, or None if `raw` isn't JSON at all (caller falls back to form data)."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if isinstance(parsed, str):
        # Paperless's `as_json` webhook body option double-encodes: the body
        # is a JSON string literal containing the templated JSON text.
        try:
            parsed = json.loads(parsed)
        except ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


@router.post("/webhook/classify", dependencies=[Depends(verify_webhook_secret)])
async def webhook_classify(request: Request) -> dict:
    raw = await request.body()
    body: dict = {}
    if raw:
        parsed = _parse_body(raw)
        if parsed is not None:
            body = parsed
        else:
            form = await request.form()
            body = dict(form)

    if get_settings().log_raw_webhooks:
        log.info("raw webhook headers=%s body=%s", dict(request.headers), raw[:2000])

    paperless_id = _extract_paperless_id(body)
    if paperless_id is None:
        log.warning("webhook: could not extract a document id from body=%s", body)
        return {"status": "ignored", "reason": "no document id found"}

    with Session(engine) as session:
        in_flight = session.exec(
            select(ClassificationRun)
            .where(ClassificationRun.paperless_id == paperless_id)
            .where(ClassificationRun.status.in_([RunStatus.QUEUED, RunStatus.PROCESSING]))
        ).first()
        if in_flight is not None:
            return {"status": "already_queued", "paperless_id": paperless_id, "run_id": in_flight.id}

        recent_cutoff = datetime.now() - timedelta(seconds=60)
        recent_dupe = session.exec(
            select(ClassificationRun)
            .where(ClassificationRun.paperless_id == paperless_id)
            .where(ClassificationRun.created_at >= recent_cutoff)
            .order_by(ClassificationRun.id.desc())
        ).first()
        if recent_dupe is not None:
            return {"status": "recently_processed", "paperless_id": paperless_id, "run_id": recent_dupe.id}

        run = ClassificationRun(paperless_id=paperless_id, status=RunStatus.QUEUED)
        session.add(run)
        session.commit()
        session.refresh(run)

    wake()
    return {"status": "queued", "paperless_id": paperless_id, "run_id": run.id}
