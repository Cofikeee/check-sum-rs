from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .models import ParsedReceipt, StoredFile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise(value: str) -> str:
    return " ".join(value.upper().split())


SCHEMA = """
PRAGMA foreign_keys = ON;

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
    UNIQUE(receipt_file_id, page_number)
);

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
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
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

    def initialise(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(SCHEMA)

    def add_file(
        self,
        path: Path,
        sha256: str,
        mime_type: str,
        original_filename: str | None,
        telegram_file_id: str | None,
        telegram_message_id: int | None,
    ) -> tuple[StoredFile, bool]:
        """Persist a file record; returns existing record for a duplicate hash."""
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM receipt_files WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if existing:
                return self._stored_file(existing), False
            cursor = connection.execute(
                """INSERT INTO receipt_files
                   (stored_path, sha256, original_filename, mime_type, telegram_file_id,
                    telegram_message_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(path), sha256, original_filename, mime_type,
                    telegram_file_id, telegram_message_id, _now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM receipt_files WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return self._stored_file(row), True

    @staticmethod
    def _stored_file(row: sqlite3.Row) -> StoredFile:
        return StoredFile(
            id=int(row["id"]),
            receipt_id=row["receipt_id"],
            path=Path(row["stored_path"]),
            sha256=row["sha256"],
            mime_type=row["mime_type"],
            telegram_file_id=row["telegram_file_id"],
        )

    def queue_single_receipt(self, chat_id: int, file_id: int) -> int | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT receipt_id FROM receipt_files WHERE id = ?", (file_id,)
            ).fetchone()
            if row is None or row["receipt_id"] is not None:
                return None
            timestamp = _now()
            cursor = connection.execute(
                "INSERT INTO receipts (owner_chat_id, status, created_at, updated_at) VALUES (?, 'QUEUED', ?, ?)",
                (chat_id, timestamp, timestamp),
            )
            receipt_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE receipt_files SET receipt_id = ? WHERE id = ?", (receipt_id, file_id)
            )
            return receipt_id

    def active_batch(self, chat_id: int) -> int | None:
        with self._connection() as connection:
            row = connection.execute(
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
            cursor = connection.execute(
                "INSERT INTO batch_sessions (chat_id, state, created_at, updated_at) VALUES (?, 'ACTIVE', ?, ?)",
                (chat_id, timestamp, timestamp),
            )
            return int(cursor.lastrowid)

    def stage_file_in_batch(self, batch_id: int, file_id: int) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state FROM batch_sessions WHERE id = ?", (batch_id,)
            ).fetchone()
            if row is None or row["state"] != "ACTIVE":
                return False
            changed = connection.execute(
                "UPDATE receipt_files SET batch_session_id = ? WHERE id = ? AND receipt_id IS NULL AND batch_session_id IS NULL",
                (batch_id, file_id),
            ).rowcount
            return changed == 1

    def finish_batch(self, chat_id: int) -> int | None:
        with self._connection() as connection:
            batch = connection.execute(
                "SELECT id FROM batch_sessions WHERE chat_id = ? AND state = 'ACTIVE' ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            if batch is None:
                return None
            batch_id = int(batch["id"])
            files = connection.execute(
                "SELECT id FROM receipt_files WHERE batch_session_id = ? AND receipt_id IS NULL ORDER BY id",
                (batch_id,),
            ).fetchall()
            if not files:
                return None
            timestamp = _now()
            receipt_id = int(connection.execute(
                "INSERT INTO receipts (owner_chat_id, status, created_at, updated_at) VALUES (?, 'QUEUED', ?, ?)",
                (chat_id, timestamp, timestamp),
            ).lastrowid)
            connection.execute(
                "UPDATE receipt_files SET receipt_id = ?, batch_session_id = NULL WHERE batch_session_id = ?",
                (receipt_id, batch_id),
            )
            connection.execute(
                "UPDATE batch_sessions SET state = 'FINISHED', updated_at = ? WHERE id = ?",
                (timestamp, batch_id),
            )
            return receipt_id

    def cancel_batch(self, chat_id: int) -> list[Path]:
        with self._connection() as connection:
            batch = connection.execute(
                "SELECT id FROM batch_sessions WHERE chat_id = ? AND state = 'ACTIVE' ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            if batch is None:
                return []
            batch_id = int(batch["id"])
            files = connection.execute(
                "SELECT stored_path FROM receipt_files WHERE batch_session_id = ?", (batch_id,)
            ).fetchall()
            connection.execute("DELETE FROM receipt_files WHERE batch_session_id = ?", (batch_id,))
            connection.execute(
                "UPDATE batch_sessions SET state = 'CANCELLED', updated_at = ? WHERE id = ?",
                (_now(), batch_id),
            )
            return [Path(row["stored_path"]) for row in files]

    def list_receipt_files(self, receipt_id: int) -> list[StoredFile]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM receipt_files WHERE receipt_id = ? ORDER BY id", (receipt_id,)
            ).fetchall()
            return [self._stored_file(row) for row in rows]

    def set_processing(self, receipt_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE receipts SET status = 'PROCESSING', error_message = NULL, updated_at = ? WHERE id = ?",
                (_now(), receipt_id),
            )

    def fail_receipt(self, receipt_id: int, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE receipts SET status = 'FAILED', error_message = ?, updated_at = ? WHERE id = ?",
                (error[:2000], _now(), receipt_id),
            )

    def queued_receipt_ids(self) -> list[int]:
        with self._connection() as connection:
            connection.execute("UPDATE receipts SET status = 'QUEUED' WHERE status = 'PROCESSING'")
            rows = connection.execute(
                "SELECT id FROM receipts WHERE status = 'QUEUED' ORDER BY id"
            ).fetchall()
            return [int(row["id"]) for row in rows]

    def save_pages(self, receipt_file_id: int, pages: list[dict[str, Any]]) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM receipt_pages WHERE receipt_file_id = ?", (receipt_file_id,))
            connection.executemany(
                """INSERT INTO receipt_pages
                   (receipt_file_id, page_number, rendered_path, embedded_text, ocr_text, ocr_confidence)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        receipt_file_id, page["page_number"], page.get("rendered_path"),
                        page.get("embedded_text"), page.get("ocr_text"), page.get("ocr_confidence"),
                    )
                    for page in pages
                ],
            )

    def save_processed_receipt(self, receipt_id: int, parsed: ParsedReceipt, threshold: float) -> bool:
        """Save extraction and create review tasks. Returns whether review is required."""
        with self._connection() as connection:
            receipt = connection.execute(
                "SELECT owner_chat_id FROM receipts WHERE id = ?", (receipt_id,)
            ).fetchone()
            if receipt is None:
                raise KeyError(f"Receipt {receipt_id} does not exist")
            chat_id = int(receipt["owner_chat_id"])
            connection.execute("DELETE FROM review_tasks WHERE receipt_id = ?", (receipt_id,))
            connection.execute("DELETE FROM receipt_items WHERE receipt_id = ?", (receipt_id,))
            timestamp = _now()
            if parsed.merchant_name_source:
                connection.execute(
                    """INSERT INTO merchants (name_source, name_normalized, name_en, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(name_normalized) DO UPDATE SET name_en = COALESCE(excluded.name_en, merchants.name_en), updated_at = excluded.updated_at""",
                    (
                        parsed.merchant_name_source, _normalise(parsed.merchant_name_source),
                        parsed.merchant_name_en, timestamp, timestamp,
                    ),
                )
            connection.execute(
                """UPDATE receipts SET
                       merchant_name_source = ?, merchant_name_en = ?, receipt_datetime = ?, currency = ?,
                       gross_total = ?, tax_total = ?, payment_method = ?, fiscal_receipt_number = ?,
                       source_text = ?, ocr_language = ?, confidence = ?, validation_warnings = ?,
                       status = 'COMPLETED', updated_at = ?
                   WHERE id = ?""",
                (
                    parsed.merchant_name_source, parsed.merchant_name_en,
                    parsed.receipt_datetime.isoformat() if parsed.receipt_datetime else None,
                    parsed.currency, self._decimal(parsed.gross_total), self._decimal(parsed.tax_total),
                    parsed.payment_method, parsed.fiscal_receipt_number, parsed.raw_text,
                    parsed.ocr_language, parsed.confidence, json.dumps(parsed.validation_warnings),
                    timestamp, receipt_id,
                ),
            )
            item_ids: list[int] = []
            for item in parsed.items:
                cursor = connection.execute(
                    """INSERT INTO receipt_items
                       (receipt_id, position, name_source, name_en, producer_source, producer_en,
                        quantity, unit, unit_price, line_total, confidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        receipt_id, item.position, item.name_source, item.name_en,
                        item.producer_source, item.producer_en, self._decimal(item.quantity), item.unit,
                        self._decimal(item.unit_price), self._decimal(item.line_total), item.confidence,
                    ),
                )
                item_ids.append(int(cursor.lastrowid))

            tasks: list[tuple[str, str, str | None]] = []
            if not parsed.merchant_name_source:
                tasks.append(("receipt.merchant", "Reply with the merchant name shown on the receipt.", None))
            if not parsed.receipt_datetime:
                tasks.append(("receipt.date", "Reply with the receipt date as DD.MM.YYYY, optionally followed by HH:MM.", None))
            if parsed.gross_total is None:
                tasks.append(("receipt.gross_total", "Reply with the receipt total in RSD, for example 1234.50.", None))
            if not parsed.items:
                tasks.append(("receipt.item_lines", "No items were found. Add item lines with /item <receipt id> name | quantity | line total.", None))
            for item, item_id in zip(parsed.items, item_ids):
                if item.confidence < threshold:
                    tasks.append((
                        f"item.{item_id}.name_source",
                        f"Reply with the corrected Serbian name for item {item.position}.",
                        item.name_source,
                    ))
            for warning in parsed.validation_warnings:
                if warning.startswith("No ") or warning.startswith("Merchant") or warning.startswith("Receipt date") or warning.startswith("Receipt total"):
                    continue
                tasks.append(("receipt.confirmation", f"Please verify: {warning}", warning))
            if parsed.confidence < threshold and not tasks:
                tasks.append(("receipt.confirmation", "OCR confidence was low. Please verify the receipt summary.", None))
            connection.executemany(
                """INSERT INTO review_tasks
                   (receipt_id, chat_id, field_key, prompt, current_value, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?)""",
                [(receipt_id, chat_id, key, prompt, value, timestamp, timestamp) for key, prompt, value in tasks],
            )
            if tasks:
                connection.execute(
                    "UPDATE receipts SET status = 'NEEDS_REVIEW', updated_at = ? WHERE id = ?",
                    (timestamp, receipt_id),
                )
            return bool(tasks)

    @staticmethod
    def _decimal(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    def next_review_task(self, chat_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM review_tasks WHERE chat_id = ? AND state = 'OPEN' ORDER BY id LIMIT 1",
                (chat_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE review_tasks SET state = 'NOTIFIED', updated_at = ? WHERE id = ?",
                (_now(), row["id"]),
            )
            return dict(row)

    def awaiting_review_task(self, chat_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM review_tasks WHERE chat_id = ? AND state = 'AWAITING_RESPONSE' ORDER BY id LIMIT 1",
                (chat_id,),
            ).fetchone()
            return dict(row) if row else None

    def mark_awaiting_correction(self, task_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE review_tasks SET state = 'AWAITING_RESPONSE', updated_at = ? WHERE id = ? AND state = 'NOTIFIED'",
                (_now(), task_id),
            )

    def approve_review_task(self, task_id: int) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT receipt_id FROM review_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Review task {task_id} does not exist")
            connection.execute(
                "UPDATE review_tasks SET state = 'RESOLVED', updated_at = ? WHERE id = ?",
                (_now(), task_id),
            )
            connection.execute(
                "INSERT INTO review_events (review_task_id, action, created_at) VALUES (?, 'approved', ?)",
                (task_id, _now()),
            )
            self._complete_if_no_tasks(connection, int(row["receipt_id"]))
            return int(row["receipt_id"])

    def apply_correction(self, task_id: int, value: str, english_value: str | None = None) -> int:
        with self._connection() as connection:
            task = connection.execute("SELECT * FROM review_tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"Review task {task_id} does not exist")
            field_key = str(task["field_key"])
            receipt_id = int(task["receipt_id"])
            if field_key == "receipt.merchant":
                connection.execute(
                    "UPDATE receipts SET merchant_name_source = ?, merchant_name_en = ?, updated_at = ? WHERE id = ?",
                    (value, english_value or value, _now(), receipt_id),
                )
                connection.execute(
                    """INSERT INTO merchants (name_source, name_normalized, name_en, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(name_normalized) DO UPDATE SET name_en = excluded.name_en, updated_at = excluded.updated_at""",
                    (value, _normalise(value), english_value or value, _now(), _now()),
                )
            elif field_key == "receipt.date":
                parsed = self._parse_date(value)
                connection.execute(
                    "UPDATE receipts SET receipt_datetime = ?, updated_at = ? WHERE id = ?",
                    (parsed.isoformat(), _now(), receipt_id),
                )
            elif field_key == "receipt.gross_total":
                amount = Decimal(value.replace(",", ".").replace(" ", ""))
                connection.execute(
                    "UPDATE receipts SET gross_total = ?, updated_at = ? WHERE id = ?",
                    (str(amount), _now(), receipt_id),
                )
            elif field_key.startswith("item.") and field_key.endswith(".name_source"):
                item_id = int(field_key.split(".")[1])
                connection.execute(
                    "UPDATE receipt_items SET name_source = ?, name_en = ? WHERE id = ? AND receipt_id = ?",
                    (value, english_value or value, item_id, receipt_id),
                )
            else:
                raise ValueError("This review task cannot accept a free-text correction")
            connection.execute(
                "UPDATE review_tasks SET state = 'RESOLVED', updated_at = ? WHERE id = ?",
                (_now(), task_id),
            )
            connection.execute(
                "INSERT INTO review_events (review_task_id, action, value, created_at) VALUES (?, 'corrected', ?, ?)",
                (task_id, value, _now()),
            )
            self._complete_if_no_tasks(connection, receipt_id)
            return receipt_id

    @staticmethod
    def _parse_date(value: str) -> datetime:
        value = value.strip()
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        raise ValueError("Use DD.MM.YYYY or DD.MM.YYYY HH:MM")

    @staticmethod
    def _complete_if_no_tasks(connection: sqlite3.Connection, receipt_id: int) -> None:
        unresolved = connection.execute(
            "SELECT 1 FROM review_tasks WHERE receipt_id = ? AND state != 'RESOLVED' LIMIT 1",
            (receipt_id,),
        ).fetchone()
        if unresolved is None:
            connection.execute(
                "UPDATE receipts SET status = 'COMPLETED', updated_at = ? WHERE id = ?",
                (_now(), receipt_id),
            )

    def add_item_from_manual_entry(
        self, receipt_id: int, name_source: str, name_en: str, quantity: Decimal, line_total: Decimal
    ) -> None:
        with self._connection() as connection:
            position = int(connection.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS next_position FROM receipt_items WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()["next_position"])
            connection.execute(
                """INSERT INTO receipt_items
                   (receipt_id, position, name_source, name_en, quantity, unit, unit_price, line_total, confidence)
                   VALUES (?, ?, ?, ?, ?, 'pcs', ?, ?, 1.0)""",
                (receipt_id, position, name_source, name_en, str(quantity), str(line_total / quantity), str(line_total)),
            )

    def get_translation_override(self, source_text: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT english_text FROM translation_overrides WHERE source_normalized = ?",
                (_normalise(source_text),),
            ).fetchone()
            return str(row["english_text"]) if row else None

    def save_translation_override(self, source_text: str, english_text: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO translation_overrides (source_normalized, source_text, english_text, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(source_normalized) DO UPDATE SET source_text = excluded.source_text,
                   english_text = excluded.english_text, updated_at = excluded.updated_at""",
                (_normalise(source_text), source_text, english_text, _now()),
            )

    def receipt_summary(self, receipt_id: int) -> str:
        with self._connection() as connection:
            receipt = connection.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
            if receipt is None:
                raise KeyError(f"Receipt {receipt_id} does not exist")
            items = connection.execute(
                "SELECT * FROM receipt_items WHERE receipt_id = ? ORDER BY position", (receipt_id,)
            ).fetchall()
            lines = [
                f"Receipt #{receipt_id}: {receipt['status']}",
                f"Merchant: {receipt['merchant_name_en'] or receipt['merchant_name_source'] or 'unknown'}",
                f"Date: {receipt['receipt_datetime'] or 'unknown'}",
                f"Total: {receipt['gross_total'] or 'unknown'} {receipt['currency']}",
                "Items:",
            ]
            lines.extend(
                f"{item['position']}. {item['name_en'] or item['name_source']} — {item['line_total'] or '?'} {receipt['currency']}"
                for item in items
            )
            return "\n".join(lines)

    def status_counts(self, chat_id: int) -> dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM receipts WHERE owner_chat_id = ? GROUP BY status",
                (chat_id,),
            ).fetchall()
            return {str(row["status"]): int(row["count"]) for row in rows}

    def owner_chat_id(self, receipt_id: int) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT owner_chat_id FROM receipts WHERE id = ?", (receipt_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Receipt {receipt_id} does not exist")
            return int(row["owner_chat_id"])
