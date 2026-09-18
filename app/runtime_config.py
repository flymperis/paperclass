"""The live-editable subset of settings, backed by SQLite (see the Settings page).

Seeded from the env-based Settings on first run, then editable at runtime
without a restart. Read fresh on every use - it's one cheap indexed row, and
staying fresh is worth more here than caching it.
"""

from __future__ import annotations

import json

from sqlmodel import Session

from .config import get_settings
from .db import engine
from .models import RuntimeConfig


def load() -> RuntimeConfig:
    with Session(engine) as session:
        cfg = session.get(RuntimeConfig, 1)
        if cfg is None:
            settings = get_settings()
            cfg = RuntimeConfig(
                id=1,
                candidate_tags=json.dumps(settings.candidate_tags),
                correspondent_blacklist=json.dumps(settings.correspondent_blacklist),
                ollama_model=settings.ollama_model,
                classify_dpi=settings.classify_dpi,
                taxonomy_refresh_minutes=settings.taxonomy_refresh_minutes,
            )
            session.add(cfg)
            session.commit()
            session.refresh(cfg)
        elif cfg.correspondent_blacklist is None:
            # column added by a later migration to a row that already existed -
            # backfill once from the env default rather than leaving it null.
            cfg.correspondent_blacklist = json.dumps(get_settings().correspondent_blacklist)
            session.add(cfg)
            session.commit()
            session.refresh(cfg)
        return cfg


def candidate_tags() -> list[str]:
    return json.loads(load().candidate_tags)


def correspondent_blacklist() -> list[str]:
    return json.loads(load().correspondent_blacklist)


def save(
    *,
    candidate_tags: list[str],
    ollama_model: str,
    classify_dpi: int,
    taxonomy_refresh_minutes: int,
    correspondent_blacklist: list[str],
) -> None:
    with Session(engine) as session:
        cfg = session.get(RuntimeConfig, 1) or RuntimeConfig(id=1)
        cfg.candidate_tags = json.dumps(candidate_tags)
        cfg.correspondent_blacklist = json.dumps(correspondent_blacklist)
        cfg.ollama_model = ollama_model
        cfg.classify_dpi = classify_dpi
        cfg.taxonomy_refresh_minutes = taxonomy_refresh_minutes
        session.add(cfg)
        session.commit()
