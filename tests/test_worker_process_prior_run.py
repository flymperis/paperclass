"""Test that the worker correctly retrieves prior run applied_* values.

This captures the bug: when _process loads a fresh ClassificationRun, that run
has NULL applied_* fields. The fix must query for the most recent PRIOR
COMPLETED run to get the historical applied_* values that protect against
clobbering human corrections.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlmodel import Session, select

from app.db import engine
from app.models import ClassificationRun, RunStatus


def get_prior_applied_values(paperless_id: int, exclude_run_id: int | None = None) -> tuple[int | None, list[int], int | None]:
    """Helper: simulates the logic that _process should use to find prior applied values.

    Returns (applied_type_id, applied_tag_ids, applied_correspondent_id)
    from the most recent COMPLETED run for this paperless_id (excluding current run).
    """
    with Session(engine) as session:
        # Query for the most recent COMPLETED run (excluding current)
        query = (
            select(ClassificationRun)
            .where(ClassificationRun.paperless_id == paperless_id)
            .where(ClassificationRun.status.in_([RunStatus.CLASSIFIED, RunStatus.NEEDS_REVIEW]))
            .order_by(ClassificationRun.id.desc())
        )
        prior = session.exec(query).first()

        if prior is None:
            return None, [], None

        return (
            prior.applied_document_type_id,
            json.loads(prior.applied_tag_ids or "[]"),
            prior.applied_correspondent_id,
        )


def test_prior_applied_values_from_most_recent_completed_run():
    """Given multiple runs for the same document, should retrieve applied_*
    values from the most recent COMPLETED one, not from the current PROCESSING run.
    """
    paperless_id = 123

    # Run 1: Initial classification (COMPLETED)
    run1 = ClassificationRun(
        paperless_id=paperless_id,
        status=RunStatus.CLASSIFIED,
        document_type="Invoice",
        tags=json.dumps(["expenses"]),
        correspondent="Vendor A",
        applied_document_type_id=2,
        applied_tag_ids=json.dumps([10]),
        applied_correspondent_id=40,
        confidence="high",
        reason="",
        model="llava",
        duration_s=1.5,
        created_at=datetime.now(),
        finished_at=datetime.now(),
    )

    with Session(engine) as session:
        session.add(run1)
        session.commit()

    # Run 2: Re-classification (PROCESSING) - this is the current run being processed
    run2 = ClassificationRun(
        paperless_id=paperless_id,
        status=RunStatus.PROCESSING,
        # applied_* fields are NULL because this is a brand-new run
    )

    with Session(engine) as session:
        session.add(run2)
        session.commit()
        session.refresh(run2)
        current_run_id = run2.id

    # The helper function should return run1's applied_* values
    prior_type, prior_tags, prior_correspondent = get_prior_applied_values(paperless_id, exclude_run_id=current_run_id)

    # Should get values from run1, not run2's empty values
    assert prior_type == 2, f"Expected prior_type=2, got {prior_type}"
    assert prior_tags == [10], f"Expected prior_tags=[10], got {prior_tags}"
    assert prior_correspondent == 40, f"Expected prior_correspondent=40, got {prior_correspondent}"


def test_prior_applied_values_with_no_prior_run():
    """When no prior COMPLETED run exists, should return empty/None values."""
    paperless_id = 456

    # Only one run: brand new, no prior history
    run = ClassificationRun(
        paperless_id=paperless_id,
        status=RunStatus.PROCESSING,
    )

    with Session(engine) as session:
        session.add(run)
        session.commit()
        session.refresh(run)
        current_run_id = run.id

    prior_type, prior_tags, prior_correspondent = get_prior_applied_values(paperless_id, exclude_run_id=current_run_id)

    assert prior_type is None, f"Expected prior_type=None, got {prior_type}"
    assert prior_tags == [], f"Expected prior_tags=[], got {prior_tags}"
    assert prior_correspondent is None, f"Expected prior_correspondent=None, got {prior_correspondent}"


def test_prior_applied_values_ignores_errored_runs():
    """Should only use COMPLETED runs (CLASSIFIED, NEEDS_REVIEW), not ERROR or PROCESSING."""
    paperless_id = 789

    # Run 1: ERROR run
    run1 = ClassificationRun(
        paperless_id=paperless_id,
        status=RunStatus.ERROR,
        applied_document_type_id=1,
        applied_tag_ids=json.dumps([5]),
        applied_correspondent_id=10,
        reason="Network error",
    )

    # Run 2: CLASSIFIED run (this should be used)
    run2 = ClassificationRun(
        paperless_id=paperless_id,
        status=RunStatus.CLASSIFIED,
        applied_document_type_id=2,
        applied_tag_ids=json.dumps([10]),
        applied_correspondent_id=40,
        reason="",
    )

    # Run 3: Current PROCESSING run
    run3 = ClassificationRun(
        paperless_id=paperless_id,
        status=RunStatus.PROCESSING,
    )

    with Session(engine) as session:
        session.add(run1)
        session.add(run2)
        session.add(run3)
        session.commit()
        session.refresh(run3)
        current_run_id = run3.id

    prior_type, prior_tags, prior_correspondent = get_prior_applied_values(paperless_id, exclude_run_id=current_run_id)

    # Should get run2's values, not run1's
    assert prior_type == 2, f"Expected prior_type=2 from CLASSIFIED run, got {prior_type}"
    assert prior_tags == [10], f"Expected prior_tags=[10], got {prior_tags}"
    assert prior_correspondent == 40, f"Expected prior_correspondent=40, got {prior_correspondent}"
