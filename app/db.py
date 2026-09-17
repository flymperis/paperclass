"""SQLite engine setup — doubles as the durable classification queue."""

from __future__ import annotations

import os

from sqlalchemy import event
from sqlmodel import SQLModel, create_engine

from .config import get_settings

settings = get_settings()
os.makedirs(settings.data_dir, exist_ok=True)
engine = create_engine(f"sqlite:///{settings.database_path}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers the table with SQLModel.metadata)

    SQLModel.metadata.create_all(engine)
