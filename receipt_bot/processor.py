from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .db import ReceiptRepository
from .documents import DocumentRenderer
from .models import ParsedReceipt
from .qr import QrCodeReader
from .translation import Translator
from .verification import ReceiptVerificationClient, VerificationError


@dataclass(frozen=True)
class ProcessOutcome:
    receipt_id: int
    needs_review: bool
    summary: str


class ReceiptProcessor:
    """Process receipts through QR verification; OCR is intentionally unused."""

    def __init__(
        self,
        repository: ReceiptRepository,
        renderer: DocumentRenderer,
        qr_reader: QrCodeReader,
        verifier: ReceiptVerificationClient,
        translator: Translator,
        confidence_threshold: float = 0.90,
    ) -> None:
        self.repository = repository
        self.renderer = renderer
        self.qr_reader = qr_reader
        self.verifier = verifier
        self.translator = translator
        self.confidence_threshold = confidence_threshold

    def process(self, receipt_id: int) -> ProcessOutcome:
        self.repository.set_processing(receipt_id)
        try:
            payloads: list[str] = []
            page_rows: list[tuple[int, dict[str, object]]] = []
            qr_codes: list[dict[str, object]] = []
            for stored_file in self.repository.list_receipt_files(receipt_id):
                pages = self.renderer.qr_pages(stored_file.path, receipt_id)
                for page in pages:
                    assert page.image_path is not None
                    page_payloads = self.qr_reader.read(page.image_path)
                    page_rows.append((stored_file.id, {
                        "page_number": page.page_number,
                        "rendered_path": str(page.image_path),
                        "qr_payloads": page_payloads,
                    }))
                    for payload in page_payloads:
                        if payload not in payloads:
                            payloads.append(payload)
                        qr_codes.append({
                            "receipt_file_id": stored_file.id,
                            "page_number": page.page_number,
                            "payload": payload,
                            "url": payload if payload.lower().startswith("https://") else None,
                        })
                self.repository.save_pages(stored_file.id, [row for file_id, row in page_rows if file_id == stored_file.id])
            if not payloads:
                raise VerificationError("No QR code found in the receipt")
            if len(payloads) > 1:
                raise VerificationError("More than one different QR payload was found in this receipt")

            parsed, document = self.verifier.fetch(payloads[0])
            parsed.qr_payload = payloads[0]
            parsed.qr_url = document.url or parsed.qr_url
            parsed.verification_response = document.content
            parsed.verification_content_type = document.content_type
            parsed.verification_http_status = document.status_code
            self._translate(parsed)
            self.repository.save_qr_codes(receipt_id, qr_codes)
            needs_review = self.repository.save_processed_receipt(receipt_id, parsed, self.confidence_threshold)
            return ProcessOutcome(receipt_id, needs_review, self.repository.receipt_summary(receipt_id))
        except Exception as error:
            self.repository.fail_receipt(receipt_id, str(error))
            raise

    def apply_review_correction(self, task: dict[str, object], value: str) -> int:
        field_key = str(task["field_key"])
        english_value = None
        if field_key == "receipt.merchant" or field_key.endswith(".name_source"):
            english_value = self.translator.translate(value)
            self.repository.save_translation_override(value, english_value)
        return self.repository.apply_correction(int(task["id"]), value, english_value)

    def add_manual_item(self, receipt_id: int, name_source: str, quantity: str, line_total: str) -> None:
        # Kept for conversational correction compatibility.  QR receipts should
        # normally never need this command; a failed QR is deliberately FAILED.
        from decimal import Decimal

        english = self.translator.translate(name_source)
        self.repository.save_translation_override(name_source, english)
        self.repository.add_item_from_manual_entry(
            receipt_id,
            name_source,
            english,
            Decimal(quantity.replace(",", ".")),
            Decimal(line_total.replace(",", ".")),
        )

    def _translate(self, parsed: ParsedReceipt) -> None:
        if parsed.merchant_name_source:
            parsed.merchant_name_en = self.translator.translate(parsed.merchant_name_source)
        for item in parsed.items:
            item.name_en = self.translator.translate(item.name_source)
            if item.producer_source:
                item.producer_en = self.translator.translate(item.producer_source)
