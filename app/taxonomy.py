"""Live tag/document-type taxonomy, fetched from Paperless and refreshed periodically.

Candidate tags are an explicit allowlist (not "every tag Paperless has") so the
model is never offered paperless-gpt's old bookkeeping tags or anything else
irrelevant to classification. Document types are offered in full since there
are few of them and the user manages that list directly.
"""

from __future__ import annotations

import asyncio
import logging

from . import runtime_config
from .paperless import Paperless

log = logging.getLogger("paperclass")


class Taxonomy:
    def __init__(self, paperless: Paperless, needs_review_tag: str) -> None:
        self.paperless = paperless
        self.needs_review_tag = needs_review_tag
        self.tag_ids: dict[str, int] = {}
        self.type_ids: dict[str, int] = {}
        self.needs_review_tag_id: int | None = None

    async def refresh(self) -> None:
        candidate_tags = runtime_config.candidate_tags()

        tags = await self.paperless.tags()
        if self.needs_review_tag not in tags:
            new_id = await self.paperless.create_tag(self.needs_review_tag)
            tags[self.needs_review_tag] = new_id
            log.info("created missing tag %r (id=%s)", self.needs_review_tag, new_id)
        self.needs_review_tag_id = tags[self.needs_review_tag]

        self.type_ids = await self.paperless.document_types()
        self.tag_ids = {name: tid for name, tid in tags.items() if name in candidate_tags}

        missing = [t for t in candidate_tags if t not in self.tag_ids]
        if missing:
            log.warning("candidate tags not found in Paperless (skipped): %s", missing)

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "document_type": {"type": "string", "enum": sorted(self.type_ids)},
                "document_type_confidence": {"type": "string", "enum": ["high", "low"]},
                "tags": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": sorted(self.tag_ids)}},
                "tags_confidence": {"type": "string", "enum": ["high", "low"]},
            },
            "required": ["document_type", "document_type_confidence", "tags", "tags_confidence"],
        }

    async def refresh_loop(self) -> None:
        while True:
            minutes = runtime_config.load().taxonomy_refresh_minutes
            await asyncio.sleep(max(minutes, 1) * 60)
            try:
                await self.refresh()
            except Exception:
                log.exception("taxonomy refresh failed")
