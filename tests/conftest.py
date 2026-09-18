"""Test setup: forces a throwaway SQLite DB and fake settings so tests never
touch the real `.env` credentials, the real Paperless/Ollama instances, or the
real `data/paperclass.db` file.

This must configure the environment *before* any `app.*` module is imported:
`app.config.get_settings()` is `lru_cache`d and `app.db` builds its SQLAlchemy
engine from it at import time, so anything imported later would otherwise
pick up the real repo-root `.env` (which points at the real, production
Paperless/Ollama instances).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_test_data_dir = tempfile.mkdtemp(prefix="paperclass-test-")
os.environ["DATA_DIR"] = _test_data_dir
# Point at a file that doesn't exist, so pydantic-settings never loads the
# repo's real .env (which has real paperless/ollama connection details).
os.environ["PAPERCLASS_ENV_FILE"] = str(Path(_test_data_dir) / "unused.env")
os.environ.setdefault("PAPERLESS_URL", "http://paperless.invalid")
os.environ.setdefault("PAPERLESS_TOKEN", "test-token")
os.environ.setdefault("OLLAMA_URL", "http://ollama.invalid")
os.environ.setdefault("WEBHOOK_SECRET", "")

import pytest  # noqa: E402
from sqlmodel import Session, delete  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import ClassificationRun  # noqa: E402

init_db()


@pytest.fixture(autouse=True)
def _clean_classification_runs():
    """Every test starts and ends with an empty queue table, so the
    in-flight/recent-dupe checks in `enqueue()` don't leak between tests."""
    with Session(engine) as session:
        session.exec(delete(ClassificationRun))
        session.commit()
    yield
    with Session(engine) as session:
        session.exec(delete(ClassificationRun))
        session.commit()
