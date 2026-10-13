from __future__ import annotations

"""Read-only importer for SlipApp SQLite backups.

SlipApp already contains the fiscal-verification response decoded from each
receipt QR code.  This module deliberately does not touch the supplied SQLite
file: it copies/indexes the backup as an immutable source and writes only the
normalized records to the configured PostgreSQL repository.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Any

from .db import ReceiptRepository
from .models import ParsedReceipt, ReceiptItemDraft
from .translation import Translator
from .warehouse import WarehouseRepository
from .category import CategoryClassifier, classifier_from_environment


class SlipAppFormatError(ValueError):
    """The file is not a supported SlipApp backup."""


@dataclass(frozen=True)
class SlipAppRecord:
    source_record_id: str
    parsed: ParsedReceipt


@dataclass(frozen=True)
class SlipAppImportResult:
    import_id: int
    receipt_ids: tuple[int, ...]
    created: int
    updated: int
    needs_review: int
    records: int
    source_sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _unit(name: str) -> str | None:
    match = re.search(r"/(KOM|KG|G|L|SET|M|ком\.?|кг|л)/", name, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _translatable_name(name: str) -> tuple[str, str | None]:
    """Separate a printed item description from its unit/product code.

    The fiscal product code is not natural language and must remain unchanged
    when the Serbian description is translated.
    """
    match = re.match(r"^(.*?)\s*/([^/]+)/([^/]+)\s*$", name)
    if not match:
        return name.strip(), None
    return match.group(1).strip(), f"/{match.group(2)}/{match.group(3)}"


class SlipAppBackupReader:
    REQUIRED_TABLES = {"slips", "products", "payment_methods", "taxes"}
    REQUIRED_SLIP_COLUMNS = {
        "id", "shop_name", "shop_tax_id", "shop_location_name", "shop_administrative_unit",
        "shop_city", "shop_address", "shop_cashier", "slip_pfr", "slip_esir", "slip_time",
        "slip_amount", "slip_type", "slip_transaction_type", "raw_slip", "url",
    }

    def read(self, path: Path) -> list[SlipAppRecord]:
        if not path.is_file():
            raise FileNotFoundError(path)
        # URI mode=ro makes accidental writes to the user's backup impossible.
        uri = f"file:{path.resolve()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as error:
            raise SlipAppFormatError(f"Could not open SQLite backup: {error}") from error
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            missing = self.REQUIRED_TABLES - tables
            if missing:
                raise SlipAppFormatError(f"Not a supported SlipApp backup; missing tables: {', '.join(sorted(missing))}")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(slips)")}
            missing_columns = self.REQUIRED_SLIP_COLUMNS - columns
            if missing_columns:
                raise SlipAppFormatError(
                    "Not a supported SlipApp backup; missing slips columns: "
                    + ", ".join(sorted(missing_columns))
                )

            records: list[SlipAppRecord] = []
            slips = connection.execute("SELECT * FROM slips ORDER BY id").fetchall()
            for slip in slips:
                slip_id = int(slip["id"])
                products = connection.execute(
                    "SELECT * FROM products WHERE slip_id = ? ORDER BY id", (slip_id,)
                ).fetchall()
                payments = connection.execute(
                    "SELECT * FROM payment_methods WHERE slip_id = ? ORDER BY id", (slip_id,)
                ).fetchall()
                taxes = connection.execute(
                    "SELECT * FROM taxes WHERE slip_id = ? ORDER BY id", (slip_id,)
                ).fetchall()
                items = [
                    ReceiptItemDraft(
                        position=index,
                        name_source=str(product["name"] or "").strip(),
                        quantity=_decimal(product["quantity"]),
                        unit=_unit(str(product["name"] or "")),
                        unit_price=_decimal(product["price"]),
                        line_total=_decimal(product["amount"]),
                        # SlipApp does not provide a printed producer field.
                        producer_source=None,
                        confidence=1.0,
                        tax_symbol=str(product["tax_symbol"] or "").strip() or None,
                        tax_name=str(product["tax_name"] or "").strip() or None,
                    )
                    for index, product in enumerate(products, start=1)
                ]
                payment_method = "; ".join(
                    str(payment["name"] or payment["code"] or "").strip()
                    for payment in payments
                    if str(payment["name"] or payment["code"] or "").strip()
                ) or None
                tax_total = sum(
                    (amount for amount in (_decimal(tax["amount"]) for tax in taxes) if amount is not None),
                    Decimal("0"),
                )
                payment_details = [
                    {
                        "name": str(payment["name"] or "").strip() or None,
                        "code": str(payment["code"] or "").strip() or None,
                        "amount": str(payment["amount"]) if payment["amount"] is not None else None,
                    }
                    for payment in payments
                ]
                tax_details = [
                    {
                        "symbol": str(tax["symbol"] or "").strip() or None,
                        "name": str(tax["name"] or "").strip() or None,
                        "rate": str(tax["rate"]) if tax["rate"] is not None else None,
                        "amount": str(tax["amount"]) if tax["amount"] is not None else None,
                    }
                    for tax in taxes
                ]
                parsed = ParsedReceipt(
                    merchant_name_source=str(slip["shop_name"] or "").strip() or None,
                    receipt_datetime=_datetime(slip["slip_time"]),
                    currency="RSD",
                    gross_total=_decimal(slip["slip_amount"]),
                    tax_total=tax_total,
                    payment_method=payment_method,
                    fiscal_receipt_number=str(slip["slip_pfr"] or "").strip() or None,
                    items=items,
                    raw_text=str(slip["raw_slip"] or ""),
                    ocr_language="slipapp",
                    confidence=1.0,
                    qr_url=str(slip["url"] or "").strip() or None,
                    qr_payload=str(slip["url"] or "").strip() or None,
                    verification_response=str(slip["raw_slip"] or ""),
                    verification_content_type="text/plain",
                    verification_http_status=200,
                    source_method="slipapp_backup",
                    merchant_tax_id=str(slip["shop_tax_id"] or "").strip() or None,
                    merchant_location_name=str(slip["shop_location_name"] or "").strip() or None,
                    merchant_administrative_unit=str(slip["shop_administrative_unit"] or "").strip() or None,
                    merchant_city=str(slip["shop_city"] or "").strip() or None,
                    merchant_address=str(slip["shop_address"] or "").strip() or None,
                    merchant_cashier=str(slip["shop_cashier"] or "").strip() or None,
                    buyer=str(slip["buyer"] or "").strip() or None,
                    fiscal_esir=str(slip["slip_esir"] or "").strip() or None,
                    receipt_type=str(slip["slip_type"] or "").strip() or None,
                    transaction_type=str(slip["slip_transaction_type"] or "").strip() or None,
                    payment_details=payment_details,
                    tax_details=tax_details,
                )
                records.append(SlipAppRecord(str(slip_id), parsed))
            return records
        except sqlite3.Error as error:
            raise SlipAppFormatError(f"Could not read SlipApp backup: {error}") from error
        finally:
            connection.close()


class SlipAppImporter:
    def __init__(
        self,
        repository: ReceiptRepository,
        translator: Translator,
        confidence_threshold: float = 0.90,
        classifier: CategoryClassifier | None = None,
    ) -> None:
        self.repository = repository
        self.translator = translator
        self.confidence_threshold = confidence_threshold
        self.classifier = classifier or classifier_from_environment()
        self._translation_error: str | None = None

    def _translate(self, value: str | None) -> str | None:
        if not value:
            return value
        if self._translation_error is not None:
            return value
        try:
            return self.translator.translate(value)
        except Exception as error:  # keep the fiscal data even if model setup is unavailable
            self._translation_error = f"Local translation unavailable: {type(error).__name__}: {error}"
            return value

    def _translate_item_name(self, value: str) -> str:
        natural_name, suffix = _translatable_name(value)
        translated = self._translate(natural_name) or natural_name
        return f"{translated}{suffix or ''}"

    def _translated(self, parsed: ParsedReceipt) -> ParsedReceipt:
        warnings = list(parsed.validation_warnings)
        # Translate all fields first: the first model-load failure can happen
        # while translating the merchant or the first item.
        merchant_name_en = self._translate(parsed.merchant_name_source)
        payment_method = self._translate(parsed.payment_method)
        translated_items = [
            ReceiptItemDraft(
                position=item.position,
                name_source=item.name_source,
                name_en=self._translate_item_name(item.name_source),
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                line_total=item.line_total,
                producer_source=None,
                producer_en=None,
                confidence=item.confidence,
                tax_symbol=item.tax_symbol,
                tax_name=item.tax_name,
            )
            for item in parsed.items
        ]
        if self._translation_error and self._translation_error not in warnings:
            warnings.append(self._translation_error)
        return ParsedReceipt(
            merchant_name_source=parsed.merchant_name_source,
            merchant_name_en=merchant_name_en,
            receipt_datetime=parsed.receipt_datetime,
            currency=parsed.currency,
            gross_total=parsed.gross_total,
            tax_total=parsed.tax_total,
            payment_method=payment_method,
            fiscal_receipt_number=parsed.fiscal_receipt_number,
            items=translated_items,
            raw_text=parsed.raw_text,
            ocr_language=parsed.ocr_language,
            confidence=parsed.confidence,
            validation_warnings=warnings,
            qr_url=parsed.qr_url,
            qr_payload=parsed.qr_payload,
            verification_response=parsed.verification_response,
            verification_content_type=parsed.verification_content_type,
            verification_http_status=parsed.verification_http_status,
            source_method=parsed.source_method,
            merchant_tax_id=parsed.merchant_tax_id,
            merchant_location_name=parsed.merchant_location_name,
            merchant_administrative_unit=parsed.merchant_administrative_unit,
            merchant_city=parsed.merchant_city,
            merchant_address=parsed.merchant_address,
            merchant_cashier=parsed.merchant_cashier,
            buyer=parsed.buyer,
            fiscal_esir=parsed.fiscal_esir,
            receipt_type=parsed.receipt_type,
            transaction_type=parsed.transaction_type,
            payment_details=parsed.payment_details,
            tax_details=parsed.tax_details,
        )

    def import_file(
        self,
        path: Path,
        owner_chat_id: int,
        stored_path: Path | None = None,
        original_filename: str | None = None,
    ) -> SlipAppImportResult:
        source_sha256 = sha256_file(path)
        source_path = stored_path or path
        records = SlipAppBackupReader().read(path)

        # New installations use explicit bronze/silver layers.  The legacy
        # normalized tables remain supported for old SQLite fixtures and old
        # deployments, but are no longer the primary backup representation.
        if isinstance(self.repository, WarehouseRepository):
            import_id, _ = self.repository.register_bronze_slipapp_import(
                source_path, source_sha256, original_filename or path.name
            )
            receipt_ids, created = self.repository.store_bronze_slipapp_records(import_id, records)
            self.repository.build_silver_slipapp(import_id, self.translator, self.classifier)
            return SlipAppImportResult(
                import_id=import_id,
                receipt_ids=tuple(receipt_ids),
                created=created,
                updated=len(receipt_ids) - created,
                needs_review=0,
                records=len(records),
                source_sha256=source_sha256,
            )

        import_id, _ = self.repository.register_import(
            "SlipApp", source_path, source_sha256, original_filename or path.name
        )
        receipt_ids: list[int] = []
        created = updated = needs_review = 0
        for record in records:
            parsed = self._translated(record.parsed)
            receipt_id, was_created, review = self.repository.import_parsed_receipt(
                import_id,
                record.source_record_id,
                owner_chat_id,
                parsed,
                self.confidence_threshold,
            )
            receipt_ids.append(receipt_id)
            created += int(was_created)
            updated += int(not was_created)
            needs_review += int(review)
        return SlipAppImportResult(
            import_id=import_id,
            receipt_ids=tuple(receipt_ids),
            created=created,
            updated=updated,
            needs_review=needs_review,
            records=len(records),
            source_sha256=source_sha256,
        )
