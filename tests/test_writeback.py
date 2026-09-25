"""Unit tests for `looks_generic_title()`: which titles are safe for
paperclass to overwrite (auto-generated) vs. a human-written title we must
never touch."""

from __future__ import annotations

import pytest

from app.writeback import looks_generic_title


@pytest.mark.parametrize(
    "title",
    [
        "downloaded",
        "Downloaded",
        "download (3)",
        "downloads",
        "download_2",
        "new document",
        "new_document",
        "New Documents",
        "file",
        "attachment",
        "Scan_2026-01-01",
        "Scan 2026-09-25 18.18.10",
        "Scan_2026-09-25_18-18-10",
        "2026-09-25 18:18:10",
        "IMG_1234",
        "Untitled",
        "",
        "   ",
    ],
)
def test_generic_titles_are_detected(title):
    assert looks_generic_title(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Downloaded Invoice from Amazon",
        "My Filed Documents",
        "Attachment from John Regarding Taxes",
        "Electricity Bill - March 2026",
    ],
)
def test_real_titles_are_not_flagged(title):
    assert looks_generic_title(title) is False
