from datetime import datetime
from decimal import Decimal
from pathlib import Path

from receipt_bot.db import ReceiptRepository
from receipt_bot.models import ParsedReceipt, ReceiptItemDraft, SourcePage
from receipt_bot.processor import ReceiptProcessor
from receipt_bot.translation import IdentityTranslator
from receipt_bot.verification import VerificationDocument


class StubRenderer:
    def qr_pages(self, _: Path, __: int) -> list[SourcePage]:
        return [SourcePage(page_number=1, image_path=Path("not-read-by-qr.png"))]


class StubQrReader:
    def read(self, _: Path) -> list[str]:
        return ["https://suf.purs.gov.rs/v/?vl=test"]


class StubVerifier:
    def fetch(self, payload: str) -> tuple[ParsedReceipt, VerificationDocument]:
        return ParsedReceipt(
            merchant_name_source="MAXI",
            receipt_datetime=datetime(2026, 10, 13, 10, 0),
            gross_total=Decimal("100.00"),
            items=[ReceiptItemDraft(
                position=1,
                name_source="HLEB",
                quantity=Decimal("1"),
                unit="pcs",
                unit_price=Decimal("100.00"),
                line_total=Decimal("100.00"),
                confidence=1.0,
            )],
            raw_text="MAXI\\nHLEB",
            confidence=1.0,
            qr_url=payload,
        ), VerificationDocument(payload, "{}", "application/json", 200)


def test_processor_persists_a_high_confidence_receipt(tmp_path: Path) -> None:
    repository = ReceiptRepository(tmp_path / "receipts.sqlite3")
    repository.initialise()
    original = tmp_path / "receipt.jpg"
    original.write_bytes(b"receipt")
    stored, _ = repository.add_file(original, "processing-hash", "image/jpeg", "receipt.jpg", None, None)
    receipt_id = repository.queue_single_receipt(1, stored.id)
    assert receipt_id is not None
    processor = ReceiptProcessor(
        repository, StubRenderer(), StubQrReader(), StubVerifier(), IdentityTranslator(), 0.90
    )

    outcome = processor.process(receipt_id)

    assert outcome.needs_review is False
    assert "MAXI" in outcome.summary
    assert repository.status_counts(1) == {"COMPLETED": 1}
