from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from .models import OcrLine, ParsedReceipt, ReceiptItemDraft


DATE_PATTERN = re.compile(
    r"\b(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{4})(?:\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?))?\b"
)
MONEY_PATTERN = re.compile(r"(?<!\d)(?:\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,\d{2}|\.\d{2})(?!\d)")
ITEM_PATTERN = re.compile(
    r"^(?P<name>.*?\D)\s+(?P<quantity>\d+(?:[,.]\d+)?)\s+(?P<amount>(?:\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,\d{2}|\.\d{2}))\s*$"
)
FISCAL_PATTERN = re.compile(r"(?:BROJ|БРОЈ|BR\.?|БР\.?|РAЧУН|РАЧУН|RAČUN)\s*[:#]?\s*([A-ZА-Я0-9/-]{5,})", re.IGNORECASE)
PRODUCER_PATTERN = re.compile(
    r"(?:PROIZVOĐAČ|PROIZVODJAC|ПРОИЗВОЂАЧ|PRODUCER)\s*[:\-]\s*(.+)$",
    re.IGNORECASE,
)

TOTAL_MARKERS = (
    "UKUPNO",
    "UKUPAN IZNOS",
    "ZA UPLATU",
    "TOTAL",
    "УКУПНО",
    "УКУПАН ИЗНОС",
)
NON_MERCHANT_MARKERS = (
    "FISKALNI",
    "ФИСКАЛНИ",
    "RAČUN",
    "RACUN",
    "РАЧУН",
    "ПИБ",
    "PIB",
    "MB:",
    "KASA",
    "DATUM",
    "ВРЕМЕ",
    "DATE",
)
NON_ITEM_MARKERS = TOTAL_MARKERS + (
    "GOTOVINA",
    "KARTICA",
    "POREZ",
    "PDV",
    "IZNOS",
    "ПОРЕЗ",
    "ПЛАЋАЊЕ",
)


def parse_decimal(value: str) -> Decimal | None:
    value = value.strip().replace(" ", "")
    if not value:
        return None
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif value.count(".") > 1:
        value = value.replace(".", "")
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


