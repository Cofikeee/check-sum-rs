from datetime import datetime
from decimal import Decimal

from receipt_bot.models import OcrLine
from receipt_bot.parser import SerbianReceiptParser, parse_decimal


def lines(*values: str, confidence: float = 0.98) -> list[OcrLine]:
    return [OcrLine(value, confidence, 1) for value in values]


def test_parses_common_latin_fiscal_receipt() -> None:
    parsed = SerbianReceiptParser().parse(lines(
        "MAXI 123",
        "Bulevar oslobodjenja 10",
        "Datum 13.10.2026 14:32",
        "MLEKO 1L 2 239,98",
        "HLEB BELI 1 79,99",
        "PDV 26,66",
        "KARTICA",
        "UKUPNO 319,97",
    ), "rs_latin")

    assert parsed.merchant_name_source == "MAXI 123"
    assert parsed.receipt_datetime == datetime(2026, 10, 13, 14, 32)
    assert parsed.gross_total == Decimal("319.97")
    assert [item.name_source for item in parsed.items] == ["MLEKO 1L", "HLEB BELI"]
    assert parsed.items[0].unit_price == Decimal("119.99")
    assert parsed.tax_total == Decimal("26.66")
    assert parsed.payment_method == "card"
    assert parsed.validation_warnings == []


def test_parses_cyrillic_and_marks_total_mismatch() -> None:
    parsed = SerbianReceiptParser().parse(lines(
        "ИДЕА",
        "Датум 13.10.2026 09:15",
        "ХЛЕБ 1 80,00",
        "УКУПНО 100,00",
    ), "rs_cyrillic")

    assert parsed.merchant_name_source == "ИДЕА"
    assert parsed.items[0].line_total == Decimal("80.00")
    assert any("does not match" in warning for warning in parsed.validation_warnings)


def test_assigns_explicit_producer_to_preceding_item() -> None:
    parsed = SerbianReceiptParser().parse(lines(
        "DISKONT",
        "13.10.2026",
        "SOK 1 100,00",
        "PROIZVODJAC: NEKTAR",
        "UKUPNO 100,00",
    ))
    assert parsed.items[0].producer_source == "NEKTAR"


def test_serbian_money_normalisation() -> None:
    assert parse_decimal("1.234,56") == Decimal("1234.56")
    assert parse_decimal("1234.56") == Decimal("1234.56")
    assert parse_decimal("not money") is None
