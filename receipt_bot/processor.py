from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .db import ReceiptRepository
from .documents import DocumentRenderer
from .models import OcrLine, ParsedReceipt
from .ocr import OcrEngine
from .parser import SerbianReceiptParser
from .translation import Translator


@dataclass(frozen=True)
class ProcessOutcome:
    receipt_id: int
    needs_review: bool
    summary: str


class ReceiptProcessor:
    def __init__(
        self,
        repository: ReceiptRepository,
        renderer: DocumentRenderer,
        ocr: OcrEngine,
        parser: SerbianReceiptParser,
        translator: Translator,
        confidence_threshold: float,
    ) -> None:
        self.repository = repository
        self.renderer = renderer
        self.ocr = ocr
        self.parser = parser
        self.translator = translator
        self.confidence_threshold = confidence_threshold

    def process(self, receipt_id: int) -> ProcessOutcome:
        self.repository.set_processing(receipt_id)
        all_lines: list[OcrLine] = []
        languages: list[str] = []
        try:
            for stored_file in self.repository.list_receipt_files(receipt_id):
                pages = self.renderer.pages(stored_file.path, receipt_id)
                page_rows = []
                for page in pages:
                    if page.embedded_text:
                        lines = [
                            OcrLine(text=line.strip(), confidence=1.0, page_number=page.page_number)
                            for line in page.embedded_text.splitlines()
                            if line.strip()
                        ]
                        language = "embedded_pdf_text"
                    else:
                        assert page.image_path is not None
                        lines, language = self.ocr.extract(page.image_path, page.page_number)
                    all_lines.extend(lines)
                    languages.append(language)
                    page_rows.append({
                        "page_number": page.page_number,
                        "rendered_path": str(page.image_path) if page.image_path else None,
                        "embedded_text": page.embedded_text,
                        "ocr_text": "\n".join(line.text for line in lines),
                        "ocr_confidence": (
                            sum(line.confidence for line in lines) / len(lines) if lines else 0.0
                        ),
                    })
                self.repository.save_pages(stored_file.id, page_rows)

            parsed = self.parser.parse(all_lines, self._dominant_language(languages))
            self._translate(parsed)
            needs_review = self.repository.save_processed_receipt(
                receipt_id, parsed, self.confidence_threshold
            )
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
        from decimal import Decimal

        english = self.translator.translate(name_source)
        self.repository.save_translation_override(name_source, english)
        self.repository.add_item_from_manual_entry(
            receipt_id, name_source, english,
            Decimal(quantity.replace(",", ".")), Decimal(line_total.replace(",", ".")),
        )

    def _translate(self, parsed: ParsedReceipt) -> None:
        if parsed.merchant_name_source:
            parsed.merchant_name_en = self.translator.translate(parsed.merchant_name_source)
        for item in parsed.items:
            item.name_en = self.translator.translate(item.name_source)
            if item.producer_source:
                item.producer_en = self.translator.translate(item.producer_source)

    @staticmethod
    def _dominant_language(languages: list[str]) -> str:
        return max(set(languages), key=languages.count) if languages else "unknown"
