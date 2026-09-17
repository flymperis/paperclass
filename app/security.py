"""Shared-secret auth for the inbound webhook and the debug/audit endpoint."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from .config import get_settings


async def verify_webhook_secret(x_paperclass_secret: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.webhook_secret:
        return  # no secret configured: allow (local/dev use)
    if not x_paperclass_secret or not hmac.compare_digest(x_paperclass_secret, settings.webhook_secret):
        raise HTTPException(status_code=401, detail="invalid webhook secret")
