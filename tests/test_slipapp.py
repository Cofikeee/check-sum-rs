from __future__ import annotations

import sqlite3
from pathlib import Path

from receipt_bot.db import ReceiptRepository
from receipt_bot.slipapp import SlipAppBackupReader, SlipAppFormatError, SlipAppImporter
from receipt_bot.translation import IdentityTranslator


def make_backup(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE slips (
            id INTEGER PRIMARY KEY, shop_name TEXT NOT NULL, shop_tax_id TEXT NOT NULL,
            shop_location_name TEXT NOT NULL, shop_administrative_unit TEXT NOT NULL,
            shop_city TEXT NOT NULL, shop_address TEXT NOT NULL, shop_cashier TEXT NOT NULL,
            buyer TEXT, slip_pfr TEXT NOT NULL, slip_esir TEXT, slip_time TEXT NOT NULL,
            slip_amount REAL NOT NULL, slip_type TEXT NOT NULL, slip_transaction_type TEXT NOT NULL,
            raw_slip TEXT NOT NULL, url TEXT NOT NULL
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY, name TEXT, price REAL, amount REAL, quantity REAL,
            tax_symbol TEXT, tax_name TEXT, slip_id INTEGER
        );
        CREATE TABLE payment_methods (id INTEGER PRIMARY KEY, amount REAL, name TEXT, code TEXT, slip_id INTEGER);
        CREATE TABLE taxes (id INTEGER PRIMARY KEY, symbol TEXT, name TEXT, rate REAL, amount REAL, slip_id INTEGER);
        INSERT INTO slips VALUES (1, 'ТЕСТ ПРОДАВНИЦА', '123', '1', 'БЕОГРАД', 'БЕОГРАД', 'Адреса 1', '1', NULL,
          'ABC-1', 'ESIR-1', '2026-10-11T14:51:20.140Z', 120.00, 'Promet', 'Prodaja', 'raw receipt', 'https://suf.purs.gov.rs/v/?vl=test');
        INSERT INTO products VALUES (1, 'Млеко/KOM/12345', 120.00, 120.00, 1.0, 'Е', 'P-PDV', 1);
        INSERT INTO payment_methods VALUES (1, 120.00, 'Gotovina', 'cash', 1);
        INSERT INTO taxes VALUES (1, 'Е', 'P-PDV', 10.0, 10.91, 1);
        """
    )
    connection.commit()
    connection.close()


def test_slipapp_reader_and_idempotent_import(tmp_path: Path) -> None:
    backup = tmp_path / "backup.db"
    make_backup(backup)

    records = SlipAppBackupReader().read(backup)
    assert len(records) == 1
    assert records[0].parsed.merchant_name_source == "ТЕСТ ПРОДАВНИЦА"
    assert records[0].parsed.items[0].name_source == "Млеко/KOM/12345"
    assert records[0].parsed.items[0].producer_source is None

    repository = ReceiptRepository(tmp_path / "receipts.sqlite3")
    repository.initialise()
    importer = SlipAppImporter(repository, IdentityTranslator())
    first = importer.import_file(backup, 77)
    second = importer.import_file(backup, 77)

    assert first.created == 1
    assert second.created == 0
    assert second.updated == 1
    assert repository.status_counts(77) == {"COMPLETED": 1}


def test_slipapp_reader_rejects_unrelated_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "other.db"
    sqlite3.connect(path).close()
    try:
        SlipAppBackupReader().read(path)
    except SlipAppFormatError as error:
        assert "missing tables" in str(error)
    else:  # pragma: no cover - assertion helper
        raise AssertionError("unrelated SQLite file was accepted")
