"""Idempotent write-back of classification results to Paperless.

Re-runs (Paperless's own Celery retries can double-fire a webhook) must not
corrupt state: we only ever remove tags *we* previously applied to a document,
never tags a human added independently, and we only touch document_type when
it's unset or still equal to what we last set ourselves (a differing live
value means a human corrected it - leave it alone, same intent as
paperless-gpt's PRESERVE_EXISTING_METADATA).
"""

from __future__ import annotations

from .classifier import ClassificationResult
from .paperless import Paperless
from .taxonomy import Taxonomy


async def apply(
    paperless: Paperless,
    taxonomy: Taxonomy,
    paperless_id: int,
    result: ClassificationResult,
    previously_applied_tag_ids: list[int],
    previously_applied_type_id: int | None,
) -> tuple[int | None, list[int], str]:
    """Returns (applied_document_type_id, applied_tag_ids, note)."""
    doc = await paperless.document(paperless_id)
    live_tag_ids = set(doc.get("tags") or [])
    live_type_id = doc.get("document_type")

    if result.status == "classified":
        decided_tag_ids = {taxonomy.tag_ids[t] for t in result.tags if t in taxonomy.tag_ids}
    else:
        decided_tag_ids = {taxonomy.needs_review_tag_id} if taxonomy.needs_review_tag_id else set()

    new_tag_ids = (live_tag_ids - set(previously_applied_tag_ids)) | decided_tag_ids

    note = ""
    new_type_id = live_type_id
    if result.status == "classified" and result.document_type:
        target_type_id = taxonomy.type_ids.get(result.document_type)
        if live_type_id is None or live_type_id == previously_applied_type_id:
            new_type_id = target_type_id
        else:
            note = "skipped: manually corrected"

    await paperless.patch_document(paperless_id, new_type_id, sorted(new_tag_ids))
    return new_type_id, sorted(new_tag_ids), note
