# Serbian Receipt Telegram Bot

A local Telegram ingestion bot for SlipApp backups and free-form purchase
entries. SlipApp exports are loaded incrementally into an immutable `bronze`
layer; a derived `silver` layer keeps English, denormalized records and product
categories.

## What it does

- Accepts SlipApp `.db`/`.sqlite` backups from one configured Telegram user and imports only new fiscal receipts.
- Keeps the backup under `data/imports/` and stores raw values in explicitly named `bronze_slipapp_*` tables.
- Accepts `/add` manual text; those messages go to `bronze_input_entries`, never into SlipApp tables.
- Builds `silver_receipts`, `silver_receipt_items`, `silver_input_entries`, `silver_merchants`, and `silver_products` from bronze.
- Translates Serbian locally in silver, keeps original values in bronze, and denormalizes merchant/product fields for simple queries.
- Classifies products into English wide/narrow categories. Heuristics run by default; an optional OpenAI classifier can be enabled with `CATEGORY_OPENAI_API_KEY`.
- Reuses translation and category overrides and retains the original source payloads.

## Run it

1. Create a Telegram bot using `@BotFather`, then obtain your numeric Telegram user ID.
2. Copy `.env.example` to `.env`, fill `TELEGRAM_BOT_TOKEN` and `ALLOWED_TELEGRAM_USER_ID`, and set a long random `POSTGRES_PASSWORD` (the same value must be in `DATABASE_URL`).
3. Start the service:

   ```bash
   docker compose up --build -d
   ```

4. Open the bot chat and run `/start`.

The first import triggers a local translation-model download. Later runs reuse
`data/models/`. PostgreSQL is stored in the named `postgres_data` Docker volume,
so a normal container crash/restart does not lose records. Do not run
`docker compose down -v` unless you intentionally want to delete the database.

To inspect the database in DataGrip, add a PostgreSQL data source with host
`127.0.0.1`, port `5432`, database `receipts`, user `receiptbot`, and the
password from `.env`.

## Telegram workflow

| Command | Behavior |
| --- | --- |
| `/start` | Shows the current input workflow. |
| `/add` | Opens inline shop buttons, then accepts `product \| unit \| quantity`; choose “New shop / free text” for a full line. |
| `/status` | Shows bronze and silver row counts. |

Accepted manual units are `pcs`, `kg`, `g`, `l`, `ml`, `pack`, and `set` (common
Serbian aliases such as `kom`, `кг`, and `л` are normalized). The free-text
fallback also accepts forms such as `shop: Lidl, milk 2 l`.

## Import a SlipApp backup

You can send a SlipApp `.db` backup to the bot as a Telegram file. It is stored
under `data/imports/`; raw rows land in `bronze_slipapp_receipts` and
`bronze_slipapp_items`, while the derived English model is written to
`silver_receipts` and `silver_receipt_items`. Sending the same backup again is
safe: its SHA-256 and every fiscal URL/PFR are deduplicated, while a new export
adds only new bronze rows.

For a file already present on the machine, run the importer inside the bot
container so it can reach PostgreSQL and the mounted data volume:

```bash
docker compose exec receipt-bot receipt-bot-import-slipapp /app/data/imports/<backup-file>.db
```

The command defaults ownership to `ALLOWED_TELEGRAM_USER_ID`; use
`--owner-chat-id` for another private chat. Bronze is committed before silver,
so a translation/category failure never destroys the raw import and a rerun can
rebuild silver.

## Categories and AI

The default classifier is local and deterministic. It writes English `wide`
and `narrow` labels plus confidence/source. To use an OpenAI model for unknown
products, set `CATEGORY_OPENAI_API_KEY` and optionally
`CATEGORY_OPENAI_MODEL`; this is opt-in and can incur API costs. Approved
corrections can be written to `silver_category_overrides` and will be reused.

## Data and backup

The source-of-truth tables are `bronze_slipapp_*` and `bronze_input_entries`.
Silver is derived and can be rebuilt. Back up both `data/` and the
`postgres_data` volume with an encrypted local copy. Do not commit `.env` or
`data/`.

## Test

Run deterministic unit tests without downloading the translation model:

```bash
docker compose run --rm --no-deps --entrypoint pytest receipt-bot
```
