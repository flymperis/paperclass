"""paperclass: standalone Paperless-ngx document classifier (document_type + tags)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import worker
from .config import get_settings
from .db import init_db
from .ollama import Ollama
from .paperless import Paperless
from .routers import system, ui, webhook
from .taxonomy import Taxonomy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("paperclass")

_background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    worker.reset_stale()

    paperless = Paperless(settings.paperless_url, settings.paperless_token)
    ollama = Ollama(
        settings.ollama_url, settings.ollama_timeout_seconds, settings.ollama_num_ctx, settings.ollama_keep_alive
    )
    taxonomy = Taxonomy(paperless, settings.needs_review_tag)
    await taxonomy.refresh()
    log.info(
        "taxonomy loaded: %d document types, %d candidate tags found",
        len(taxonomy.type_ids),
        len(taxonomy.tag_ids),
    )

    app.state.paperless = paperless
    app.state.ollama = ollama
    app.state.taxonomy = taxonomy

    _background_tasks.append(asyncio.create_task(taxonomy.refresh_loop()))
    _background_tasks.append(asyncio.create_task(worker.worker_loop(paperless, ollama, taxonomy)))
    worker.wake()

    yield

    for task in _background_tasks:
        task.cancel()


app = FastAPI(title="paperclass", lifespan=lifespan)
app.include_router(webhook.router)
app.include_router(system.router)
app.include_router(ui.router)
