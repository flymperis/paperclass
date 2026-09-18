"""Live tag/document-type/correspondent taxonomy, fetched from Paperless and
refreshed periodically.

Candidate tags are an explicit allowlist (not "every tag Paperless has") so the
model is never offered paperless-gpt's old bookkeeping tags or anything else
irrelevant to classification. Document types are offered in full since there
are few of them and the user manages that list directly. Correspondents are
also offered in full (for fuzzy-match lookup) since, unlike tags/types, any
existing correspondent is fair game to reuse - there's no small curated list.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re

from . import runtime_config
from .paperless import Paperless

log = logging.getLogger("paperclass")

# A confident-but-not-exact match (e.g. "ΔΕΗ" vs "ΔΕΗ Α.Ε.") must clear this bar
# before we reuse an existing correspondent instead of creating a new one. Kept
# high on purpose: a false merge of two different real senders is worse than an
# occasional near-duplicate correspondent.
_MATCH_THRESHOLD = 0.86


def _normalize(name: str) -> str:
    return re.sub(r"[^\w]+", " ", name, flags=re.UNICODE).strip().casefold()


def is_blacklisted_correspondent(name: str, blacklist: list[str]) -> bool:
    """True if `name` matches (normalized, case/punctuation-insensitive) any
    blacklist entry - used to keep the document owner's own name from ever
    being written as a correspondent, even if the model ignores the prompt."""
    norm = _normalize(name)
    return bool(norm) and any(norm == _normalize(b) for b in blacklist)


class Taxonomy:
    def __init__(self, paperless: Paperless, needs_review_tag: str) -> None:
        self.paperless = paperless
        self.needs_review_tag = needs_review_tag
        self.tag_ids: dict[str, int] = {}
        self.type_ids: dict[str, int] = {}
        self.correspondent_ids: dict[str, int] = {}
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
        self.correspondent_ids = await self.paperless.correspondents()

        missing = [t for t in candidate_tags if t not in self.tag_ids]
        if missing:
            log.warning("candidate tags not found in Paperless (skipped): %s", missing)

    def match_correspondent(self, name: str) -> int | None:
        """Fuzzy-match a model-suggested correspondent name against existing
        correspondents. Returns an existing id on a confident match, else None
        (meaning: safe to create a new correspondent for this name)."""
        target = _normalize(name)
        if not target:
            return None

        best_id: int | None = None
        best_score = 0.0
        for existing_name, existing_id in self.correspondent_ids.items():
            norm = _normalize(existing_name)
            if not norm:
                continue
            if norm == target:
                return existing_id
            score = difflib.SequenceMatcher(None, norm, target).ratio()
            # One name fully containing the other (e.g. "δεη" in "δεη α ε") is a
            # strong signal of the same entity under a longer/shorter spelling -
            # but only once both sides are long enough that this isn't a fluke.
            if len(norm) >= 3 and len(target) >= 3 and (norm in target or target in norm):
                score = max(score, 0.9)
            if score > best_score:
                best_score = score
                best_id = existing_id

        return best_id if best_score >= _MATCH_THRESHOLD else None

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "document_type": {"type": "string", "enum": sorted(self.type_ids)},
                "document_type_confidence": {"type": "string", "enum": ["high", "low"]},
                "tags": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": sorted(self.tag_ids)}},
                "tags_confidence": {"type": "string", "enum": ["high", "low"]},
                "correspondent": {"type": "string"},
                "correspondent_confidence": {"type": "string", "enum": ["high", "low"]},
                "title": {"type": "string"},
                "title_confidence": {"type": "string", "enum": ["high", "low"]},
            },
            "required": [
                "document_type",
                "document_type_confidence",
                "tags",
                "tags_confidence",
                "correspondent",
                "correspondent_confidence",
                "title",
                "title_confidence",
            ],
        }

    async def refresh_loop(self) -> None:
        while True:
            minutes = runtime_config.load().taxonomy_refresh_minutes
            await asyncio.sleep(max(minutes, 1) * 60)
            try:
                await self.refresh()
            except Exception:
                log.exception("taxonomy refresh failed")
