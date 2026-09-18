"""Integration tests for GET /settings: the Ollama model-picker dropdown and
its fallback to a plain text input when Ollama can't be reached.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import runtime_config
from app.ollama import OllamaError
from app.routers import ui


class _FakeOllamaOk:
    async def list_models(self):
        return [
            {"name": "llama3.1:8b", "vision": False},
            {"name": "qwen2.5vl:7b", "vision": True},
        ]


class _FakeOllamaDown:
    async def list_models(self):
        raise OllamaError("Ollama is not reachable at http://ollama.invalid:11434: connection refused")


def _make_client(ollama) -> TestClient:
    app = FastAPI()
    app.include_router(ui.router)
    app.state.ollama = ollama
    return TestClient(app)


def test_settings_renders_a_select_when_ollama_is_reachable():
    client = _make_client(_FakeOllamaOk())

    resp = client.get("/settings")

    assert resp.status_code == 200
    assert '<select id="ollama_model" name="ollama_model" required>' in resp.text
    assert "qwen2.5vl:7b" in resp.text
    assert "Vision models (recommended)" in resp.text
    assert "won't work for classification" in resp.text
    # the fallback text input must not also be rendered
    assert '<input type="text" id="ollama_model"' not in resp.text


def test_settings_falls_back_to_text_input_when_ollama_is_unreachable():
    """This is the actual failure mode being guarded against: if Ollama can't
    be reached, /settings must still render 200 with a working text input,
    not 500, and must tell the user why there's no dropdown."""
    client = _make_client(_FakeOllamaDown())

    resp = client.get("/settings")

    assert resp.status_code == 200
    assert '<select id="ollama_model"' not in resp.text
    assert '<input type="text" id="ollama_model" name="ollama_model"' in resp.text
    assert "model list couldn't be loaded" in resp.text.lower()
    # the currently configured model must still be present as the input's value,
    # not silently blanked out or changed
    current_model = runtime_config.load().ollama_model
    assert f'value="{current_model}"' in resp.text


def test_settings_keeps_current_model_selectable_even_if_ollama_forgot_it():
    """If the configured model isn't in Ollama's list (removed, renamed, or
    typed in by hand), it must still show up as a selectable/selected option
    so saving the form without touching the field doesn't silently switch it."""

    class FakeOllamaMissingCurrent:
        async def list_models(self):
            return [{"name": "some-other-model:latest", "vision": True}]

    client = _make_client(FakeOllamaMissingCurrent())
    current_model = runtime_config.load().ollama_model

    resp = client.get("/settings")

    assert resp.status_code == 200
    assert f'<option value="{current_model}"' in resp.text
    assert "not found in Ollama" in resp.text
    # and it must be the selected option, not silently defaulted to something else
    assert f'value="{current_model}" selected' in resp.text
