from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageOps
from pypdf import PdfReader

from .models import SourcePage
from .storage import LocalStorage


class DocumentRenderer:
    """Normalises image uploads and renders only non-text PDF pages to PNG."""

    def __init__(self, storage: LocalStorage) -> None:
        self.storage = storage

    def pages(self, source: Path, receipt_id: int) -> list[SourcePage]:
        if source.suffix.lower() == ".pdf":
            return self._pdf_pages(source, receipt_id)
        return [SourcePage(page_number=1, image_path=self._normalise_image(source, receipt_id, 1))]

    def _normalise_image(self, source: Path, receipt_id: int, page_number: int) -> Path:
        target = self.storage.page_path(receipt_id, page_number)
        with Image.open(source) as image:
            normalised = ImageOps.exif_transpose(image).convert("RGB")
            normalised.save(target, "PNG", optimize=True)
        return target

    def _pdf_pages(self, source: Path, receipt_id: int) -> list[SourcePage]:
        try:
            reader = PdfReader(str(source))
        except Exception as error:
            raise ValueError(f"The PDF cannot be read: {error}") from error
        embedded = [(page.extract_text() or "").strip() for page in reader.pages]
        pages: list[SourcePage] = []
        rendered: dict[int, Path] = {}
        missing = [index for index, text in enumerate(embedded, start=1) if len(text) < 20]
        if missing:
            rendered = self._render_pdf(source, receipt_id)
        for index, text in enumerate(embedded, start=1):
            if len(text) >= 20:
                pages.append(SourcePage(page_number=index, embedded_text=text))
            else:
                pages.append(SourcePage(page_number=index, image_path=rendered[index]))
        return pages

    def _render_pdf(self, source: Path, receipt_id: int) -> dict[int, Path]:
        with tempfile.TemporaryDirectory(prefix="receipt-pdf-") as temporary:
            prefix = Path(temporary) / "page"
            completed = subprocess.run(
                ["pdftoppm", "-png", "-r", "300", str(source), str(prefix)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"PDF rendering failed: {completed.stderr.strip()}")
            rendered: dict[int, Path] = {}
            for temporary_page in sorted(Path(temporary).glob("page-*.png")):
                page_number = int(temporary_page.stem.rsplit("-", 1)[-1])
                target = self.storage.page_path(receipt_id, page_number)
                shutil.move(str(temporary_page), target)
                rendered[page_number] = target
            if not rendered:
                raise RuntimeError("PDF rendering produced no image pages")
            return rendered
