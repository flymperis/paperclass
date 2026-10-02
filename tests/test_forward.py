"""Forwarding classified documents to other apps by tag (FORWARD_WEBHOOKS)."""

from __future__ import annotations

import json

import httpx
import pytest

from app import forward
from app.classifier import ClassificationResult
from app.config import ForwardWebhook, Settings

RULES = [
    ForwardWebhook(tag="Electricity", url="http://bills.invalid/api/webhook", secret="s3cret"),
    ForwardWebhook(tag="Medical Report", url="http://health.invalid/hook"),
]


def _result(tags):
    return ClassificationResult(
        status="classified", document_type="Bill", tags=tags, correspondent="ΔΕΗ", title="Electricity bill",
        confidence="high", reason="", raw="{}",
    )


def test_matching_is_by_applied_tag_case_insensitive():
    assert [r.url for r in forward.matching_rules(["electricity"], RULES)] == ["http://bills.invalid/api/webhook"]
    assert forward.matching_rules(["Water"], RULES) == []
    assert len(forward.matching_rules(["Electricity", "Medical Report"], RULES)) == 2


def test_rules_parse_from_env_json(monkeypatch):
    monkeypatch.setenv("FORWARD_WEBHOOKS", '[{"tag": "Electricity", "url": "http://x/api/webhook"}]')
    s = Settings()
    assert s.forward_webhooks == [ForwardWebhook(tag="Electricity", url="http://x/api/webhook", secret="")]


@pytest.mark.asyncio
async def test_notify_posts_document_id_and_secret():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("x-webhook-secret"), json.loads(request.content)))
        return httpx.Response(200, json={"queued": 42})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await forward.notify(42, _result(["Electricity"]), ["Electricity"], rules=RULES, client=client)

    assert len(seen) == 1
    url, secret, body = seen[0]
    assert url == "http://bills.invalid/api/webhook" and secret == "s3cret"
    assert body == {"event": "classified", "document_id": 42, "tags": ["Electricity"], "document_type": "Bill",
                    "correspondent": "ΔΕΗ", "title": "Electricity bill"}
    assert "HTTP 200" in out[0]


@pytest.mark.asyncio
async def test_notify_never_raises_when_receiver_is_down():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await forward.notify(7, _result(["Medical Report"]), ["Medical Report"], rules=RULES, client=client)
    assert "failed" in out[0] and "ConnectError" in out[0]


@pytest.mark.asyncio
async def test_no_matching_rule_sends_nothing():
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("unexpected request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await forward.notify(1, _result(["Water"]), ["Water"], rules=RULES, client=client) == []
