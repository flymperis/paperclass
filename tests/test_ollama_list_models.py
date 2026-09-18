"""Unit tests for `Ollama.list_models()`: parsing Ollama's GET /api/tags
response into `{"name", "vision"}` dicts for the settings-page dropdown, and
turning any transport/HTTP failure into the existing `OllamaError`.

No real network call is made: `httpx.AsyncClient` is monkeypatched to route
through an in-process `httpx.MockTransport`, the same client library the app
already depends on (no extra test dependency needed).
"""

from __future__ import annotations

import httpx
import pytest

from app import ollama as ollama_module
from app.ollama import Ollama, OllamaError


def _patch_transport(monkeypatch, handler):
    """Make every `httpx.AsyncClient(...)` the module constructs use `handler`
    instead of hitting the network, while keeping other kwargs (timeout) intact."""
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_async_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ollama_module.httpx, "AsyncClient", fake_async_client)


@pytest.mark.asyncio
async def test_list_models_parses_and_sorts_vision_first(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "llama3.1:8b", "capabilities": ["completion", "tools"]},
                    {"name": "qwen2.5vl:7b", "capabilities": ["completion", "vision"]},
                    {"name": "bakllava:latest", "capabilities": ["completion", "vision"]},
                    {"name": "mistral:7b", "capabilities": ["completion"]},
                ]
            },
        )

    _patch_transport(monkeypatch, handler)
    client = Ollama("http://ollama.invalid:11434", timeout=5, num_ctx=8192, keep_alive="30s")

    models = await client.list_models()

    assert models == [
        {"name": "bakllava:latest", "vision": True},
        {"name": "qwen2.5vl:7b", "vision": True},
        {"name": "llama3.1:8b", "vision": False},
        {"name": "mistral:7b", "vision": False},
    ]


@pytest.mark.asyncio
async def test_list_models_treats_missing_capabilities_as_text_only(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "no-caps:latest"}]})

    _patch_transport(monkeypatch, handler)
    client = Ollama("http://ollama.invalid:11434", timeout=5, num_ctx=8192, keep_alive="30s")

    models = await client.list_models()

    assert models == [{"name": "no-caps:latest", "vision": False}]


@pytest.mark.asyncio
async def test_list_models_raises_ollama_error_on_http_error_status(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal explosion"})

    _patch_transport(monkeypatch, handler)
    client = Ollama("http://ollama.invalid:11434", timeout=5, num_ctx=8192, keep_alive="30s")

    with pytest.raises(OllamaError, match="internal explosion"):
        await client.list_models()


@pytest.mark.asyncio
async def test_list_models_raises_ollama_error_when_unreachable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_transport(monkeypatch, handler)
    client = Ollama("http://ollama.invalid:11434", timeout=5, num_ctx=8192, keep_alive="30s")

    with pytest.raises(OllamaError, match="not reachable"):
        await client.list_models()


@pytest.mark.asyncio
async def test_list_models_raises_ollama_error_on_bad_json(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    _patch_transport(monkeypatch, handler)
    client = Ollama("http://ollama.invalid:11434", timeout=5, num_ctx=8192, keep_alive="30s")

    with pytest.raises(OllamaError, match="invalid JSON"):
        await client.list_models()
