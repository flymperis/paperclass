"""The audit trail / durable queue: one row per classification attempt."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class RunStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    CLASSIFIED = "classified"
    NEEDS_REVIEW = "needs_review"
    ERROR = "error"


class ClassificationRun(SQLModel, table=True):
    __tablename__ = "classification_runs"

    id: int | None = Field(default=None, primary_key=True)
    paperless_id: int = Field(index=True)
    status: RunStatus = Field(default=RunStatus.QUEUED)
    document_type: str | None = None
    tags: str = "[]"  # JSON list of tag names the model chose
    applied_document_type_id: int | None = None
    applied_tag_ids: str = "[]"  # JSON list of ids we actually PATCHed
    confidence: str = ""
    raw_model_output: str = ""
    reason: str = ""  # error text, or "skipped: manually corrected"
    model: str = ""
    duration_s: float | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = None


class RuntimeConfig(SQLModel, table=True):
    """The handful of settings editable from the Settings page, without a restart.

    Single row (id=1). Connection details (Paperless/Ollama URLs, tokens) stay
    in the env file - those gate what the whole app can even reach, so a
    restart is fine for them. This table is only for day-to-day tuning knobs.
    """

    __tablename__ = "runtime_config"

    id: int = Field(default=1, primary_key=True)
    candidate_tags: str = "[]"  # JSON list of tag names
    ollama_model: str = ""
    classify_dpi: int = 0
    taxonomy_refresh_minutes: int = 0
