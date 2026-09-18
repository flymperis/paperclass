"""Server-rendered admin GUI: dashboard, a read-only test-classify page, and settings.

LAN-only, no auth by design (the whole service already assumes a trusted network).
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from .. import runtime_config
from ..classifier import classify
from ..config import get_settings
from ..db import engine
from ..models import ClassificationRun, RunStatus
from ..ollama import OllamaError
from ..render import Pages
from ..worker import enqueue

log = logging.getLogger("paperclass")

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/")
async def dashboard(request: Request):
    with Session(engine) as session:
        rows = session.exec(select(ClassificationRun).order_by(ClassificationRun.id.desc()).limit(50)).all()

    runs = []
    counts = {"total": 0, "classified": 0, "needs_review": 0, "error": 0, "in_progress": 0}
    for r in rows:
        counts["total"] += 1
        if r.status == RunStatus.CLASSIFIED:
            counts["classified"] += 1
        elif r.status == RunStatus.NEEDS_REVIEW:
            counts["needs_review"] += 1
        elif r.status == RunStatus.ERROR:
            counts["error"] += 1
        else:
            counts["in_progress"] += 1
        runs.append(
            {
                "id": r.id,
                "paperless_id": r.paperless_id,
                "status": r.status,
                "document_type": r.document_type,
                "tags": json.loads(r.tags or "[]"),
                "correspondent": r.correspondent,
                "title": r.title,
                "confidence": r.confidence,
                "duration_s": r.duration_s,
                "created_at": r.created_at,
            }
        )

    return templates.TemplateResponse(
        request, "dashboard.html", {"active": "dashboard", "runs": runs, "counts": counts}
    )


@router.get("/test")
async def test_form(request: Request, paperless_id: int | None = None):
    return templates.TemplateResponse(
        request,
        "test.html",
        {"active": "test", "paperless_id": paperless_id, "result": None, "error": None, "flash": None},
    )


@router.post("/test")
async def test_run(request: Request, paperless_id: int = Form(...)):
    paperless = request.app.state.paperless
    ollama = request.app.state.ollama
    taxonomy = request.app.state.taxonomy
    cfg = runtime_config.load()

    context = {
        "active": "test",
        "paperless_id": paperless_id,
        "result": None,
        "error": None,
        "doc": None,
        "flash": None,
    }

    try:
        doc = await paperless.document(paperless_id)
        context["doc"] = doc
    except Exception as exc:  # noqa: BLE001 - any lookup failure is shown, not raised
        context["error"] = f"Could not fetch document {paperless_id} from Paperless: {exc}"
        return templates.TemplateResponse(request, "test.html", context)

    try:
        file_bytes = await paperless.download(paperless_id)
        preview = base64.b64encode(Pages(file_bytes).png(0, cfg.classify_dpi)).decode()
        context["image_data_uri"] = f"data:image/png;base64,{preview}"
        blacklist = runtime_config.correspondent_blacklist()
        result = await classify(ollama, cfg.ollama_model, taxonomy, file_bytes, cfg.classify_dpi, blacklist)
        context["result"] = result
    except Exception as exc:  # noqa: BLE001 - render/classify failure shown, never a 500
        context["error"] = f"Classification failed: {exc}"

    return templates.TemplateResponse(request, "test.html", context)


@router.post("/test/apply")
async def test_apply(request: Request, paperless_id: int = Form(...)):
    """The real write-back: enqueues `paperless_id` on the same durable FIFO
    queue the Paperless webhook uses, rather than classifying+applying it
    directly here - the GPU can only run one Ollama model at a time, so this
    must never race the background worker with a second concurrent call.
    """
    status, run_id = enqueue(paperless_id)

    messages = {
        "queued": (
            f"Queued document {paperless_id} for real classification and write-back (run #{run_id}). "
            f"Processing happens asynchronously through the classification queue, not immediately - "
            f"check the Dashboard in a few moments for the result."
        ),
        "already_queued": (
            f"Document {paperless_id} is already queued or being processed (run #{run_id}) - not queued again. "
            f"See the Dashboard for its status."
        ),
        "recently_processed": (
            f"Document {paperless_id} was already processed in the last minute (run #{run_id}) - not re-queued "
            f"to avoid a duplicate write-back. See the Dashboard for its result."
        ),
    }

    context = {
        "active": "test",
        "paperless_id": paperless_id,
        "result": None,
        "error": None,
        "doc": None,
        "flash": messages[status],
    }
    return templates.TemplateResponse(request, "test.html", context)


@router.get("/settings")
async def settings_form(request: Request):
    cfg = runtime_config.load()
    settings = get_settings()

    ollama_models, ollama_models_error = await _fetch_ollama_models(request)
    ollama_models = _ensure_current_model_present(ollama_models, cfg.ollama_model)

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "candidate_tags": "\n".join(json.loads(cfg.candidate_tags)),
            "correspondent_blacklist": "\n".join(json.loads(cfg.correspondent_blacklist)),
            "ollama_model": cfg.ollama_model,
            "ollama_models": ollama_models,
            "ollama_models_error": ollama_models_error,
            "classify_dpi": cfg.classify_dpi,
            "taxonomy_refresh_minutes": cfg.taxonomy_refresh_minutes,
            "paperless_url": settings.paperless_url,
            "ollama_url": settings.ollama_url,
            "flash": None,
            "error": None,
        },
    )


async def _fetch_ollama_models(request: Request) -> tuple[list[dict], str | None]:
    """Best-effort model list for the settings dropdown: on any failure to
    reach Ollama, fall back to an empty list (the template then falls back to
    a plain text input) rather than let the whole settings page 500."""
    try:
        return await request.app.state.ollama.list_models(), None
    except OllamaError as exc:
        log.warning("could not list Ollama models for settings page: %s", exc)
        return [], str(exc)


def _ensure_current_model_present(models: list[dict], current: str) -> list[dict]:
    """Make sure the configured model is always a selectable option, even if
    it wasn't returned by Ollama (e.g. removed from Ollama, or Ollama was
    unreachable) - so saving the form without touching the field can never
    silently switch it to something else."""
    if not models or any(m["name"] == current for m in models):
        return models
    return [*models, {"name": current, "vision": False, "missing": True}]


@router.post("/settings")
async def settings_save(
    request: Request,
    candidate_tags: str = Form(...),
    correspondent_blacklist: str = Form(...),
    ollama_model: str = Form(...),
    classify_dpi: str = Form(...),
    taxonomy_refresh_minutes: str = Form(...),
):
    settings = get_settings()
    ollama_models, ollama_models_error = await _fetch_ollama_models(request)
    context = {
        "active": "settings",
        "candidate_tags": candidate_tags,
        "correspondent_blacklist": correspondent_blacklist,
        "ollama_model": ollama_model,
        "ollama_models": _ensure_current_model_present(ollama_models, ollama_model),
        "ollama_models_error": ollama_models_error,
        "classify_dpi": classify_dpi,
        "taxonomy_refresh_minutes": taxonomy_refresh_minutes,
        "paperless_url": settings.paperless_url,
        "ollama_url": settings.ollama_url,
        "flash": None,
        "error": None,
    }

    try:
        dpi = int(classify_dpi)
        refresh_minutes = int(taxonomy_refresh_minutes)
        if dpi <= 0 or refresh_minutes <= 0:
            raise ValueError("must be positive")
    except ValueError:
        context["error"] = "classify_dpi and taxonomy_refresh_minutes must be positive whole numbers."
        return templates.TemplateResponse(request, "settings.html", context)

    tags = [line.strip() for line in candidate_tags.splitlines() if line.strip()]
    blacklist = [line.strip() for line in correspondent_blacklist.splitlines() if line.strip()]
    if not ollama_model.strip():
        context["error"] = "ollama_model must not be empty."
        return templates.TemplateResponse(request, "settings.html", context)

    runtime_config.save(
        candidate_tags=tags,
        correspondent_blacklist=blacklist,
        ollama_model=ollama_model.strip(),
        classify_dpi=dpi,
        taxonomy_refresh_minutes=refresh_minutes,
    )
    await request.app.state.taxonomy.refresh()

    context["flash"] = "Settings saved."
    context["candidate_tags"] = "\n".join(tags)
    context["correspondent_blacklist"] = "\n".join(blacklist)
    context["ollama_model"] = ollama_model.strip()
    context["classify_dpi"] = dpi
    context["taxonomy_refresh_minutes"] = refresh_minutes
    return templates.TemplateResponse(request, "settings.html", context)
