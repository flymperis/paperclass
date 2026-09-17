"""Server-rendered admin GUI: dashboard, a read-only test-classify page, and settings.

LAN-only, no auth by design (the whole service already assumes a trusted network).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from .. import runtime_config
from ..classifier import classify
from ..config import get_settings
from ..db import engine
from ..models import ClassificationRun, RunStatus
from ..render import Pages

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
        request, "test.html", {"active": "test", "paperless_id": paperless_id, "result": None, "error": None}
    )


@router.post("/test")
async def test_run(request: Request, paperless_id: int = Form(...)):
    paperless = request.app.state.paperless
    ollama = request.app.state.ollama
    taxonomy = request.app.state.taxonomy
    cfg = runtime_config.load()

    context = {"active": "test", "paperless_id": paperless_id, "result": None, "error": None, "doc": None}

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
        result = await classify(ollama, cfg.ollama_model, taxonomy, file_bytes, cfg.classify_dpi)
        context["result"] = result
    except Exception as exc:  # noqa: BLE001 - render/classify failure shown, never a 500
        context["error"] = f"Classification failed: {exc}"

    return templates.TemplateResponse(request, "test.html", context)


@router.get("/settings")
async def settings_form(request: Request):
    cfg = runtime_config.load()
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "candidate_tags": "\n".join(json.loads(cfg.candidate_tags)),
            "ollama_model": cfg.ollama_model,
            "classify_dpi": cfg.classify_dpi,
            "taxonomy_refresh_minutes": cfg.taxonomy_refresh_minutes,
            "paperless_url": settings.paperless_url,
            "ollama_url": settings.ollama_url,
            "flash": None,
            "error": None,
        },
    )


@router.post("/settings")
async def settings_save(
    request: Request,
    candidate_tags: str = Form(...),
    ollama_model: str = Form(...),
    classify_dpi: str = Form(...),
    taxonomy_refresh_minutes: str = Form(...),
):
    settings = get_settings()
    context = {
        "active": "settings",
        "candidate_tags": candidate_tags,
        "ollama_model": ollama_model,
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
    if not ollama_model.strip():
        context["error"] = "ollama_model must not be empty."
        return templates.TemplateResponse(request, "settings.html", context)

    runtime_config.save(
        candidate_tags=tags, ollama_model=ollama_model.strip(), classify_dpi=dpi, taxonomy_refresh_minutes=refresh_minutes
    )
    await request.app.state.taxonomy.refresh()

    context["flash"] = "Settings saved."
    context["candidate_tags"] = "\n".join(tags)
    context["ollama_model"] = ollama_model.strip()
    context["classify_dpi"] = dpi
    context["taxonomy_refresh_minutes"] = refresh_minutes
    return templates.TemplateResponse(request, "settings.html", context)
