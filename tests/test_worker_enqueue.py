"""Unit tests for `app.worker.enqueue` - the single shared implementation of
"queue a Paperless document id for real classification + write-back" used by
both the Paperless webhook and the manual /test/apply action.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, select

from app import worker
from app.db import engine
from app.models import ClassificationRun, RunStatus


def test_enqueue_creates_a_queued_run_and_wakes_the_worker():
    worker._wake.clear()

    status, run_id = worker.enqueue(101)

    assert status == "queued"
    with Session(engine) as session:
        run = session.get(ClassificationRun, run_id)
        assert run is not None
        assert run.paperless_id == 101
        assert run.status == RunStatus.QUEUED
    assert worker._wake.is_set()


def test_enqueue_is_idempotent_while_a_run_is_queued():
    status1, run_id1 = worker.enqueue(202)
    status2, run_id2 = worker.enqueue(202)

    assert status1 == "queued"
    assert status2 == "already_queued"
    assert run_id2 == run_id1

    with Session(engine) as session:
        count = len(
            session.exec(select(ClassificationRun).where(ClassificationRun.paperless_id == 202)).all()
        )
    assert count == 1  # the double-click / double-fired-webhook case never inserts a second row


def test_enqueue_is_idempotent_while_a_run_is_processing():
    with Session(engine) as session:
        run = ClassificationRun(paperless_id=303, status=RunStatus.PROCESSING)
        session.add(run)
        session.commit()
        session.refresh(run)
        processing_id = run.id

    status, run_id = worker.enqueue(303)

    assert status == "already_queued"
    assert run_id == processing_id


def test_enqueue_skips_a_recently_finished_run():
    with Session(engine) as session:
        run = ClassificationRun(paperless_id=404, status=RunStatus.CLASSIFIED)
        session.add(run)
        session.commit()
        session.refresh(run)
        finished_id = run.id

    status, run_id = worker.enqueue(404)

    assert status == "recently_processed"
    assert run_id == finished_id
    with Session(engine) as session:
        count = len(
            session.exec(select(ClassificationRun).where(ClassificationRun.paperless_id == 404)).all()
        )
    assert count == 1  # no new row queued on top of the recent one


def test_enqueue_requeues_once_the_recent_dupe_window_has_passed():
    old_cutoff = datetime.now() - timedelta(seconds=120)
    with Session(engine) as session:
        run = ClassificationRun(paperless_id=505, status=RunStatus.CLASSIFIED, created_at=old_cutoff)
        session.add(run)
        session.commit()
        session.refresh(run)
        old_run_id = run.id

    status, run_id = worker.enqueue(505)

    assert status == "queued"
    assert run_id != old_run_id
    with Session(engine) as session:
        new_run = session.get(ClassificationRun, run_id)
        assert new_run.status == RunStatus.QUEUED
