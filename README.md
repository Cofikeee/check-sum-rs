# Serbian Receipt Telegram Bot

A local, personal Telegram bot that turns Serbian printed receipt photos and PDFs into verified receipt records in PostgreSQL. It reads the fiscal QR code locally, follows the public verification URL, and stores the original Serbian values alongside English translations.

## What it does

- Accepts receipt photos and PDF documents from one configured Telegram user.
- Treats each upload as a receipt by default; `/batch` and `/done` combine several photos into one long receipt.
- Keeps originals under `data/files/`, rendered PDF/image pages under `data/pages/`, and all structured data in PostgreSQL.
- Decodes QR symbols locally with ZXing; no receipt image is sent to an OCR or AI service.
- Fetches the public verification response, then stores merchant, receipt date, fiscal number, currency, total, payment, item lines, quantities, prices, and producer only when printed.
- Queues receipts sequentially, rejects duplicate file hashes, and records a review/audit trail for corrections.

This first release is intentionally limited to printed retail/restaurant receipts and PDFs. It does not attempt handwriting, invoices, product-catalog enrichment, web dashboards, or cloud backup.

## Run it

1. Create a Telegram bot using `@BotFather`, then obtain your numeric Telegram user ID.
2. Copy `.env.example` to `.env` and fill `TELEGRAM_BOT_TOKEN` and `ALLOWED_TELEGRAM_USER_ID`. Set a long random `POSTGRES_PASSWORD` and use the same value in `DATABASE_URL`.
3. Start the service:

   ```bash
   docker compose up --build -d
   ```

4. Open the bot chat and run `/start`.

The first receipt triggers a local translation-model download. This is expected and can take a while; later runs reuse `data/models/`. The PostgreSQL data directory is stored in the named `postgres_data` Docker volume, so a normal container crash/restart does not lose records. Do not run `docker compose down -v` unless you intentionally want to delete the database. The container requires `poppler-utils` for PDF page rendering.

To inspect the database in DataGrip, add a PostgreSQL data source with host `127.0.0.1`, port `5432`, database `receipts`, user `receiptbot`, and the password from `.env`.

## Telegram commands

| Command | Behavior |
| --- | --- |
| `/start` | Shows the upload workflow. |
| `/batch` | Starts collecting multiple photos/PDFs as one receipt. |
| `/done` | Queues the current batch as one receipt. |
| `/cancel` | Discards unprocessed files in the active batch. |
| `/status` | Shows queued, processing, review-needed, completed, and failed counts. |
| `/item <receipt id> Serbian name \| quantity \| line total` | Adds a missing item line after a manual review. |

If a QR code cannot be decoded, the verification URL is unreachable, or the verification response is missing core fields, the receipt is marked `FAILED`; this version does not fall back to OCR. Corrections to merchant or item names are translated locally and cached for future receipts.

## Data and backup

The bot never calls paid OCR or AI APIs. Telegram still receives uploaded files as part of its normal bot transport. Use full-disk encryption for the host and back up both `data/` and the PostgreSQL volume with an encrypted local copy. Do not commit `.env` or `data/`.

## Test

Run the deterministic unit tests without downloading OCR or translation models:

```bash
docker compose run --rm --no-deps --entrypoint pytest receipt-bot
```

Before relying on automatic acceptance, process 30 representative receipts and compare merchant, date, total, and every line item against the originals. Keep the default conservative confidence threshold until that pilot is complete.
