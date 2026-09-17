"""The classification pipeline: render page(s) -> Ollama vision call -> validate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ollama import ClassifyError, Ollama, OllamaError, describe
from .render import Pages
from .taxonomy import Taxonomy

PROMPT_TEMPLATE = (Path(__file__).parent.parent / "prompts" / "classify_prompt.tmpl").read_text(encoding="utf-8")


@dataclass
class ClassificationResult:
    document_type: str | None
    tags: list[str]
    confidence: str
    raw: str
    status: str  # "classified" | "needs_review" | "error"
    reason: str = ""


def _build_prompt(taxonomy: Taxonomy) -> str:
    return PROMPT_TEMPLATE.format(
        document_types="\n".join(f"- {t}" for t in sorted(taxonomy.type_ids)),
        tags="\n".join(f"- {t}" for t in sorted(taxonomy.tag_ids)),
    )


def _validate(answer: dict, taxonomy: Taxonomy) -> tuple[str | None, bool, list[str], bool]:
    dtype = answer.get("document_type")
    if not isinstance(dtype, str) or dtype not in taxonomy.type_ids:
        dtype = None
    type_confident = answer.get("document_type_confidence") == "high"

    tags_in = answer.get("tags") or []
    tags = [t for t in tags_in if isinstance(t, str) and t in taxonomy.tag_ids][:2]
    tags_confident = answer.get("tags_confidence") == "high"

    return dtype, type_confident, tags, tags_confident


async def classify(ollama: Ollama, model: str, taxonomy: Taxonomy, file_bytes: bytes, dpi: int) -> ClassificationResult:
    """document_type is applied whenever the model is confident in it, independently
    of tag confidence - tags are only ever applied when the model is *also*
    confident in them specifically, since they're the noisier half of the answer
    (e.g. Shopping vs Electronics vs Home ambiguity). Low tag confidence means
    "type is fine, just add no tags", not "needs review"."""
    prompt = _build_prompt(taxonomy)
    schema = taxonomy.schema()

    try:
        pages = Pages(file_bytes)
    except Exception as exc:  # noqa: BLE001 - any parse failure means we can't render at all
        return ClassificationResult(None, [], "low", "", "error", f"could not open file: {describe(exc)}")

    page_indices = [0] + ([1] if pages.count > 1 else [])
    last_error = ""
    for index in page_indices:
        try:
            png = pages.png(index, dpi)
            answer = await ollama.classify(model, prompt, schema, png)
        except OllamaError as exc:
            return ClassificationResult(None, [], "low", "", "error", describe(exc))
        except ClassifyError as exc:
            last_error = describe(exc)
            continue

        dtype, type_confident, tags, tags_confident = _validate(answer, taxonomy)
        if dtype is not None and type_confident:
            final_tags = tags if tags_confident else []
            confidence = "high" if tags_confident else "low"
            return ClassificationResult(dtype, final_tags, confidence, str(answer), "classified")
        last_error = f"low confidence or unmatched type (raw={answer})"

    return ClassificationResult(None, [], "low", last_error, "needs_review", last_error)
