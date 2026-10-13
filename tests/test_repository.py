from datetime import datetime
from decimal import Decimal
from pathlib import Path

from receipt_bot.db import ReceiptRepository
from receipt_bot.models import ParsedReceipt, ReceiptItemDraft


def create_file(repository: ReceiptRepository, directory: Path, name: str, content: bytes = b"image") -> int:
    path = directory / name
    path.write_bytes(content)
    stored, inserted = repository.add_file(path, f"hash-{name}", "image/jpeg", name, None, None)
    assert inserted
    return stored.id


def complete_receipt() -> ParsedReceipt:
    return ParsedReceipt(
        merchant_name_source="MAXI",
        merchant_name_en="MAXI",
        receipt_datetime=datetime(2026, 10, 13, 14, 32),
        gross_total=Decimal("100.00"),
        items=[ReceiptItemDraft(
            position=1, name_source="HLEB", name_en="Bread", quantity=Decimal("1"),
            unit="pcs", unit_price=Decimal("100.00"), line_total=Decimal("100.00"), confidence=0.99,
        )],
        raw_text="MAXI\nHLEB",
        ocr_language="rs_latin",
        confidence=0.99,
    )


def test_single_receipt_is_deduplicated_and_completed(tmp_path: Path) -> None:
    repository = ReceiptRepository(tmp_path / "receipts.sqlite3")
    repository.initialise()
    file_id = create_file(repository, tmp_path, "one.jpg")
    receipt_id = repository.queue_single_receipt(11, file_id)
    assert receipt_id is not None
    assert repository.save_processed_receipt(receipt_id, complete_receipt(), 0.90) is False
    assert repository.status_counts(11) == {"COMPLETED": 1}
    assert "Bread" in repository.receipt_summary(receipt_id)

    stored, inserted = repository.add_file(tmp_path / "one.jpg", "hash-one.jpg", "image/jpeg", "one.jpg", None, None)
    assert inserted is False
    assert stored.receipt_id == receipt_id


def test_batch_attaches_multiple_files_to_one_receipt(tmp_path: Path) -> None:
    repository = ReceiptRepository(tmp_path / "receipts.sqlite3")
    repository.initialise()
    batch_id = repository.begin_batch(99)
    first = create_file(repository, tmp_path, "first.jpg")
    second = create_file(repository, tmp_path, "second.jpg")
    assert repository.stage_file_in_batch(batch_id, first)
    assert repository.stage_file_in_batch(batch_id, second)
    receipt_id = repository.finish_batch(99)
    assert receipt_id is not None
    assert len(repository.list_receipt_files(receipt_id)) == 2
    assert repository.active_batch(99) is None


def test_failed_receipt_is_recorded_for_qr_errors(tmp_path: Path) -> None:
    repository = ReceiptRepository(tmp_path / "receipts.sqlite3")
    repository.initialise()
    receipt_id = repository.queue_single_receipt(5, create_file(repository, tmp_path, "review.jpg"))
    assert receipt_id is not None
    repository.fail_receipt(receipt_id, "No QR code found")
    assert repository.status_counts(5) == {"FAILED": 1}
