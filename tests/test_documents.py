from pathlib import Path

import receipt_bot.documents as documents
from receipt_bot.documents import DocumentRenderer
from receipt_bot.storage import LocalStorage


class FakePdfPage:
    def __init__(self, text: str) -> None:
        self.text = text

    def extract_text(self) -> str:
        return self.text


class FakePdfReader:
    def __init__(self, _: str) -> None:
        self.pages = [FakePdfPage("Digitally embedded receipt text"), FakePdfPage("")]


def test_pdf_uses_embedded_text_and_renders_only_missing_pages(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "receipt.pdf"
    source.write_bytes(b"placeholder")
    storage = LocalStorage(tmp_path / "data")
    renderer = DocumentRenderer(storage)
    rendered = storage.page_path(12, 2)
    rendered.write_bytes(b"png")
    monkeypatch.setattr(documents, "PdfReader", FakePdfReader)
    monkeypatch.setattr(renderer, "_render_pdf", lambda _source, _receipt_id: {2: rendered})

    pages = renderer.pages(source, 12)

    assert pages[0].embedded_text == "Digitally embedded receipt text"
    assert pages[0].image_path is None
    assert pages[1].embedded_text is None
    assert pages[1].image_path == rendered
