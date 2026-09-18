"""SQLite engine setup — doubles as the durable classification queue."""

from __future__ import annotations

import logging
import os

from sqlalchemy import event, inspect, text
from sqlmodel import SQLModel, create_engine

from .config import get_settings

log = logging.getLogger("paperclass")

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
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all only creates missing *tables*, not missing columns on a table
    that already has rows - a bare ALTER TABLE ADD COLUMN here lets the schema
    evolve without pulling in a full migration tool for one small SQLite file."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type}"))
                    log.info("added column %s.%s", table.name, column.name)
