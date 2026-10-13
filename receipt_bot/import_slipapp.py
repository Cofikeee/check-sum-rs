from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .slipapp import SlipAppImporter
from .translation import CachedTranslator, LocalMarianTranslator
from .warehouse import WarehouseRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a SlipApp SQLite backup into PostgreSQL")
    parser.add_argument("backup", type=Path, help="Path visible inside the container")
    parser.add_argument(
        "--owner-chat-id",
        type=int,
        help="Telegram chat id to own imported receipts (defaults to ALLOWED_TELEGRAM_USER_ID)",
    )
    args = parser.parse_args()

    settings = Settings.from_environment()
    settings.ensure_directories()
    repository = WarehouseRepository(settings.database_url)
    repository.initialise()
    translator = CachedTranslator(repository, LocalMarianTranslator(settings.translation_model))
    result = SlipAppImporter(
        repository,
        translator,
        settings.confidence_threshold,
    ).import_file(
        args.backup,
        args.owner_chat_id if args.owner_chat_id is not None else settings.allowed_telegram_user_id,
    )
    print(json.dumps({
        "import_id": result.import_id,
        "records": result.records,
        "created": result.created,
        "updated": result.updated,
        "needs_review": result.needs_review,
        "source_sha256": result.source_sha256,
        "receipt_ids": list(result.receipt_ids),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
