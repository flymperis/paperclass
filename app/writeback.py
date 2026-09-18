"""Idempotent write-back of classification results to Paperless.

Re-runs (Paperless's own Celery retries can double-fire a webhook) must not
corrupt state: we only ever remove tags *we* previously applied to a document,
never tags a human added independently, and we only touch document_type or
correspondent when it's unset or still equal to what we last set ourselves (a
differing live value means a human corrected it - leave it alone, same intent
as paperless-gpt's PRESERVE_EXISTING_METADATA). Title is only ever touched
when the *current* title still looks auto-generated - once it's been set (by
a human, or by us with confidence) it no longer looks generic, so later runs
naturally leave it alone without needing to track a separate "applied title".
"""

from __future__ import annotations

import re

from . import runtime_config
from .classifier import ClassificationResult
from .paperless import Paperless
from .taxonomy import Taxonomy, is_blacklisted_correspondent

# Scanner/app defaults and bare filenames: "Scan_2026-01-01", "IMG_1234", a bare
# timestamp, a bare UUID, or similar - never a title a human bothered to write.
_GENERIC_TITLE_RE = re.compile(
    r"""^(
        scan[\s_-]*\d*([\s_-]*\d{4}[-_]\d{2}[-_]\d{2})? |
        img[\s_-]*\d+ |
        (image|photo|document|doc|untitled|scanned[\s_-]*document|file|attachment)([\s_-]*\(?\d+\)?)? |
        download(ed|s)?([\s_-]*\(?\d+\)?)? |
        new[\s_-]*document(s)?([\s_-]*\(?\d+\)?)? |
        \d{4}[-_]\d{2}[-_]\d{2}([\s_t]\d{2}[-_:]\d{2}(:\d{2})?)? |
        [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


def looks_generic_title(title: str) -> bool:
    """True when `title` looks empty or auto-generated (a raw filename, a
    scanner-default pattern, or a bare timestamp) rather than something a
    human - or a confident prior classification - actually wrote."""
    stripped = (title or "").strip()
    if not stripped:
        return True
    return bool(_GENERIC_TITLE_RE.match(stripped))


async def _resolve_correspondent_id(paperless: Paperless, taxonomy: Taxonomy, name: str) -> int | None:
    blacklist = runtime_config.correspondent_blacklist()
    if is_blacklisted_correspondent(name, blacklist):
        return None  # defense in depth - the prompt/classifier should already have dropped this
    target_id = taxonomy.match_correspondent(name)
    if target_id is None:
        target_id = await paperless.create_correspondent(name)
        taxonomy.correspondent_ids[name] = target_id  # visible to match_correspondent before the next refresh
    return target_id


async def apply(
    paperless: Paperless,
    taxonomy: Taxonomy,
    paperless_id: int,
    result: ClassificationResult,
    previously_applied_tag_ids: list[int],
    previously_applied_type_id: int | None,
    previously_applied_correspondent_id: int | None,
) -> tuple[int | None, list[int], int | None, str]:
    """Returns (applied_document_type_id, applied_tag_ids, applied_correspondent_id, note)."""
    doc = await paperless.document(paperless_id)
    live_tag_ids = set(doc.get("tags") or [])
    live_type_id = doc.get("document_type")
    live_correspondent_id = doc.get("correspondent")
    live_title = doc.get("title") or ""

    if result.status == "classified":
        decided_tag_ids = {taxonomy.tag_ids[t] for t in result.tags if t in taxonomy.tag_ids}
    else:
        decided_tag_ids = {taxonomy.needs_review_tag_id} if taxonomy.needs_review_tag_id else set()

    new_tag_ids = (live_tag_ids - set(previously_applied_tag_ids)) | decided_tag_ids

    notes = []
    new_type_id = live_type_id
    if result.status == "classified" and result.document_type:
        target_type_id = taxonomy.type_ids.get(result.document_type)
        if live_type_id is None or live_type_id == previously_applied_type_id:
            new_type_id = target_type_id
        else:
            notes.append("document_type skipped: manually corrected")

    new_correspondent_id = live_correspondent_id
    if result.status == "classified" and result.correspondent:
        if live_correspondent_id is None or live_correspondent_id == previously_applied_correspondent_id:
            new_correspondent_id = await _resolve_correspondent_id(paperless, taxonomy, result.correspondent)
        else:
            notes.append("correspondent skipped: manually corrected")

    title_to_apply: str | None = None
    if result.status == "classified" and result.title and looks_generic_title(live_title):
        title_to_apply = result.title

    await paperless.patch_document(
        paperless_id,
        new_type_id,
        sorted(new_tag_ids),
        correspondent_id=new_correspondent_id,
        title=title_to_apply,
    )
    return new_type_id, sorted(new_tag_ids), new_correspondent_id, "; ".join(notes)
