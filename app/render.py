"""Render a document (PDF or image) into an in-memory PNG per page. Nothing touches disk."""

from __future__ import annotations

import io

import pypdfium2 as pdfium
from PIL import Image

MAX_IMAGE_SIDE = 3000


class Pages:
    def __init__(self, data: bytes) -> None:
        self._pdf: pdfium.PdfDocument | None = None
        self._image: Image.Image | None = None
        try:
            self._pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            self._image = Image.open(io.BytesIO(data)).convert("RGB")

    @property
    def count(self) -> int:
        return len(self._pdf) if self._pdf is not None else 1

    def png(self, index: int, dpi: int) -> bytes:
        if self._pdf is not None:
            page = self._pdf[index]
            image = page.render(scale=dpi / 72).to_pil().convert("RGB")
        else:
            image = self._image
            side = max(image.size)
            if side > MAX_IMAGE_SIDE:
                ratio = MAX_IMAGE_SIDE / side
                image = image.resize((int(image.width * ratio), int(image.height * ratio)))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
