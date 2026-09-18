"""Integration test for the Paperless webhook endpoint against a stubbed app
(no real network calls) - verifies it enqueues via the shared `worker.enqueue`
and that double-fired webhooks (Paperless/Celery retries) don't double-queue.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.models import ClassificationRun, RunStatus
from app.routers import webhook

app = FastAPI()
app.include_router(webhook.router)
client = TestClient(app)


def test_webhook_with_paperless_id_field_queues_a_run():
    resp = client.post("/webhook/classify", json={"paperless_id": 111})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["paperless_id"] == 111

    with Session(engine) as session:
        run = session.get(ClassificationRun, body["run_id"])
        assert run is not None
        assert run.status == RunStatus.QUEUED


def test_webhook_extracts_id_from_doc_url():
    resp = client.post("/webhook/classify", json={"doc_url": "https://paperless.example/documents/222/details"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["paperless_id"] == 222


def test_webhook_double_fire_does_not_double_queue():
    first = client.post("/webhook/classify", json={"paperless_id": 333})
    second = client.post("/webhook/classify", json={"paperless_id": 333})

    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "already_queued"
    assert second.json()["run_id"] == first.json()["run_id"]

    with Session(engine) as session:
        runs = session.exec(select(ClassificationRun).where(ClassificationRun.paperless_id == 333)).all()
    assert len(runs) == 1


def test_webhook_double_encoded_json_body_is_parsed():
    # Confirmed empirically: Paperless's `as_json` webhook option sends the
    # body as a JSON string literal containing the templated JSON text.
    double_encoded = '{"paperless_id": 444}'
    resp = client.post(
        "/webhook/classify",
        content=('"' + double_encoded.replace('"', '\\"') + '"').encode(),
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert resp.json()["paperless_id"] == 444


def test_webhook_with_no_extractable_id_is_ignored():
    resp = client.post("/webhook/classify", json={"something": "else"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "reason": "no document id found"}
