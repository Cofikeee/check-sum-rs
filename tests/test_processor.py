from datetime import datetime
from pathlib import Path

from receipt_bot.db import ReceiptRepository
from receipt_bot.models import OcrLine, SourcePage
from receipt_bot.ocr import StaticOcrEngine
from receipt_bot.parser import SerbianReceiptParser
from receipt_bot.processor import ReceiptProcessor
from receipt_bot.translation import IdentityTranslator


class StubRenderer:
    def pages(self, _: Path, __: int) -> list[SourcePage]:
        return [SourcePage(page_number=1, image_path=Path("not-read-by-static-ocr.png"))]


def test_processor_persists_a_high_confidence_receipt(tmp_path: Path) -> None:
    repository = ReceiptRepository(tmp_path / "receipts.sqlite3")
    repository.initialise()
    original = tmp_path / "receipt.jpg"
    original.write_bytes(b"receipt")
    stored, _ = repository.add_file(original, "processing-hash", "image/jpeg", "receipt.jpg", None, None)
    receipt_id = repository.queue_single_receipt(1, stored.id)
    assert receipt_id is not None
    ocr = StaticOcrEngine([
        OcrLine("MAXI", 0.99, 1),
        OcrLine("13.10.2026 10:00", 0.99, 1),
        OcrLine("HLEB 1 100,00", 0.99, 1),
        OcrLine("UKUPNO 100,00", 0.99, 1),
    ])
    processor = ReceiptProcessor(
        repository, StubRenderer(), ocr, SerbianReceiptParser(), IdentityTranslator(), 0.90
    )

    outcome = processor.process(receipt_id)

    assert outcome.needs_review is False
    assert "MAXI" in outcome.summary
    assert repository.status_counts(1) == {"COMPLETED": 1}
