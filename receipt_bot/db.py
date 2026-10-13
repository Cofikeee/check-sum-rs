from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator

from .models import ParsedReceipt, StoredFile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise(value: str) -> str:
    return " ".join(value.upper().split())


SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY,
    owner_chat_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('QUEUED','PROCESSING','COMPLETED','NEEDS_REVIEW','FAILED')),
    merchant_name_source TEXT,
    merchant_name_en TEXT,
    receipt_datetime TEXT,
    currency TEXT NOT NULL DEFAULT 'RSD',
    gross_total TEXT,
    tax_total TEXT,
    payment_method TEXT,
    fiscal_receipt_number TEXT,
    source_text TEXT NOT NULL DEFAULT '',
    ocr_language TEXT,
    confidence REAL,
    validation_warnings TEXT NOT NULL DEFAULT '[]',
    error_message TEXT,
    qr_url TEXT,
    qr_payload TEXT,
    verification_response TEXT,
    verification_content_type TEXT,
    verification_http_status INTEGER,
    source_method TEXT NOT NULL DEFAULT 'qr_verification',
    merchant_tax_id TEXT,
    merchant_location_name TEXT,
    merchant_administrative_unit TEXT,
    merchant_city TEXT,
    merchant_address TEXT,
    merchant_cashier TEXT,
    buyer TEXT,
    fiscal_esir TEXT,
    receipt_type TEXT,
    transaction_type TEXT,
    payment_details TEXT NOT NULL DEFAULT '[]',
    tax_details TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_sessions (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('ACTIVE','FINISHED','CANCELLED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batch_active ON batch_sessions(chat_id, state);

CREATE TABLE IF NOT EXISTS receipt_files (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER REFERENCES receipts(id) ON DELETE CASCADE,
    batch_session_id INTEGER REFERENCES batch_sessions(id) ON DELETE SET NULL,
    stored_path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    original_filename TEXT,
    mime_type TEXT NOT NULL,
    telegram_file_id TEXT,
    telegram_message_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipt_files_receipt ON receipt_files(receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipt_files_batch ON receipt_files(batch_session_id);

CREATE TABLE IF NOT EXISTS receipt_pages (
    id INTEGER PRIMARY KEY,
    receipt_file_id INTEGER NOT NULL REFERENCES receipt_files(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    rendered_path TEXT,
    embedded_text TEXT,
    ocr_text TEXT,
    ocr_confidence REAL,
    qr_payloads TEXT NOT NULL DEFAULT '[]',
    UNIQUE(receipt_file_id, page_number)
);

CREATE TABLE IF NOT EXISTS receipt_qr_codes (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    receipt_file_id INTEGER NOT NULL REFERENCES receipt_files(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    payload TEXT NOT NULL,
    url TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(receipt_file_id, page_number, payload)
);
CREATE INDEX IF NOT EXISTS idx_receipt_qr_receipt ON receipt_qr_codes(receipt_id);

-- A SlipApp backup contains many receipts in one SQLite file.  Keep the
-- backup as one immutable import source and link each source row to the
-- normalized receipt it produced instead of pretending the whole database is
-- a single receipt file.
CREATE TABLE IF NOT EXISTS receipt_imports (
    id INTEGER PRIMARY KEY,
    source_app TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    original_filename TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS receipt_import_receipts (
    import_id INTEGER NOT NULL REFERENCES receipt_imports(id) ON DELETE CASCADE,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    source_record_id TEXT NOT NULL,
    PRIMARY KEY (import_id, receipt_id),
    UNIQUE (import_id, source_record_id)
);
CREATE INDEX IF NOT EXISTS idx_receipt_import_receipts_receipt ON receipt_import_receipts(receipt_id);

CREATE TABLE IF NOT EXISTS merchants (
    id INTEGER PRIMARY KEY,
    name_source TEXT NOT NULL,
    name_normalized TEXT NOT NULL UNIQUE,
    name_en TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS receipt_items (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name_source TEXT NOT NULL,
    name_en TEXT,
    producer_source TEXT,
    producer_en TEXT,
    quantity TEXT,
    unit TEXT,
    unit_price TEXT,
    line_total TEXT,
    confidence REAL NOT NULL,
    tax_symbol TEXT,
    tax_name TEXT,
    UNIQUE(receipt_id, position)
);
CREATE INDEX IF NOT EXISTS idx_receipt_items_receipt ON receipt_items(receipt_id);

CREATE TABLE IF NOT EXISTS translation_overrides (
    source_normalized TEXT PRIMARY KEY,
    source_text TEXT NOT NULL,
    english_text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_tasks (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    field_key TEXT NOT NULL,
    prompt TEXT NOT NULL,
    current_value TEXT,
    state TEXT NOT NULL CHECK(state IN ('OPEN','NOTIFIED','AWAITING_RESPONSE','RESOLVED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_tasks_queue ON review_tasks(chat_id, state, id);

CREATE TABLE IF NOT EXISTS review_events (
    id INTEGER PRIMARY KEY,
    review_task_id INTEGER NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    value TEXT,
    created_at TEXT NOT NULL
);
"""


class ReceiptRepository:
    """Repository backed by PostgreSQL in production and SQLite in unit tests.

    Passing a PostgreSQL URL is mandatory for the bot.  A Path is retained as
    a small test-only compatibility mode so deterministic tests do not need a
    second database server.
    """

    def __init__(self, database: str | Path) -> None:
        self.database_url = str(database) if isinstance(database, str) and database.startswith("postgres") else None
        self.database_path = Path(database) if self.database_url is None else None

    @property
    def is_postgres(self) -> bool:
        return self.database_url is not None

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as error:  # pragma: no cover - Docker supplies it
                raise RuntimeError("psycopg is not installed") from error
            connection = psycopg.connect(self.database_url, row_factory=dict_row)
        else:
            assert self.database_path is not None
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _sql(self, query: str) -> str:
        return query.replace("?", "%s") if self.is_postgres else query

    def _execute(self, connection: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
        return connection.execute(self._sql(query), params)

    def _executemany(self, connection: Any, query: str, params: list[tuple[Any, ...]]) -> Any:
        if self.is_postgres:
            with connection.cursor() as cursor:
                return cursor.executemany(self._sql(query), params)
        return connection.executemany(self._sql(query), params)

    def _insert_id(self, connection: Any, query: str, params: tuple[Any, ...]) -> int:
        if self.is_postgres:
            row = self._execute(connection, query + " RETURNING id", params).fetchone()
            assert row is not None
            return int(row["id"])
        return int(self._execute(connection, query, params).lastrowid)

    def initialise(self) -> None:
        if self.is_postgres:
            schema = re.sub(r"\bINTEGER PRIMARY KEY\b", "BIGSERIAL PRIMARY KEY", SCHEMA)
            schema = re.sub(r"\bINTEGER NOT NULL REFERENCES\b", "BIGINT NOT NULL REFERENCES", schema)
            schema = re.sub(r"\bINTEGER REFERENCES\b", "BIGINT REFERENCES", schema)
            schema = schema.replace("owner_chat_id INTEGER", "owner_chat_id BIGINT")
            schema = schema.replace("chat_id INTEGER", "chat_id BIGINT")
            schema = schema.replace("telegram_message_id INTEGER", "telegram_message_id BIGINT")
            try:
                import psycopg
            except ImportError as error:  # pragma: no cover
                raise RuntimeError("psycopg is not installed") from error
            with self._connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(schema)
                    # The production database may have been initialized by an
                    # older bot image.  Keep this migration additive and
                    # restart-safe.
                    for statement in (
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS merchant_tax_id TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS merchant_location_name TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS merchant_administrative_unit TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS merchant_city TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS merchant_address TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS merchant_cashier TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS buyer TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS fiscal_esir TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS receipt_type TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS transaction_type TEXT",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS payment_details TEXT NOT NULL DEFAULT '[]'",
                        "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS tax_details TEXT NOT NULL DEFAULT '[]'",
                        "ALTER TABLE receipt_items ADD COLUMN IF NOT EXISTS tax_symbol TEXT",
                        "ALTER TABLE receipt_items ADD COLUMN IF NOT EXISTS tax_name TEXT",
                    ):
                        cursor.execute(statement)
            return
        assert self.database_path is not None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(SCHEMA)

    def add_file(
        self, path: Path, sha256: str, mime_type: str, original_filename: str | None,
        telegram_file_id: str | None, telegram_message_id: int | None,
    ) -> tuple[StoredFile, bool]:
        with self._connection() as connection:
            existing = self._execute(connection, "SELECT * FROM receipt_files WHERE sha256 = ?", (sha256,)).fetchone()
            if existing:
                return self._stored_file(existing), False
            file_id = self._insert_id(
                connection,
                """INSERT INTO receipt_files
                   (stored_path, sha256, original_filename, mime_type, telegram_file_id,
                    telegram_message_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(path), sha256, original_filename, mime_type, telegram_file_id, telegram_message_id, _now()),
            )
            row = self._execute(connection, "SELECT * FROM receipt_files WHERE id = ?", (file_id,)).fetchone()
            assert row is not None
            return self._stored_file(row), True

    def register_import(
        self, source_app: str, path: Path, sha256: str, original_filename: str | None
    ) -> tuple[int, bool]:
        """Register an immutable multi-receipt import source.

        Returns ``(id, inserted)``.  A repeated upload of the same backup can
        therefore resume safely without creating another set of receipts.
        """
        with self._connection() as connection:
            existing = self._execute(
                connection, "SELECT id FROM receipt_imports WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if existing is not None:
                return int(existing["id"]), False
            import_id = self._insert_id(
                connection,
                """INSERT INTO receipt_imports
                   (source_app, stored_path, sha256, original_filename, imported_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (source_app, str(path), sha256, original_filename, _now()),
            )
            return import_id, True

    def import_parsed_receipt(
        self,
        import_id: int,
        source_record_id: str,
        owner_chat_id: int,
        parsed: ParsedReceipt,
        threshold: float = 0.90,
    ) -> tuple[int, bool, bool]:
        """Upsert one parsed receipt and link it to a backup source row.

        The fiscal verification URL and fiscal receipt number are stable
        identifiers.  They make imports idempotent even when a receipt was
        previously entered through Telegram or another backup.
        """
        with self._connection() as connection:
            linked = self._execute(
                connection,
                """SELECT receipt_id FROM receipt_import_receipts
                   WHERE import_id = ? AND source_record_id = ?""",
                (import_id, source_record_id),
            ).fetchone()
            if linked is not None:
                return int(linked["receipt_id"]), False, False

            existing = None
            if parsed.qr_url or parsed.fiscal_receipt_number:
                if parsed.qr_url and parsed.fiscal_receipt_number:
                    existing = self._execute(
                        connection,
                        """SELECT id FROM receipts
                           WHERE qr_url = ? OR fiscal_receipt_number = ?
                           ORDER BY id LIMIT 1""",
                        (parsed.qr_url, parsed.fiscal_receipt_number),
                    ).fetchone()
                elif parsed.qr_url:
                    existing = self._execute(
                        connection, "SELECT id FROM receipts WHERE qr_url = ? ORDER BY id LIMIT 1", (parsed.qr_url,)
                    ).fetchone()
                else:
                    existing = self._execute(
                        connection,
                        "SELECT id FROM receipts WHERE fiscal_receipt_number = ? ORDER BY id LIMIT 1",
                        (parsed.fiscal_receipt_number,),
                    ).fetchone()

            created = existing is None
            if created:
                timestamp = _now()
                receipt_id = self._insert_id(
                    connection,
                    """INSERT INTO receipts
                       (owner_chat_id, status, created_at, updated_at)
                       VALUES (?, 'PROCESSING', ?, ?)""",
                    (owner_chat_id, timestamp, timestamp),
                )
            else:
                receipt_id = int(existing["id"])

            self._execute(
                connection,
                """INSERT INTO receipt_import_receipts (import_id, receipt_id, source_record_id)
                   VALUES (?, ?, ?)
                   ON CONFLICT DO NOTHING""",
                (import_id, receipt_id, source_record_id),
            )

        needs_review = self.save_processed_receipt(receipt_id, parsed, threshold)
        return receipt_id, created, needs_review

    @staticmethod
    def _stored_file(row: Any) -> StoredFile:
        return StoredFile(
            id=int(row["id"]), receipt_id=row["receipt_id"], path=Path(row["stored_path"]),
            sha256=row["sha256"], mime_type=row["mime_type"], telegram_file_id=row["telegram_file_id"],
        )

    def queue_single_receipt(self, chat_id: int, file_id: int) -> int | None:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT receipt_id FROM receipt_files WHERE id = ?", (file_id,)).fetchone()
            if row is None or row["receipt_id"] is not None:
                return None
            timestamp = _now()
            receipt_id = self._insert_id(
                connection,
                "INSERT INTO receipts (owner_chat_id, status, created_at, updated_at) VALUES (?, 'QUEUED', ?, ?)",
                (chat_id, timestamp, timestamp),
            )
            self._execute(connection, "UPDATE receipt_files SET receipt_id = ? WHERE id = ?", (receipt_id, file_id))
            return receipt_id

    def active_batch(self, chat_id: int) -> int | None:
        with self._connection() as connection:
            row = self._execute(
                connection,
                "SELECT id FROM batch_sessions WHERE chat_id = ? AND state = 'ACTIVE' ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            return int(row["id"]) if row else None

    def begin_batch(self, chat_id: int) -> int:
        active = self.active_batch(chat_id)
        if active is not None:
            return active
        with self._connection() as connection:
            timestamp = _now()
            return self._insert_id(
                connection,
                "INSERT INTO batch_sessions (chat_id, state, created_at, updated_at) VALUES (?, 'ACTIVE', ?, ?)",
                (chat_id, timestamp, timestamp),
            )

    def stage_file_in_batch(self, batch_id: int, file_id: int) -> bool:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT state FROM batch_sessions WHERE id = ?", (batch_id,)).fetchone()
            if row is None or row["state"] != "ACTIVE":
                return False
            changed = self._execute(
                connection,
                "UPDATE receipt_files SET batch_session_id = ? WHERE id = ? AND receipt_id IS NULL AND batch_session_id IS NULL",
                (batch_id, file_id),
            ).rowcount
            return changed == 1

    def finish_batch(self, chat_id: int) -> int | None:
        with self._connection() as connection:
            batch = self._execute(
                connection,
                "SELECT id FROM batch_sessions WHERE chat_id = ? AND state = 'ACTIVE' ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            if batch is None:
                return None
            batch_id = int(batch["id"])
            files = self._execute(
                connection,
                "SELECT id FROM receipt_files WHERE batch_session_id = ? AND receipt_id IS NULL ORDER BY id",
                (batch_id,),
            ).fetchall()
            if not files:
                return None
            timestamp = _now()
            receipt_id = self._insert_id(
                connection,
                "INSERT INTO receipts (owner_chat_id, status, created_at, updated_at) VALUES (?, 'QUEUED', ?, ?)",
                (chat_id, timestamp, timestamp),
            )
            self._execute(
                connection,
                "UPDATE receipt_files SET receipt_id = ?, batch_session_id = NULL WHERE batch_session_id = ?",
                (receipt_id, batch_id),
            )
            self._execute(connection, "UPDATE batch_sessions SET state = 'FINISHED', updated_at = ? WHERE id = ?", (timestamp, batch_id))
            return receipt_id

    def cancel_batch(self, chat_id: int) -> list[Path]:
        with self._connection() as connection:
            batch = self._execute(
                connection,
                "SELECT id FROM batch_sessions WHERE chat_id = ? AND state = 'ACTIVE' ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            if batch is None:
                return []
            batch_id = int(batch["id"])
            files = self._execute(connection, "SELECT stored_path FROM receipt_files WHERE batch_session_id = ?", (batch_id,)).fetchall()
            self._execute(connection, "DELETE FROM receipt_files WHERE batch_session_id = ?", (batch_id,))
            self._execute(connection, "UPDATE batch_sessions SET state = 'CANCELLED', updated_at = ? WHERE id = ?", (_now(), batch_id))
            return [Path(row["stored_path"]) for row in files]

    def list_receipt_files(self, receipt_id: int) -> list[StoredFile]:
        with self._connection() as connection:
            rows = self._execute(connection, "SELECT * FROM receipt_files WHERE receipt_id = ? ORDER BY id", (receipt_id,)).fetchall()
            return [self._stored_file(row) for row in rows]

    def set_processing(self, receipt_id: int) -> None:
        with self._connection() as connection:
            self._execute(connection, "UPDATE receipts SET status = 'PROCESSING', error_message = NULL, updated_at = ? WHERE id = ?", (_now(), receipt_id))

    def fail_receipt(self, receipt_id: int, error: str) -> None:
        with self._connection() as connection:
            self._execute(connection, "UPDATE receipts SET status = 'FAILED', error_message = ?, updated_at = ? WHERE id = ?", (error[:2000], _now(), receipt_id))

    def queued_receipt_ids(self) -> list[int]:
        with self._connection() as connection:
            self._execute(connection, "UPDATE receipts SET status = 'QUEUED' WHERE status = 'PROCESSING'")
            rows = self._execute(connection, "SELECT id FROM receipts WHERE status = 'QUEUED' ORDER BY id").fetchall()
            return [int(row["id"]) for row in rows]

    def save_pages(self, receipt_file_id: int, pages: list[dict[str, Any]]) -> None:
        with self._connection() as connection:
            self._execute(connection, "DELETE FROM receipt_pages WHERE receipt_file_id = ?", (receipt_file_id,))
            self._executemany(
                connection,
                """INSERT INTO receipt_pages
                   (receipt_file_id, page_number, rendered_path, embedded_text, ocr_text, ocr_confidence, qr_payloads)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [(
                    receipt_file_id, page["page_number"], page.get("rendered_path"), page.get("embedded_text"),
                    page.get("ocr_text"), page.get("ocr_confidence"), json.dumps(page.get("qr_payloads", []), ensure_ascii=False),
                ) for page in pages],
            )

    def save_qr_codes(self, receipt_id: int, codes: list[dict[str, Any]]) -> None:
        with self._connection() as connection:
            self._execute(connection, "DELETE FROM receipt_qr_codes WHERE receipt_id = ?", (receipt_id,))
            self._executemany(
                connection,
                """INSERT INTO receipt_qr_codes
                   (receipt_id, receipt_file_id, page_number, payload, url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(
                    receipt_id, code["receipt_file_id"], code["page_number"], code["payload"], code.get("url"), _now(),
                ) for code in codes],
            )

    def save_processed_receipt(self, receipt_id: int, parsed: ParsedReceipt, threshold: float = 0.90) -> bool:
        with self._connection() as connection:
            receipt = self._execute(connection, "SELECT owner_chat_id FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
            if receipt is None:
                raise KeyError(f"Receipt {receipt_id} does not exist")
            chat_id = int(receipt["owner_chat_id"])
            self._execute(connection, "DELETE FROM review_tasks WHERE receipt_id = ?", (receipt_id,))
            self._execute(connection, "DELETE FROM receipt_items WHERE receipt_id = ?", (receipt_id,))
            timestamp = _now()
            if parsed.merchant_name_source:
                self._execute(
                    connection,
                    """INSERT INTO merchants (name_source, name_normalized, name_en, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(name_normalized) DO UPDATE SET name_en = COALESCE(excluded.name_en, merchants.name_en), updated_at = excluded.updated_at""",
                    (parsed.merchant_name_source, _normalise(parsed.merchant_name_source), parsed.merchant_name_en, timestamp, timestamp),
                )
            self._execute(
                connection,
                """UPDATE receipts SET merchant_name_source = ?, merchant_name_en = ?, receipt_datetime = ?, currency = ?,
                       gross_total = ?, tax_total = ?, payment_method = ?, fiscal_receipt_number = ?, source_text = ?,
                       ocr_language = ?, confidence = ?, validation_warnings = ?, qr_url = ?, qr_payload = ?,
                       verification_response = ?, verification_content_type = ?, verification_http_status = ?, source_method = ?,
                       merchant_tax_id = ?, merchant_location_name = ?, merchant_administrative_unit = ?, merchant_city = ?,
                       merchant_address = ?, merchant_cashier = ?, buyer = ?, fiscal_esir = ?, receipt_type = ?,
                       transaction_type = ?, payment_details = ?, tax_details = ?,
                       status = 'COMPLETED', updated_at = ? WHERE id = ?""",
                (
                    parsed.merchant_name_source, parsed.merchant_name_en,
                    parsed.receipt_datetime.isoformat() if parsed.receipt_datetime else None,
                    parsed.currency, self._decimal(parsed.gross_total), self._decimal(parsed.tax_total),
                    parsed.payment_method, parsed.fiscal_receipt_number, parsed.raw_text, parsed.ocr_language,
                    parsed.confidence, json.dumps(parsed.validation_warnings, ensure_ascii=False), parsed.qr_url,
                    parsed.qr_payload, parsed.verification_response, parsed.verification_content_type,
                    parsed.verification_http_status, parsed.source_method,
                    parsed.merchant_tax_id, parsed.merchant_location_name, parsed.merchant_administrative_unit,
                    parsed.merchant_city, parsed.merchant_address, parsed.merchant_cashier, parsed.buyer,
                    parsed.fiscal_esir, parsed.receipt_type, parsed.transaction_type,
                    json.dumps(parsed.payment_details, ensure_ascii=False),
                    json.dumps(parsed.tax_details, ensure_ascii=False), timestamp, receipt_id,
                ),
            )
            item_ids: list[int] = []
            for item in parsed.items:
                item_ids.append(self._insert_id(
                    connection,
                    """INSERT INTO receipt_items
                       (receipt_id, position, name_source, name_en, producer_source, producer_en, quantity, unit,
                        unit_price, line_total, confidence, tax_symbol, tax_name)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        receipt_id, item.position, item.name_source, item.name_en, item.producer_source, item.producer_en,
                        self._decimal(item.quantity), item.unit, self._decimal(item.unit_price), self._decimal(item.line_total),
                        item.confidence, item.tax_symbol, item.tax_name,
                    ),
                ))
            tasks: list[tuple[str, str, str | None]] = []
            for item, item_id in zip(parsed.items, item_ids):
                if item.confidence < threshold:
                    tasks.append((f"item.{item_id}.name_source", f"Reply with the corrected Serbian name for item {item.position}.", item.name_source))
            if parsed.validation_warnings:
                tasks.extend(("receipt.confirmation", f"Please verify: {warning}", warning) for warning in parsed.validation_warnings)
            self._executemany(
                connection,
                """INSERT INTO review_tasks
                   (receipt_id, chat_id, field_key, prompt, current_value, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?)""",
                [(receipt_id, chat_id, key, prompt, value, timestamp, timestamp) for key, prompt, value in tasks],
            )
            if tasks:
                self._execute(connection, "UPDATE receipts SET status = 'NEEDS_REVIEW', updated_at = ? WHERE id = ?", (timestamp, receipt_id))
            return bool(tasks)

    @staticmethod
    def _decimal(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    def next_review_task(self, chat_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT * FROM review_tasks WHERE chat_id = ? AND state = 'OPEN' ORDER BY id LIMIT 1", (chat_id,)).fetchone()
            if row is None:
                return None
            self._execute(connection, "UPDATE review_tasks SET state = 'NOTIFIED', updated_at = ? WHERE id = ?", (_now(), row["id"]))
            return dict(row)

    def awaiting_review_task(self, chat_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT * FROM review_tasks WHERE chat_id = ? AND state = 'AWAITING_RESPONSE' ORDER BY id LIMIT 1", (chat_id,)).fetchone()
            return dict(row) if row else None

    def mark_awaiting_correction(self, task_id: int) -> None:
        with self._connection() as connection:
            self._execute(connection, "UPDATE review_tasks SET state = 'AWAITING_RESPONSE', updated_at = ? WHERE id = ? AND state = 'NOTIFIED'", (_now(), task_id))

    def approve_review_task(self, task_id: int) -> int:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT receipt_id FROM review_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Review task {task_id} does not exist")
            self._execute(connection, "UPDATE review_tasks SET state = 'RESOLVED', updated_at = ? WHERE id = ?", (_now(), task_id))
            self._insert_id(connection, "INSERT INTO review_events (review_task_id, action, created_at) VALUES (?, 'approved', ?)", (task_id, _now()))
            self._complete_if_no_tasks(connection, int(row["receipt_id"]))
            return int(row["receipt_id"])

    def apply_correction(self, task_id: int, value: str, english_value: str | None = None) -> int:
        with self._connection() as connection:
            task = self._execute(connection, "SELECT * FROM review_tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"Review task {task_id} does not exist")
            field_key, receipt_id = str(task["field_key"]), int(task["receipt_id"])
            if field_key == "receipt.merchant":
                self._execute(connection, "UPDATE receipts SET merchant_name_source = ?, merchant_name_en = ?, updated_at = ? WHERE id = ?", (value, english_value or value, _now(), receipt_id))
                self._execute(connection, """INSERT INTO merchants (name_source, name_normalized, name_en, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?) ON CONFLICT(name_normalized) DO UPDATE SET name_en = excluded.name_en, updated_at = excluded.updated_at""", (value, _normalise(value), english_value or value, _now(), _now()))
            elif field_key == "receipt.date":
                self._execute(connection, "UPDATE receipts SET receipt_datetime = ?, updated_at = ? WHERE id = ?", (self._parse_date(value).isoformat(), _now(), receipt_id))
            elif field_key == "receipt.gross_total":
                amount = Decimal(value.replace(",", ".").replace(" ", ""))
                self._execute(connection, "UPDATE receipts SET gross_total = ?, updated_at = ? WHERE id = ?", (str(amount), _now(), receipt_id))
            elif field_key.startswith("item.") and field_key.endswith(".name_source"):
                item_id = int(field_key.split(".")[1])
                self._execute(connection, "UPDATE receipt_items SET name_source = ?, name_en = ? WHERE id = ? AND receipt_id = ?", (value, english_value or value, item_id, receipt_id))
            else:
                raise ValueError("This review task cannot accept a free-text correction")
            self._execute(connection, "UPDATE review_tasks SET state = 'RESOLVED', updated_at = ? WHERE id = ?", (_now(), task_id))
            self._insert_id(connection, "INSERT INTO review_events (review_task_id, action, value, created_at) VALUES (?, 'corrected', ?, ?)", (task_id, value, _now()))
            self._complete_if_no_tasks(connection, receipt_id)
            return receipt_id

    @staticmethod
    def _parse_date(value: str) -> datetime:
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                pass
        raise ValueError("Use DD.MM.YYYY or DD.MM.YYYY HH:MM")

    def _complete_if_no_tasks(self, connection: Any, receipt_id: int) -> None:
        unresolved = self._execute(connection, "SELECT 1 FROM review_tasks WHERE receipt_id = ? AND state != 'RESOLVED' LIMIT 1", (receipt_id,)).fetchone()
        if unresolved is None:
            self._execute(connection, "UPDATE receipts SET status = 'COMPLETED', updated_at = ? WHERE id = ?", (_now(), receipt_id))

    def add_item_from_manual_entry(self, receipt_id: int, name_source: str, name_en: str, quantity: Decimal, line_total: Decimal) -> None:
        with self._connection() as connection:
            position = int(self._execute(connection, "SELECT COALESCE(MAX(position), 0) + 1 AS next_position FROM receipt_items WHERE receipt_id = ?", (receipt_id,)).fetchone()["next_position"])
            self._insert_id(connection, """INSERT INTO receipt_items
                (receipt_id, position, name_source, name_en, quantity, unit, unit_price, line_total, confidence)
                VALUES (?, ?, ?, ?, ?, 'pcs', ?, ?, 1.0)""", (receipt_id, position, name_source, name_en, str(quantity), str(line_total / quantity), str(line_total)))

    def get_translation_override(self, source_text: str) -> str | None:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT english_text FROM translation_overrides WHERE source_normalized = ?", (_normalise(source_text),)).fetchone()
            return str(row["english_text"]) if row else None

    def save_translation_override(self, source_text: str, english_text: str) -> None:
        with self._connection() as connection:
            self._execute(connection, """INSERT INTO translation_overrides (source_normalized, source_text, english_text, updated_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(source_normalized) DO UPDATE SET source_text = excluded.source_text,
                english_text = excluded.english_text, updated_at = excluded.updated_at""", (_normalise(source_text), source_text, english_text, _now()))

    def receipt_summary(self, receipt_id: int) -> str:
        with self._connection() as connection:
            receipt = self._execute(connection, "SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
            if receipt is None:
                raise KeyError(f"Receipt {receipt_id} does not exist")
            items = self._execute(connection, "SELECT * FROM receipt_items WHERE receipt_id = ? ORDER BY position", (receipt_id,)).fetchall()
            lines = [
                f"Receipt #{receipt_id}: {receipt['status']}",
                f"Merchant: {receipt['merchant_name_en'] or receipt['merchant_name_source'] or 'unknown'}",
                f"Date: {receipt['receipt_datetime'] or 'unknown'}",
                f"Total: {receipt['gross_total'] or 'unknown'} {receipt['currency']}",
                f"Source: {receipt['source_method']}",
                "Items:",
            ]
            lines.extend(f"{item['position']}. {item['name_en'] or item['name_source']} — {item['line_total'] or '?'} {receipt['currency']}" for item in items)
            return "\n".join(lines)

    def status_counts(self, chat_id: int) -> dict[str, int]:
        with self._connection() as connection:
            rows = self._execute(connection, "SELECT status, COUNT(*) AS count FROM receipts WHERE owner_chat_id = ? GROUP BY status", (chat_id,)).fetchall()
            return {str(row["status"]): int(row["count"]) for row in rows}

    def owner_chat_id(self, receipt_id: int) -> int:
        with self._connection() as connection:
            row = self._execute(connection, "SELECT owner_chat_id FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
            if row is None:
                raise KeyError(f"Receipt {receipt_id} does not exist")
            return int(row["owner_chat_id"])
