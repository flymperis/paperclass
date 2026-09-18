"""The classification pipeline: render page(s) -> Ollama vision call -> validate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ollama import ClassifyError, Ollama, OllamaError, describe
from .render import Pages
from .taxonomy import Taxonomy, is_blacklisted_correspondent

PROMPT_TEMPLATE = (Path(__file__).parent.parent / "prompts" / "classify_prompt.tmpl").read_text(encoding="utf-8")


@dataclass
class ClassificationResult:
    document_type: str | None
    tags: list[str]
    confidence: str
    raw: str
    status: str  # "classified" | "needs_review" | "error"
    reason: str = ""
    correspondent: str = ""  # empty when not confident enough to use, or genuinely absent
    title: str = ""  # empty when not confident enough to use


def _build_prompt(taxonomy: Taxonomy, correspondent_blacklist: list[str]) -> str:
    return PROMPT_TEMPLATE.format(
        document_types="\n".join(f"- {t}" for t in sorted(taxonomy.type_ids)),
        tags="\n".join(f"- {t}" for t in sorted(taxonomy.tag_ids)),
        correspondent_blacklist="\n".join(f"- {name}" for name in correspondent_blacklist),
    )


def _validate(
    answer: dict, taxonomy: Taxonomy, correspondent_blacklist: list[str]
) -> tuple[str | None, bool, list[str], bool, str, bool, str, bool]:
    dtype = answer.get("document_type")
    if not isinstance(dtype, str) or dtype not in taxonomy.type_ids:
        dtype = None
    type_confident = answer.get("document_type_confidence") == "high"

    tags_in = answer.get("tags") or []
    tags = [t for t in tags_in if isinstance(t, str) and t in taxonomy.tag_ids][:2]
    tags_confident = answer.get("tags_confidence") == "high"

    correspondent = answer.get("correspondent")
    correspondent = correspondent.strip() if isinstance(correspondent, str) else ""
    if is_blacklisted_correspondent(correspondent, correspondent_blacklist):
        correspondent = ""  # the model ignored the blacklist rule - drop it rather than trust it
    correspondent_confident = answer.get("correspondent_confidence") == "high"

    title = answer.get("title")
    title = title.strip() if isinstance(title, str) else ""
    title_confident = answer.get("title_confidence") == "high"

    return dtype, type_confident, tags, tags_confident, correspondent, correspondent_confident, title, title_confident


async def classify(
    ollama: Ollama,
    model: str,
    taxonomy: Taxonomy,
    file_bytes: bytes,
    dpi: int,
    correspondent_blacklist: list[str],
) -> ClassificationResult:
    """document_type is applied whenever the model is confident in it, independently
    of tag confidence - tags are only ever applied when the model is *also*
    confident in them specifically, since they're the noisier half of the answer
    (e.g. Shopping vs Electronics vs Home ambiguity). Low tag confidence means
    "type is fine, just add no tags", not "needs review". correspondent and title
    are similarly each gated on their own confidence field, independently of
    document_type/tags - a low-confidence correspondent/title is dropped
    (empty string) rather than blocking classification of the rest."""
    prompt = _build_prompt(taxonomy, correspondent_blacklist)
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

        dtype, type_confident, tags, tags_confident, correspondent, correspondent_confident, title, title_confident = (
            _validate(answer, taxonomy, correspondent_blacklist)
        )
        if dtype is not None and type_confident:
            final_tags = tags if tags_confident else []
            confidence = "high" if tags_confident else "low"
            final_correspondent = correspondent if (correspondent_confident and correspondent) else ""
            final_title = title if (title_confident and title) else ""
            return ClassificationResult(
                dtype, final_tags, confidence, str(answer), "classified",
                correspondent=final_correspondent, title=final_title,
            )
        last_error = f"low confidence or unmatched type (raw={answer})"

    return ClassificationResult(None, [], "low", last_error, "needs_review", last_error)
