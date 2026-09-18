"""Integration tests for the new POST /test/apply route: the real write-back
action on the /test page. Verifies it enqueues through the same shared
`worker.enqueue` the webhook uses (so a double-click can't double-queue), and
that it never calls `classify`/`writeback.apply` directly itself - it must
only ever go through the durable queue, since the GPU can hold one Ollama
model at a time and the background worker already owns that queue.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.models import ClassificationRun, RunStatus
from app.routers import ui

app = FastAPI()
app.include_router(ui.router)
client = TestClient(app)


def test_apply_queues_a_run_and_confirms_on_the_page():
    resp = client.post("/test/apply", data={"paperless_id": 555})

    assert resp.status_code == 200
    assert "555" in resp.text
    assert "Dashboard" in resp.text  # points the user at where the async result will show up

    with Session(engine) as session:
        run = session.exec(select(ClassificationRun).where(ClassificationRun.paperless_id == 555)).first()
    assert run is not None
    assert run.status == RunStatus.QUEUED


def test_apply_double_click_does_not_double_queue():
    first = client.post("/test/apply", data={"paperless_id": 666})
    second = client.post("/test/apply", data={"paperless_id": 666})

    assert first.status_code == 200
    assert second.status_code == 200
    assert "already" in second.text.lower()

    with Session(engine) as session:
        runs = session.exec(select(ClassificationRun).where(ClassificationRun.paperless_id == 666)).all()
    assert len(runs) == 1


def test_apply_and_webhook_share_the_same_in_flight_check():
    """An /apply click for a document that's already queued via the webhook
    path must not queue a second, concurrent run for the same document."""
    from app import worker

    status, run_id = worker.enqueue(777)
    assert status == "queued"

    resp = client.post("/test/apply", data={"paperless_id": 777})
    assert resp.status_code == 200
    assert "already" in resp.text.lower()

    with Session(engine) as session:
        runs = session.exec(select(ClassificationRun).where(ClassificationRun.paperless_id == 777)).all()
    assert len(runs) == 1
    assert runs[0].id == run_id
