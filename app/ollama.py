"""Local Ollama vision call for document classification, with JSON-schema-constrained output."""

from __future__ import annotations

import base64
import json
import logging

import httpx

log = logging.getLogger("paperclass")


class OllamaError(Exception):
    """Infra-level failure: unreachable, model missing, bad HTTP status."""


class ClassifyError(Exception):
    """The model's answer could not be used (empty, truncated, bad JSON)."""


def describe(exc: BaseException) -> str:
    """Never an empty message: fall back to the exception's class name."""
    text = str(exc).strip()
    return text[:300] if text else type(exc).__name__


class Ollama:
    def __init__(self, url: str, timeout: float, num_ctx: int, keep_alive: str) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.keep_alive = _keep_alive(keep_alive)

    async def _chat(self, body: dict) -> dict:
        """POST /api/chat; one retry on a timeout."""
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(f"{self.url}/api/chat", json=body)
            except httpx.TimeoutException as exc:
                if attempt == 1:
                    log.warning("ollama %s timed out, retrying once", body["model"])
                    continue
                raise OllamaError(f"Ollama timed out twice after {int(self.timeout)}s ({body['model']})") from exc
            except httpx.HTTPError as exc:
                raise OllamaError(f"Ollama is not reachable at {self.url}: {describe(exc)}") from exc
            if resp.status_code == 404:
                raise OllamaError(f"model {body['model']} is not installed in Ollama")
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("error", "")
                except ValueError:
                    detail = resp.text[:200]
                raise OllamaError(f"Ollama error {resp.status_code}: {detail or resp.reason_phrase}")
            return resp.json()
        raise AssertionError("unreachable")

    async def list_models(self) -> list[dict]:
        """GET /api/tags: the models Ollama currently has pulled, for the
        settings-page dropdown. Returns `{"name": str, "vision": bool}` dicts,
        vision-capable models first, then alphabetically within each group.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.url}/api/tags")
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama is not reachable at {self.url}: {describe(exc)}") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", "")
            except ValueError:
                detail = resp.text[:200]
            raise OllamaError(f"Ollama error {resp.status_code}: {detail or resp.reason_phrase}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise OllamaError(f"Ollama returned invalid JSON for /api/tags: {describe(exc)}") from exc

        models = []
        for entry in data.get("models", []):
            name = entry.get("name")
            if not name:
                continue
            models.append({"name": name, "vision": "vision" in (entry.get("capabilities") or [])})
        models.sort(key=lambda m: (not m["vision"], m["name"]))
        return models

    async def classify(self, model: str, prompt: str, schema: dict, png: bytes) -> dict:
        body = {
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "temperature": 0, "num_predict": 512},
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(png).decode()]}],
            "format": schema,
        }
        r = await self._chat(body)
        content = (r.get("message") or {}).get("content") or ""
        if r.get("done_reason") == "length":
            raise ClassifyError("answer cut off (length)")
        if not content.strip():
            raise ClassifyError("empty answer")
        try:
            return json.loads(content)
        except ValueError as exc:
            raise ClassifyError(f"bad JSON: {describe(exc)}") from exc


def _keep_alive(value: str) -> str | int:
    # Ollama wants a number for 0 / -1 / plain seconds, a duration string otherwise.
    return int(value) if value.lstrip("-").isdigit() else value
