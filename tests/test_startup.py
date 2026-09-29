"""Startup must survive Paperless not being up yet (e.g. right after a host
reboot) instead of crashing and tripping systemd's restart limit."""

from __future__ import annotations

import pytest

from app import main


class _FlakyTaxonomy:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def refresh(self) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("Could not resolve hostname: paperless-webserver")


@pytest.mark.asyncio
async def test_initial_refresh_retries_until_paperless_answers(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    taxonomy = _FlakyTaxonomy(failures=5)

    await main._initial_refresh(taxonomy, max_delay=10)

    assert taxonomy.calls == 6
    assert sleeps == [2, 4, 8, 10, 10]
