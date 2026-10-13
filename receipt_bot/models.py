from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    page_number: int
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class ReceiptItemDraft:
    position: int
    name_source: str
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    producer_source: str | None = None
    confidence: float = 0.0
    name_en: str | None = None
    producer_en: str | None = None


@dataclass
class ParsedReceipt:
    merchant_name_source: str | None
    receipt_datetime: datetime | None
    currency: str = "RSD"
    gross_total: Decimal | None = None
    tax_total: Decimal | None = None
    payment_method: str | None = None
    fiscal_receipt_number: str | None = None
    items: list[ReceiptItemDraft] = field(default_factory=list)
    merchant_name_en: str | None = None
    raw_text: str = ""
    ocr_language: str = "unknown"
    confidence: float = 0.0
    validation_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SourcePage:
    page_number: int
    image_path: Path | None = None
    embedded_text: str | None = None


@dataclass(frozen=True)
class StoredFile:
    id: int
    receipt_id: int | None
    path: Path
    sha256: str
    mime_type: str
    telegram_file_id: str | None


ReceiptStatus = Literal["QUEUED", "PROCESSING", "COMPLETED", "NEEDS_REVIEW", "FAILED"]
