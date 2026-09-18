"""Paperless-ngx REST client: read taxonomy/documents, write classification back."""

from __future__ import annotations

from typing import Any

import httpx


class Paperless:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.url,
            headers={"Authorization": f"Token {self.token}", "Accept": "application/json"},
            timeout=60,
        )

    async def _get_all(self, path: str, params: dict[str, Any]) -> list[dict]:
        """Follow Paperless pagination until the last page."""
        out: list[dict] = []
        async with self._client() as client:
            resp = await client.get(path, params={**params, "page_size": 200})
            resp.raise_for_status()
            data = resp.json()
            out.extend(data.get("results", []))
            while data.get("next"):
                resp = await client.get(data["next"])
                resp.raise_for_status()
                data = resp.json()
                out.extend(data.get("results", []))
        return out

    async def documents(self, **params: Any) -> list[dict]:
        return await self._get_all("/api/documents/", params)

    async def tags(self) -> dict[str, int]:
        rows = await self._get_all("/api/tags/", {})
        return {r["name"]: r["id"] for r in rows}

    async def document_types(self) -> dict[str, int]:
        rows = await self._get_all("/api/document_types/", {})
        return {r["name"]: r["id"] for r in rows}

    async def correspondents(self) -> dict[str, int]:
        rows = await self._get_all("/api/correspondents/", {})
        return {r["name"]: r["id"] for r in rows}

    async def create_tag(self, name: str) -> int:
        async with self._client() as client:
            resp = await client.post("/api/tags/", json={"name": name})
            resp.raise_for_status()
            return resp.json()["id"]

    async def create_correspondent(self, name: str) -> int:
        async with self._client() as client:
            resp = await client.post("/api/correspondents/", json={"name": name})
            resp.raise_for_status()
            return resp.json()["id"]

    async def document(self, paperless_id: int) -> dict:
        async with self._client() as client:
            resp = await client.get(f"/api/documents/{paperless_id}/")
            resp.raise_for_status()
            return resp.json()

    async def download(self, paperless_id: int) -> bytes:
        """The original file bytes, kept in memory by the caller and never stored."""
        async with self._client() as client:
            resp = await client.get(
                f"/api/documents/{paperless_id}/download/",
                params={"original": "true"},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.content

    async def patch_document(
        self,
        paperless_id: int,
        document_type_id: int | None,
        tag_ids: list[int],
        correspondent_id: int | None = None,
        title: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"tags": tag_ids}
        if document_type_id is not None:
            payload["document_type"] = document_type_id
        if correspondent_id is not None:
            payload["correspondent"] = correspondent_id
        if title is not None:
            payload["title"] = title
        async with self._client() as client:
            resp = await client.patch(f"/api/documents/{paperless_id}/", json=payload)
            resp.raise_for_status()
