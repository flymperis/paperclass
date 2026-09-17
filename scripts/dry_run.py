"""Offline accuracy check: classify already-tagged real documents, no writeback.

Usage: python -m scripts.dry_run [count]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import runtime_config  # noqa: E402
from app.classifier import classify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.ollama import Ollama  # noqa: E402
from app.paperless import Paperless  # noqa: E402
from app.taxonomy import Taxonomy  # noqa: E402


async def main(limit: int) -> None:
    init_db()
    settings = get_settings()
    cfg = runtime_config.load()
    paperless = Paperless(settings.paperless_url, settings.paperless_token)
    ollama = Ollama(
        settings.ollama_url, settings.ollama_timeout_seconds, settings.ollama_num_ctx, settings.ollama_keep_alive
    )
    taxonomy = Taxonomy(paperless, settings.needs_review_tag)
    await taxonomy.refresh()

    tag_names = {v: k for k, v in (await paperless.tags()).items()}
    type_names = {v: k for k, v in (await paperless.document_types()).items()}

    docs = await paperless.documents(ordering="-created")
    tested = [d for d in docs if d.get("document_type") and d.get("tags")][:limit]

    correct_type = 0
    tag_hits = 0
    tag_total = 0
    for doc in tested:
        file_bytes = await paperless.download(doc["id"])
        result = await classify(ollama, cfg.ollama_model, taxonomy, file_bytes, cfg.classify_dpi)

        actual_type = type_names.get(doc["document_type"])
        actual_tags = {tag_names.get(t) for t in doc["tags"]} & set(runtime_config.candidate_tags())
        got_tags = set(result.tags)

        type_ok = result.document_type == actual_type
        correct_type += int(type_ok)
        tag_hits += len(got_tags & actual_tags)
        tag_total += len(actual_tags)

        print(
            f"doc {doc['id']:>4} | actual={actual_type or '-':<8} tags={sorted(actual_tags)!s:<28} | "
            f"got={result.document_type or '-':<8} tags={sorted(got_tags)!s:<28} status={result.status:<12} "
            f"{'OK' if type_ok else 'MISS'}"
        )

    n = len(tested)
    print(f"\n{correct_type}/{n} document_type correct, {tag_hits}/{tag_total} candidate tags recovered")


if __name__ == "__main__":
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    asyncio.run(main(limit_arg))
