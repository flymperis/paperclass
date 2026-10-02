"""Forward classified documents to other apps, by tag.

After a document is classified, every rule in `FORWARD_WEBHOOKS` whose tag was
applied gets a small JSON POST with the document id, so the receiving app (an
electricity-bill tracker, a medical-records app, ...) can read the document
from Paperless itself. Strictly best-effort: a receiver being down never
affects classification, it is only logged - receivers should also poll
Paperless as a fallback.
"""

from __future__ import annotations

import logging

import httpx

from .classifier import ClassificationResult
from .config import ForwardWebhook, get_settings

log = logging.getLogger("paperclass")

TIMEOUT_SECONDS = 10.0


def matching_rules(applied_tags: list[str], rules: list[ForwardWebhook]) -> list[ForwardWebhook]:
    applied = {t.casefold() for t in applied_tags}
    return [r for r in rules if r.tag.casefold() in applied]


async def notify(
    paperless_id: int,
    result: ClassificationResult,
    applied_tags: list[str],
    *,
    rules: list[ForwardWebhook] | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """POST to every matching receiver. Returns one human-readable outcome per
    receiver (also logged); never raises."""
    rules = get_settings().forward_webhooks if rules is None else rules
    targets = matching_rules(applied_tags, rules)
    if not targets:
        return []
    payload = {
        "event": "classified",
        "document_id": paperless_id,
        "tags": applied_tags,
        "document_type": result.document_type,
        "correspondent": result.correspondent,
        "title": result.title,
    }
    outcomes = []
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        for rule in targets:
            headers = {"X-Webhook-Secret": rule.secret} if rule.secret else {}
            try:
                resp = await client.post(rule.url, json=payload, headers=headers)
                outcome = f"forwarded to {rule.url} ({rule.tag}): HTTP {resp.status_code}"
                (log.info if resp.is_success else log.warning)("doc %s %s", paperless_id, outcome)
            except httpx.HTTPError as exc:
                outcome = f"forward to {rule.url} ({rule.tag}) failed: {exc.__class__.__name__}: {exc}"
                log.warning("doc %s %s", paperless_id, outcome)
            outcomes.append(outcome)
    finally:
        if own_client:
            await client.aclose()
    return outcomes
