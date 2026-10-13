from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from receipt_bot.category import HeuristicCategoryClassifier
from receipt_bot.input_parser import InputParseError, parse_input
from receipt_bot.slipapp import SlipAppImporter
from receipt_bot.translation import IdentityTranslator
from receipt_bot.warehouse import WarehouseRepository

from .test_slipapp import make_backup


def test_input_parser_pipe_and_free_text() -> None:
    assert parse_input("Lidl | Mleko | l | 2") == parse_input("Mleko | l | 2", "Lidl")
    draft = parse_input("shop: Lidl, apples 1.5 kg")
    assert draft.merchant_name_source == "Lidl"
    assert draft.product_name_source == "apples"
    assert draft.quantity == Decimal("1.5")
    assert draft.unit == "kg"
    try:
        parse_input("Mleko", "Lidl")
    except InputParseError:
        pass
    else:  # pragma: no cover
        raise AssertionError("invalid input was accepted")


def test_bronze_source_tables_and_silver_derivation(tmp_path: Path) -> None:
    backup = tmp_path / "slipapp.db"
    make_backup(backup)
    repository = WarehouseRepository(tmp_path / "warehouse.sqlite3")
    repository.initialise()
    result = SlipAppImporter(
        repository,
        IdentityTranslator(),
        classifier=HeuristicCategoryClassifier(),
    ).import_file(backup, 123)
    bronze_id = repository.add_bronze_input(
        "Lidl | Apples | kg | 1.5",
        "Lidl",
        "Apples",
        "kg",
        Decimal("1.5"),
        123,
        1,
    )
    repository.build_silver_input(bronze_id, IdentityTranslator(), HeuristicCategoryClassifier())
    counts = repository.layer_counts()
    assert result.records == counts["bronze_slipapp_receipts"] == counts["silver_receipts"]
    assert counts["bronze_input_entries"] == counts["silver_input_entries"] == 1
    with repository._connection() as connection:  # noqa: SLF001 - schema provenance assertion
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "bronze_slipapp_receipts" in tables
    assert "bronze_input_entries" in tables
    assert "silver_receipts" in tables