class SerbianReceiptParser:
    """Conservative layout-independent parser for common Serbian fiscal receipts."""

    def parse(self, lines: Iterable[OcrLine], language: str = "unknown") -> ParsedReceipt:
        ordered = [line for line in lines if line.text.strip()]
        text_lines = [line.text.strip() for line in ordered]
        raw_text = "\n".join(text_lines)
        merchant = self._merchant(ordered)
        receipt_datetime = self._date(raw_text)
        total = self._total(ordered)
        items = self._items(ordered)
        fiscal = self._fiscal_number(raw_text)
        tax_total = self._tax_total(ordered)
        warnings: list[str] = []

        if merchant is None:
            warnings.append("Merchant could not be identified")
        if receipt_datetime is None:
            warnings.append("Receipt date could not be identified")
        if total is None:
            warnings.append("Receipt total could not be identified")
        if not items:
            warnings.append("No receipt item lines could be identified")
        elif total is not None:
            item_total = sum((item.line_total or Decimal("0")) for item in items)
            if abs(item_total - total[0]) > Decimal("0.05"):
                warnings.append(
                    f"Item total {item_total} does not match receipt total {total[0]}"
                )

        confidences = [line.confidence for line in ordered]
        return ParsedReceipt(
            merchant_name_source=merchant[0] if merchant else None,
            receipt_datetime=receipt_datetime,
            currency=self._currency(raw_text),
            gross_total=total[0] if total else None,
            tax_total=tax_total[0] if tax_total else None,
            payment_method=self._payment_method(raw_text),
            fiscal_receipt_number=fiscal,
            items=items,
            raw_text=raw_text,
            ocr_language=language,
            confidence=sum(confidences) / len(confidences) if confidences else 0.0,
            validation_warnings=warnings,
        )

    def _merchant(self, lines: list[OcrLine]) -> tuple[str, float] | None:
        for line in lines[:10]:
            normalized = line.text.upper()
            if (
                len(line.text.strip()) >= 3
                and not re.fullmatch(r"\d+", line.text.strip())
                and not any(marker in normalized for marker in NON_MERCHANT_MARKERS)
                and not DATE_PATTERN.search(line.text)
                and not MONEY_PATTERN.search(line.text)
            ):
                return line.text.strip(), line.confidence
        return None

    @staticmethod
    def _date(raw_text: str) -> datetime | None:
        match = DATE_PATTERN.search(raw_text)
        if not match:
            return None
        time = match.group("time") or "00:00"
        value = f"{match.group('date')} {time}"
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _total(lines: list[OcrLine]) -> tuple[Decimal, float] | None:
        candidates: list[tuple[Decimal, float]] = []
        for index, line in enumerate(lines):
            upper = line.text.upper()
            if "POREZ" in upper or "ПОРЕЗ" in upper:
                continue
            if any(marker in upper for marker in TOTAL_MARKERS):
                values = MONEY_PATTERN.findall(line.text)
                for following in lines[index + 1:index + 3]:
                    values.extend(MONEY_PATTERN.findall(following.text))
                if values:
                    value = parse_decimal(values[-1])
                    if value is not None:
                        candidates.append((value, line.confidence))
        return candidates[-1] if candidates else None

    @staticmethod
    def _fiscal_number(raw_text: str) -> str | None:
        lines = raw_text.splitlines()
        for index, line in enumerate(lines):
            upper = line.upper()
            if "ПФР БРОЈ" not in upper and "PFR BROJ" not in upper:
                continue
            for following in lines[index + 1:index + 3]:
                candidate = following.strip().strip(":#")
                if re.fullmatch(r"[A-ZА-Я0-9/-]{5,}", candidate, re.IGNORECASE) and not candidate.isdigit():
                    return candidate
        for match in FISCAL_PATTERN.finditer(raw_text):
            candidate = match.group(1)
            if not candidate.isdigit() and candidate.upper() not in {"РАЧУНА", "RACUNA"}:
                return candidate
        return None

    @staticmethod
    def _currency(raw_text: str) -> str:
        upper = raw_text.upper()
        if "EUR" in upper or "€" in upper:
            return "EUR"
        return "RSD"

    @staticmethod
    def _payment_method(raw_text: str) -> str | None:
        upper = raw_text.upper()
        if "KARTICA" in upper or "КАРТИЦА" in upper or "CARD" in upper:
            return "card"
        if "GOTOVINA" in upper or "CASH" in upper:
            return "cash"
        if "VIRMAN" in upper or "TRANSFER" in upper:
            return "bank_transfer"
        return None

    @staticmethod
    def _tax_total(lines: list[OcrLine]) -> tuple[Decimal, float] | None:
        candidates: list[tuple[Decimal, float]] = []
        for index, line in enumerate(lines):
            upper = line.text.upper()
            if "PDV" not in upper and "POREZ" not in upper and "ПОРЕЗ" not in upper:
                continue
            values = MONEY_PATTERN.findall(line.text)
            if not values:
                for following in lines[index + 1:index + 3]:
                    values.extend(MONEY_PATTERN.findall(following.text))
                    if values:
                        break
            if values and (amount := parse_decimal(values[0])) is not None:
                candidates.append((amount, line.confidence))
        return candidates[-1] if candidates else None

    def _items(self, lines: list[OcrLine]) -> list[ReceiptItemDraft]:
        items: list[ReceiptItemDraft] = []
        body = self._item_body(lines)
        for index, line in enumerate(body):
            normalized = line.text.upper()
            producer = PRODUCER_PATTERN.search(line.text.strip())
            if producer and items:
                items[-1].producer_source = producer.group(1).strip()
                continue
            if any(marker in normalized for marker in NON_ITEM_MARKERS):
                continue
            match = ITEM_PATTERN.match(line.text.strip())
            if match:
                name = match.group("name").strip(" -:")
                quantity = parse_decimal(match.group("quantity"))
                line_total = parse_decimal(match.group("amount"))
                if name and quantity is not None and line_total is not None:
                    items.append(ReceiptItemDraft(
                        position=len(items) + 1, name_source=name, quantity=quantity,
                        unit="pcs", unit_price=(line_total / quantity) if quantity else None,
                        line_total=line_total, confidence=line.confidence,
                    ))
                continue

            # Serbian fiscal printers commonly put the product name and unit
            # on one line, then print price, quantity and line total as three
            # separate OCR boxes. Consume that five-token layout conservatively.
            if self._numeric(line.text) is not None or self._unit_only(line.text):
                continue
            unit = "kg" if re.search(r"\bKG\b", line.text, re.IGNORECASE) else "pcs"
            name = re.sub(r"\s+(?:KOM|KG)\s*\([A-ZА-Я]\)\s*$", "", line.text.strip(), flags=re.IGNORECASE)
            cursor = index + 1
            if cursor < len(body) and self._unit_only(body[cursor].text):
                unit = "kg" if body[cursor].text.upper().startswith("KG") else "pcs"
                cursor += 1
            values: list[tuple[Decimal, float]] = []
            while cursor < len(body) and len(values) < 3:
                number = self._numeric(body[cursor].text)
                if number is None:
                    break
                values.append((number, body[cursor].confidence))
                cursor += 1
            if len(name) >= 2 and len(values) == 3:
                price, quantity, total = (value[0] for value in values)
                items.append(ReceiptItemDraft(
                    position=len(items) + 1, name_source=name, quantity=quantity,
                    unit=unit, unit_price=price, line_total=total,
                    confidence=line.confidence,
                ))
        return items

    @staticmethod
    def _item_body(lines: list[OcrLine]) -> list[OcrLine]:
        start = 0
        for index, line in enumerate(lines):
            upper = line.text.upper()
            if "АРТИКЛ" in upper or "ARTIKL" in upper:
                start = index + 1
                break
        body: list[OcrLine] = []
        for line in lines[start:]:
            upper = line.text.upper()
            if "УКУПАН ИЗНОС" in upper or "UKUPAN IZNOS" in upper or "ZA UPLATU" in upper or "TOTAL" in upper:
                break
            body.append(line)
        return body

    @staticmethod
    def _numeric(value: str) -> Decimal | None:
        value = value.strip()
        if not re.fullmatch(r"\d+(?:[,.]\d+)?", value):
            return None
        return parse_decimal(value)

    @staticmethod
    def _unit_only(value: str) -> bool:
        return bool(re.fullmatch(r"(?:KOM|KG)\s*\([A-ZА-Я]\)", value.strip(), re.IGNORECASE))
