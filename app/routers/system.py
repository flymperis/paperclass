"""Health check and audit endpoints. No UI - inspect via curl or sqlite3 directly."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import engine
from ..models import ClassificationRun
from ..security import verify_webhook_secret

router = APIRouter()


@router.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/api/runs", dependencies=[Depends(verify_webhook_secret)])
async def runs(limit: int = 50) -> list[dict]:
    with Session(engine) as session:
        rows = session.exec(select(ClassificationRun).order_by(ClassificationRun.id.desc()).limit(limit)).all()
        return [r.model_dump(mode="json") for r in rows]
