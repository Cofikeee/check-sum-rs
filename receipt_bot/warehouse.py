from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Iterable

from .category import CategoryClassifier
from .db import ReceiptRepository
from .models import ParsedReceipt


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _name_without_code(value: str) -> str:
    match = re.match(r"^(.*?)\s*/[^/]+/[^/]+\s*$", value)
    return match.group(1).strip() if match else value.strip()


def _merchant_display(value: str | None) -> str | None:
    """Return the canonical, non-translated company label used in silver."""
    if not value:
        return None
    cleaned = re.sub(
        r"D\s*\.?\s*O\s*\.?\s*O\s*\.?|NOVI\s+SAD|BEOGRAD|SERBIA|SRBIJA|\bRS\b|\bNS\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[,.;:/()_-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.upper() or None


WAREHOUSE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bronze_slipapp_imports (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    stored_path TEXT NOT NULL,
    original_filename TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bronze_slipapp_receipts (
    id INTEGER PRIMARY KEY,
    fiscal_receipt_number TEXT NOT NULL UNIQUE,
    qr_url TEXT UNIQUE,
    merchant_name_source TEXT,
    merchant_tax_id TEXT,
    merchant_location_name TEXT,
    merchant_administrative_unit TEXT,
    merchant_city TEXT,
    merchant_address TEXT,
    merchant_cashier TEXT,
    buyer TEXT,
    receipt_datetime TEXT,
    currency TEXT NOT NULL DEFAULT 'RSD',
    gross_total TEXT,
    tax_total TEXT,
    payment_method TEXT,
    fiscal_esir TEXT,
    receipt_type TEXT,
    transaction_type TEXT,
    payment_details TEXT NOT NULL DEFAULT '[]',
    tax_details TEXT NOT NULL DEFAULT '[]',
    raw_text TEXT NOT NULL DEFAULT '',
    source_payload TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bronze_slipapp_import_rows (
    import_id INTEGER NOT NULL REFERENCES bronze_slipapp_imports(id) ON DELETE CASCADE,
    source_record_id TEXT NOT NULL,
    bronze_receipt_id INTEGER NOT NULL REFERENCES bronze_slipapp_receipts(id) ON DELETE CASCADE,
    source_payload TEXT NOT NULL DEFAULT '{}',
    seen_at TEXT NOT NULL,
    PRIMARY KEY(import_id, source_record_id)
);
CREATE INDEX IF NOT EXISTS idx_bronze_slipapp_rows_receipt ON bronze_slipapp_import_rows(bronze_receipt_id);

CREATE TABLE IF NOT EXISTS bronze_slipapp_items (
    id INTEGER PRIMARY KEY,
    bronze_receipt_id INTEGER NOT NULL REFERENCES bronze_slipapp_receipts(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name_source TEXT NOT NULL,
    quantity TEXT,
    unit TEXT,
    unit_price TEXT,
    line_total TEXT,
    producer_source TEXT,
    tax_symbol TEXT,
    tax_name TEXT,
    source_payload TEXT NOT NULL DEFAULT '{}',
    UNIQUE(bronze_receipt_id, position)
);

CREATE TABLE IF NOT EXISTS bronze_input_entries (
    id INTEGER PRIMARY KEY,
    source_text TEXT NOT NULL,
    merchant_name_source TEXT,
    product_name_source TEXT NOT NULL,
    unit TEXT,
    quantity TEXT,
    source_chat_id BIGINT,
    source_message_id BIGINT,
    parser_metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS silver_merchants (
    id INTEGER PRIMARY KEY,
    name_source TEXT NOT NULL,
    name_en TEXT,
    name_normalized TEXT NOT NULL UNIQUE,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS silver_products (
    id INTEGER PRIMARY KEY,
    merchant_id INTEGER REFERENCES silver_merchants(id) ON DELETE SET NULL,
    merchant_name TEXT,
    name_source TEXT NOT NULL,
    name_en TEXT,
    name_normalized TEXT NOT NULL,
    category_wide TEXT NOT NULL DEFAULT 'other',
    category_narrow TEXT NOT NULL DEFAULT 'other',
    category_source TEXT NOT NULL DEFAULT 'heuristic',
    category_confidence REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(merchant_id, name_normalized)
);

CREATE TABLE IF NOT EXISTS silver_receipts (
    id INTEGER PRIMARY KEY,
    bronze_slipapp_receipt_id INTEGER NOT NULL UNIQUE REFERENCES bronze_slipapp_receipts(id) ON DELETE CASCADE,
    merchant_id INTEGER REFERENCES silver_merchants(id) ON DELETE SET NULL,
    merchant_name TEXT,
    receipt_datetime TEXT,
    currency TEXT NOT NULL DEFAULT 'RSD',
    gross_total TEXT,
    tax_total TEXT,
    payment_method TEXT,
    fiscal_receipt_number TEXT,
    fiscal_esir TEXT,
    source_status TEXT NOT NULL DEFAULT 'READY',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS silver_receipt_items (
    id INTEGER PRIMARY KEY,
    silver_receipt_id INTEGER NOT NULL REFERENCES silver_receipts(id) ON DELETE CASCADE,
    bronze_slipapp_item_id INTEGER NOT NULL UNIQUE REFERENCES bronze_slipapp_items(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    merchant_id INTEGER REFERENCES silver_merchants(id) ON DELETE SET NULL,
    merchant_name TEXT,
    product_id INTEGER REFERENCES silver_products(id) ON DELETE SET NULL,
    product_name TEXT,
    quantity TEXT,
    unit TEXT,
    unit_price TEXT,
    line_total TEXT,
    producer TEXT,
    category_wide TEXT NOT NULL DEFAULT 'other',
    category_narrow TEXT NOT NULL DEFAULT 'other',
    category_source TEXT NOT NULL DEFAULT 'heuristic',
    category_confidence REAL NOT NULL DEFAULT 0,
    UNIQUE(silver_receipt_id, position)
);

CREATE TABLE IF NOT EXISTS silver_input_entries (
    id INTEGER PRIMARY KEY,
    bronze_input_id INTEGER NOT NULL UNIQUE REFERENCES bronze_input_entries(id) ON DELETE CASCADE,
    merchant_id INTEGER REFERENCES silver_merchants(id) ON DELETE SET NULL,
    product_id INTEGER REFERENCES silver_products(id) ON DELETE SET NULL,
    merchant_name TEXT,
    product_name TEXT,
    quantity TEXT,
    unit TEXT,
    category_wide TEXT NOT NULL DEFAULT 'other',
    category_narrow TEXT NOT NULL DEFAULT 'other',
    category_source TEXT NOT NULL DEFAULT 'heuristic',
    category_confidence REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_translation_overrides (
    source_normalized TEXT PRIMARY KEY,
    source_text TEXT NOT NULL,
    english_text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_category_overrides (
    name_normalized TEXT PRIMARY KEY,
    category_wide TEXT NOT NULL,
    category_narrow TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_schema_versions (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);
"""


# Logical table names stay readable in SQLite tests.  In PostgreSQL they are
# placed in real bronze/silver/meta schemas so a DataGrip connection exposes
# the data layers as separate namespaces.
WAREHOUSE_TABLES = {
    "bronze_slipapp_imports": "bronze.slipapp_imports",
    "bronze_slipapp_receipts": "bronze.slipapp_receipts",
    "bronze_slipapp_import_rows": "bronze.slipapp_import_rows",
    "bronze_slipapp_items": "bronze.slipapp_items",
    "bronze_input_entries": "bronze.input_entries",
    "silver_merchants": "silver.merchants",
    "silver_products": "silver.products",
    "silver_receipts": "silver.slipapp_receipts",
    "silver_receipt_items": "silver.slipapp_items",
    "silver_input_entries": "silver.input_entries",
    "meta_translation_overrides": "meta.translation_overrides",
    "meta_category_overrides": "meta.category_overrides",
    "meta_schema_versions": "meta.schema_versions",
}

LEGACY_PUBLIC_TABLES = (
    "silver_category_overrides",
    "silver_input_entries",
    "silver_receipt_items",
    "silver_receipts",
    "silver_products",
    "silver_merchants",
    "bronze_input_entries",
    "bronze_slipapp_items",
    "bronze_slipapp_import_rows",
    "bronze_slipapp_receipts",
    "bronze_slipapp_imports",
    "review_events",
    "review_tasks",
    "receipt_qr_codes",
    "receipt_pages",
    "receipt_files",
    "receipt_items",
    "translation_overrides",
    "merchants",
    "receipt_import_receipts",
    "receipt_imports",
    "batch_sessions",
    "receipts",
)


class WarehouseRepository(ReceiptRepository):
    """Repository for the explicit bronze/silver/meta warehouse model.

    ``ReceiptRepository`` remains available for the old unit-test surface, but
    the running bot uses this class and therefore never creates the obsolete
    public ``receipts``/``receipt_files`` model.
    """

    def _warehouse_sql(self, query: str) -> str:
        if not self.is_postgres:
            return query
        # Replace only standalone SQL identifiers.  This avoids changing index
        # names such as ``idx_silver_receipts_merchant``.
        for logical, physical in sorted(WAREHOUSE_TABLES.items(), key=lambda item: -len(item[0])):
            query = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(logical)}(?![A-Za-z0-9_])",
                physical,
                query,
            )
        return query

    def _execute(self, connection: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
        return super()._execute(connection, self._warehouse_sql(query), params)

    def initialise(self) -> None:
        schema = self._warehouse_sql(WAREHOUSE_SCHEMA)
        if self.is_postgres:
            schema = re.sub(r"\bINTEGER PRIMARY KEY\b", "BIGSERIAL PRIMARY KEY", schema)
            schema = re.sub(r"\bINTEGER NOT NULL REFERENCES\b", "BIGINT NOT NULL REFERENCES", schema)
            schema = re.sub(r"\bINTEGER REFERENCES\b", "BIGINT REFERENCES", schema)
            schema = schema.replace("source_chat_id BIGINT", "source_chat_id BIGINT")
            schema = schema.replace("source_message_id BIGINT", "source_message_id BIGINT")
            with self._connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("CREATE SCHEMA IF NOT EXISTS bronze")
                    cursor.execute("CREATE SCHEMA IF NOT EXISTS silver")
                    cursor.execute("CREATE SCHEMA IF NOT EXISTS meta")
                    # The old public model is no longer used by the bot.  It
                    # is safe to remove it during this intentional warehouse
                    # reset; the user explicitly requested a clean Postgres
                    # database for the new model.
                    for table in LEGACY_PUBLIC_TABLES:
                        cursor.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
                    cursor.execute(schema)
        else:
            with self._connection() as connection:
                connection.executescript(schema)
        with self._connection() as connection:
            self._execute(
                connection,
                """INSERT INTO meta_schema_versions (component, version, applied_at)
                   VALUES (?, ?, ?) ON CONFLICT(component) DO UPDATE SET version = excluded.version,
                   applied_at = excluded.applied_at""",
                ("warehouse", 2, _now()),
            )

    def register_bronze_slipapp_import(
        self, path: Path, sha256: str, original_filename: str | None
    ) -> tuple[int, bool]:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT id FROM bronze_slipapp_imports WHERE sha256 = ?", (sha256,)).fetchone()
            if row:
                return int(row["id"]), False
            import_id = self._insert_id(
                connection,
                """INSERT INTO bronze_slipapp_imports
                   (sha256, stored_path, original_filename, imported_at) VALUES (?, ?, ?, ?)""",
                (sha256, str(path), original_filename, _now()),
            )
            return import_id, True

    def store_bronze_slipapp_records(self, import_id: int, records: Iterable[Any]) -> tuple[list[int], int]:
        receipt_ids: list[int] = []
        created = 0
        with self._connection() as connection:
            for record in records:
                parsed: ParsedReceipt = record.parsed
                source_payload = {
                    "source_record_id": record.source_record_id,
                    "qr_url": parsed.qr_url,
                    "raw_text": parsed.raw_text,
                    "merchant": parsed.merchant_name_source,
                    "merchant_tax_id": parsed.merchant_tax_id,
                    "items": [
                        {"position": item.position, "name": item.name_source, "quantity": _decimal(item.quantity),
                         "unit": item.unit, "unit_price": _decimal(item.unit_price), "line_total": _decimal(item.line_total),
                         "tax_symbol": item.tax_symbol, "tax_name": item.tax_name}
                        for item in parsed.items
                    ],
                }
                existing = self._execute(
                    connection,
                    """SELECT id FROM bronze_slipapp_receipts
                       WHERE fiscal_receipt_number = ? OR (qr_url IS NOT NULL AND qr_url = ?)
                       ORDER BY id LIMIT 1""",
                    (parsed.fiscal_receipt_number, parsed.qr_url),
                ).fetchone()
                timestamp = _now()
                if existing:
                    receipt_id = int(existing["id"])
                    self._execute(
                        connection,
                        "UPDATE bronze_slipapp_receipts SET last_seen_at = ?, source_payload = ? WHERE id = ?",
                        (timestamp, _json(source_payload), receipt_id),
                    )
                else:
                    created += 1
                    receipt_id = self._insert_id(
                        connection,
                        """INSERT INTO bronze_slipapp_receipts
                           (fiscal_receipt_number, qr_url, merchant_name_source, merchant_tax_id,
                            merchant_location_name, merchant_administrative_unit, merchant_city, merchant_address,
                            merchant_cashier, buyer, receipt_datetime, currency, gross_total, tax_total,
                            payment_method, fiscal_esir, receipt_type, transaction_type, payment_details,
                            tax_details, raw_text, source_payload, first_seen_at, last_seen_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            parsed.fiscal_receipt_number or f"source-{import_id}-{record.source_record_id}",
                            parsed.qr_url, parsed.merchant_name_source, parsed.merchant_tax_id,
                            parsed.merchant_location_name, parsed.merchant_administrative_unit, parsed.merchant_city,
                            parsed.merchant_address, parsed.merchant_cashier, parsed.buyer,
                            parsed.receipt_datetime.isoformat() if parsed.receipt_datetime else None,
                            parsed.currency, _decimal(parsed.gross_total), _decimal(parsed.tax_total),
                            parsed.payment_method, parsed.fiscal_esir, parsed.receipt_type, parsed.transaction_type,
                            _json(parsed.payment_details), _json(parsed.tax_details), parsed.raw_text,
                            _json(source_payload), timestamp, timestamp,
                        ),
                    )
                    for item in parsed.items:
                        item_payload = {
                            "source_record_id": record.source_record_id,
                            "position": item.position,
                            "name": item.name_source,
                            "quantity": _decimal(item.quantity),
                            "unit_price": _decimal(item.unit_price),
                            "line_total": _decimal(item.line_total),
                        }
                        self._insert_id(
                            connection,
                            """INSERT INTO bronze_slipapp_items
                               (bronze_receipt_id, position, name_source, quantity, unit, unit_price,
                                line_total, producer_source, tax_symbol, tax_name, source_payload)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                receipt_id, item.position, item.name_source, _decimal(item.quantity), item.unit,
                                _decimal(item.unit_price), _decimal(item.line_total), item.producer_source,
                                item.tax_symbol, item.tax_name, _json(item_payload),
                            ),
                        )
                self._execute(
                    connection,
                    """INSERT INTO bronze_slipapp_import_rows
                       (import_id, source_record_id, bronze_receipt_id, source_payload, seen_at)
                       VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                    (import_id, record.source_record_id, receipt_id, _json(source_payload), timestamp),
                )
                receipt_ids.append(receipt_id)
        return receipt_ids, created

    def bronze_slipapp_records(self, import_id: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = self._execute(
                connection,
                """SELECT DISTINCT r.* FROM bronze_slipapp_receipts r
                   JOIN bronze_slipapp_import_rows l ON l.bronze_receipt_id = r.id
                   WHERE l.import_id = ? ORDER BY r.id""",
                (import_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def _merchant_id(self, connection: Any, name_source: str | None, name_en: str | None) -> int | None:
        if not name_source:
            return None
        display = _merchant_display(name_source)
        if not display:
            return None
        normalized = _norm(display)
        row = self._execute(connection, "SELECT id FROM silver_merchants WHERE name_normalized = ?", (normalized,)).fetchone()
        if row:
            self._execute(
                connection,
                "UPDATE silver_merchants SET name_source = ?, name_en = ?, updated_at = ? WHERE id = ?",
                (display, display, _now(), row["id"]),
            )
            return int(row["id"])
        return self._insert_id(
            connection,
            "INSERT INTO silver_merchants (name_source, name_en, name_normalized, updated_at) VALUES (?, ?, ?, ?)",
            (display, display, normalized, _now()),
        )

    def get_translation_override(self, source_text: str) -> str | None:
        with self._connection() as connection:
            row = self._execute(
                connection,
                "SELECT english_text FROM meta_translation_overrides WHERE source_normalized = ?",
                (_norm(source_text),),
            ).fetchone()
            return str(row["english_text"]) if row else None

    def save_translation_override(self, source_text: str, english_text: str) -> None:
        with self._connection() as connection:
            self._execute(
                connection,
                """INSERT INTO meta_translation_overrides
                   (source_normalized, source_text, english_text, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(source_normalized) DO UPDATE SET source_text = excluded.source_text,
                   english_text = excluded.english_text, updated_at = excluded.updated_at""",
                (_norm(source_text), source_text, english_text, _now()),
            )

    def _category(self, connection: Any, name_source: str, merchant_source: str | None, classifier: CategoryClassifier) -> tuple[str, str, str, float]:
        override = self._execute(connection, "SELECT category_wide, category_narrow FROM meta_category_overrides WHERE name_normalized = ?", (_norm(name_source),)).fetchone()
        if override:
            return str(override["category_wide"]), str(override["category_narrow"]), "manual", 1.0
        result = classifier.classify(_name_without_code(name_source), merchant_source)
        return result.wide, result.narrow, result.source, result.confidence

    def _product_id(
        self, connection: Any, merchant_id: int | None, merchant_source: str | None,
        name_source: str, name_en: str | None, classifier: CategoryClassifier,
    ) -> tuple[int, tuple[str, str, str, float]]:
        category = self._category(connection, name_source, merchant_source, classifier)
        normalized = _norm(name_source)
        row = self._execute(
            connection,
            "SELECT id FROM silver_products WHERE merchant_id IS NOT DISTINCT FROM ? AND name_normalized = ?",
            (merchant_id, normalized),
        ).fetchone()
        if row:
            self._execute(
                connection,
                """UPDATE silver_products SET name_en = COALESCE(?, name_en), merchant_name = ?, category_wide = ?,
                   category_narrow = ?, category_source = ?, category_confidence = ?, updated_at = ? WHERE id = ?""",
                (name_en, merchant_source, category[0], category[1], category[2], category[3], _now(), row["id"]),
            )
            return int(row["id"]), category
        product_id = self._insert_id(
            connection,
            """INSERT INTO silver_products
               (merchant_id, merchant_name, name_source, name_en, name_normalized, category_wide, category_narrow,
                category_source, category_confidence, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (merchant_id, merchant_source, name_source, name_en, normalized, category[0], category[1], category[2], category[3], _now()),
        )
        return product_id, category

    def build_silver_slipapp(self, import_id: int, translator: Any, classifier: CategoryClassifier) -> int:
        """Rebuild silver rows for all rows from one bronze import."""
        count = 0
        with self._connection() as connection:
            rows = self._execute(
                connection,
                """SELECT DISTINCT r.* FROM bronze_slipapp_receipts r
                   JOIN bronze_slipapp_import_rows l ON l.bronze_receipt_id = r.id
                   WHERE l.import_id = ? ORDER BY r.id""",
                (import_id,),
            ).fetchall()
            for row in rows:
                merchant_en = _merchant_display(row["merchant_name_source"])
                merchant_id = self._merchant_id(connection, row["merchant_name_source"], merchant_en)
                existing = self._execute(connection, "SELECT id FROM silver_receipts WHERE bronze_slipapp_receipt_id = ?", (row["id"],)).fetchone()
                if existing:
                    silver_id = int(existing["id"])
                    self._execute(connection, "DELETE FROM silver_receipt_items WHERE silver_receipt_id = ?", (silver_id,))
                    self._execute(
                        connection,
                        """UPDATE silver_receipts SET merchant_id = ?, merchant_name = ?, receipt_datetime = ?, currency = ?,
                           gross_total = ?, tax_total = ?, payment_method = ?, fiscal_receipt_number = ?, fiscal_esir = ?,
                           source_status = 'READY', updated_at = ? WHERE id = ?""",
                        (merchant_id, merchant_en, row["receipt_datetime"], row["currency"], row["gross_total"], row["tax_total"],
                         row["payment_method"], row["fiscal_receipt_number"], row["fiscal_esir"], _now(), silver_id),
                    )
                else:
                    silver_id = self._insert_id(
                        connection,
                        """INSERT INTO silver_receipts
                           (bronze_slipapp_receipt_id, merchant_id, merchant_name, receipt_datetime, currency,
                            gross_total, tax_total, payment_method, fiscal_receipt_number, fiscal_esir, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (row["id"], merchant_id, merchant_en, row["receipt_datetime"], row["currency"], row["gross_total"],
                         row["tax_total"], row["payment_method"], row["fiscal_receipt_number"], row["fiscal_esir"], _now()),
                    )
                items = self._execute(connection, "SELECT * FROM bronze_slipapp_items WHERE bronze_receipt_id = ? ORDER BY position", (row["id"],)).fetchall()
                for item in items:
                    name_en = translator.translate(_name_without_code(item["name_source"]))
                    suffix_match = re.search(r"(/[^/]+/[^/]+)\s*$", item["name_source"])
                    if suffix_match:
                        name_en += suffix_match.group(1)
                    product_id, category = self._product_id(connection, merchant_id, row["merchant_name_source"], item["name_source"], name_en, classifier)
                    self._insert_id(
                        connection,
                        """INSERT INTO silver_receipt_items
                           (silver_receipt_id, bronze_slipapp_item_id, position, merchant_id, merchant_name,
                            product_id, product_name,
                            quantity, unit, unit_price, line_total, producer, category_wide, category_narrow,
                            category_source, category_confidence)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (silver_id, item["id"], item["position"], merchant_id, merchant_en,
                         product_id, name_en, item["quantity"], item["unit"],
                         item["unit_price"], item["line_total"], item["producer_source"], category[0], category[1], category[2], category[3]),
                    )
                count += 1
        return count

    def add_bronze_input(
        self, source_text: str, merchant_name_source: str | None, product_name_source: str,
        unit: str | None, quantity: Decimal | None, source_chat_id: int | None = None,
        source_message_id: int | None = None, parser_metadata: dict[str, Any] | None = None,
    ) -> int:
        with self._connection() as connection:
            return self._insert_id(
                connection,
                """INSERT INTO bronze_input_entries
                   (source_text, merchant_name_source, product_name_source, unit, quantity,
                    source_chat_id, source_message_id, parser_metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_text, merchant_name_source, product_name_source, unit, _decimal(quantity), source_chat_id,
                 source_message_id, _json(parser_metadata or {}), _now()),
            )

    def build_silver_input(self, bronze_input_id: int, translator: Any, classifier: CategoryClassifier) -> int:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT * FROM bronze_input_entries WHERE id = ?", (bronze_input_id,)).fetchone()
            if row is None:
                raise KeyError(f"bronze_input_entries {bronze_input_id} does not exist")
            merchant_en = _merchant_display(row["merchant_name_source"])
            merchant_id = self._merchant_id(connection, row["merchant_name_source"], merchant_en)
            category = self._category(connection, row["product_name_source"], row["merchant_name_source"], classifier)
            product_name = translator.translate(row["product_name_source"])
            product_id, _ = self._product_id(
                connection,
                merchant_id,
                row["merchant_name_source"],
                row["product_name_source"],
                product_name,
                classifier,
            )
            existing = self._execute(connection, "SELECT id FROM silver_input_entries WHERE bronze_input_id = ?", (bronze_input_id,)).fetchone()
            params = (merchant_id, product_id, merchant_en, product_name, row["quantity"], row["unit"], category[0], category[1], category[2], category[3])
            if existing:
                self._execute(connection, """UPDATE silver_input_entries SET merchant_id = ?, product_id = ?, merchant_name = ?, product_name = ?,
                    quantity = ?, unit = ?, category_wide = ?, category_narrow = ?, category_source = ?, category_confidence = ?
                    WHERE id = ?""", (*params, existing["id"]))
                return int(existing["id"])
            return self._insert_id(
                connection,
                """INSERT INTO silver_input_entries
                   (bronze_input_id, merchant_id, product_id, merchant_name, product_name, quantity, unit,
                    category_wide, category_narrow, category_source, category_confidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (bronze_input_id, *params, _now()),
            )

    def merchant_choices(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = self._execute(connection, "SELECT id, name_source, name_en FROM silver_merchants ORDER BY COALESCE(name_en, name_source) LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def merchant_name(self, merchant_id: int) -> str | None:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT name_source FROM silver_merchants WHERE id = ?", (merchant_id,)).fetchone()
            return str(row["name_source"]) if row else None

    def save_category_override(self, name_source: str, wide: str, narrow: str) -> None:
        with self._connection() as connection:
            self._execute(connection, """INSERT INTO meta_category_overrides (name_normalized, category_wide, category_narrow, updated_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(name_normalized) DO UPDATE SET category_wide = excluded.category_wide,
                category_narrow = excluded.category_narrow, updated_at = excluded.updated_at""", (_norm(name_source), wide, narrow, _now()))

    def layer_counts(self) -> dict[str, int]:
        with self._connection() as connection:
            queries = {
                "bronze_slipapp_receipts": "SELECT COUNT(*) AS n FROM bronze_slipapp_receipts",
                "bronze_input_entries": "SELECT COUNT(*) AS n FROM bronze_input_entries",
                "silver_receipts": "SELECT COUNT(*) AS n FROM silver_receipts",
                "silver_receipt_items": "SELECT COUNT(*) AS n FROM silver_receipt_items",
                "silver_input_entries": "SELECT COUNT(*) AS n FROM silver_input_entries",
            }
            return {name: int(self._execute(connection, query).fetchone()["n"]) for name, query in queries.items()}

    def bronze_slipapp_import_ids(self) -> list[int]:
        with self._connection() as connection:
            rows = self._execute(connection, "SELECT id FROM bronze_slipapp_imports ORDER BY id").fetchall()
            return [int(row["id"]) for row in rows]
