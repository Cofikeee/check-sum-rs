# Serbian Receipt Telegram Bot

A local, personal Telegram bot that turns Serbian printed receipt photos and PDFs into a retained SQLite database. It stores the original Serbian text alongside English translations and only asks for Telegram review when extraction is uncertain.

## What it does

- Accepts receipt photos and PDF documents from one configured Telegram user.
- Treats each upload as a receipt by default; `/batch` and `/done` combine several photos into one long receipt.
- Keeps originals under `data/files/`, rendered PDF/image pages under `data/pages/`, and all structured data in `data/receipts.sqlite3`.
- Uses local PaddleOCR Serbian Latin/Cyrillic models, then a local Serbo-Croatian-to-English Marian translation model.
- Stores merchant, receipt date, fiscal number when found, currency, total, OCR text, item lines, quantities, prices, and producer only when printed.
- Queues receipts sequentially, rejects duplicate file hashes, and records a review/audit trail for corrections.

This first release is intentionally limited to printed retail/restaurant receipts and PDFs. It does not attempt handwriting, invoices, product-catalog enrichment, web dashboards, or cloud backup.

## Run it

1. Create a Telegram bot using `@BotFather`, then obtain your numeric Telegram user ID.
2. Copy `.env.example` to `.env` and fill `TELEGRAM_BOT_TOKEN` and `ALLOWED_TELEGRAM_USER_ID`.
3. Start the service:

   ```bash
   docker compose up --build -d
   ```

4. Open the bot chat and run `/start`.

The first receipt triggers local OCR and translation-model downloads. This is expected and can take a while; later runs reuse `data/models/`. The Docker image explicitly uses CPU-only PyTorch—no CUDA/GPU packages are installed. A 4-core, 8-GB-RAM host is the practical minimum. The container requires `poppler-utils` only for image-only PDF pages.

## Telegram commands

| Command | Behavior |
| --- | --- |
| `/start` | Shows the upload workflow. |
| `/batch` | Starts collecting multiple photos/PDFs as one receipt. |
| `/done` | Queues the current batch as one receipt. |
| `/cancel` | Discards unprocessed files in the active batch. |
| `/status` | Shows queued, processing, review-needed, completed, and failed counts. |
| `/item <receipt id> Serbian name \| quantity \| line total` | Adds a missing item line after an OCR failure. |

When a field is uncertain, the bot sends a concise review task. Use **Mark verified**, or **Correct** and reply with the requested value. Corrections to merchant or item names are translated locally and cached for future receipts.

## Data and backup

The bot never calls paid OCR or AI APIs. Telegram still receives uploaded files as part of its normal bot transport. Use full-disk encryption for the host and copy the whole `data/` directory to an encrypted external disk for backups. Do not commit `.env` or `data/`.

## Test

Run the deterministic unit tests without downloading OCR or translation models:

```bash
docker compose run --rm receipt-bot pytest
```

Before relying on automatic acceptance, process 30 representative receipts and compare merchant, date, total, and every line item against the originals. Keep the default conservative confidence threshold until that pilot is complete.
