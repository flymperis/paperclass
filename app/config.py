"""Settings, read from the environment (see .env.example)."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    paperless_url: str = "http://192.168.1.100:8000"
    paperless_token: str = ""

    ollama_url: str = "http://192.168.1.100:11434"
    ollama_model: str = "qwen3.5:4b"
    ollama_num_ctx: int = 8192
    ollama_timeout_seconds: int = 300
    ollama_keep_alive: str = "30s"

    candidate_tags: list[str] = [
        "Blood Test",
        "Electricity",
        "Electronics",
        "Home",
        "Imaging",
        "Internet",
        "Medical Report",
        "Prescription",
        "Shopping",
        "Vehicle",
    ]
    correspondent_blacklist: list[str] = [
        "Fotis Lymperis",
        "Φώτιος Λυμπέρης",
        "ΦΩΤΙΟΣ ΛΥΜΠΕΡΗΣ",
        "Φώτης Λυμπέρης",
    ]
    needs_review_tag: str = "Needs Review"
    taxonomy_refresh_minutes: int = 60
    classify_dpi: int = 150

    webhook_secret: str = ""
    log_raw_webhooks: bool = False

    data_dir: str = "./data"

    model_config = {"env_file": os.environ.get("PAPERCLASS_ENV_FILE", ".env"), "extra": "ignore"}

    @property
    def database_path(self) -> str:
        return os.path.join(self.data_dir, "paperclass.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
