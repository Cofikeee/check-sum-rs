from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Settings
from .category import CategoryClassifier, classifier_from_environment
from .dbt_runner import DbtRunner
from .input_parser import InputParseError, parse_input
from .slipapp import SlipAppImporter
from .storage import LocalStorage
from .translation import CachedTranslator, LocalMarianTranslator
from .warehouse import WarehouseRepository


class TelegramReceiptBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_directories()
        self.repository = WarehouseRepository(settings.database_url)
        self.repository.initialise()
        self.storage = LocalStorage(settings.data_dir)
        translator = CachedTranslator(
            self.repository, LocalMarianTranslator(settings.translation_model)
        )
        self.translator = translator
        self.category_classifier: CategoryClassifier = classifier_from_environment()
        self.dbt_runner = DbtRunner()
        self.slipapp_importer = SlipAppImporter(
            self.repository, translator, settings.confidence_threshold, self.category_classifier, self.dbt_runner
        )
        self.application: Application | None = None

    def build_application(self) -> Application:
        application = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .build()
        )
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("add", self.add))
        application.add_handler(CallbackQueryHandler(self.input_callback, pattern=r"^input:"))
        application.add_handler(
            MessageHandler(filters.Document.ALL, self.upload)
        )
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.review_reply))
        self.application = application
        return application

    def _is_allowed(self, update: Update) -> bool:
        user = update.effective_user
        return user is not None and user.id == self.settings.allowed_telegram_user_id

    async def _reject_if_needed(self, update: Update) -> bool:
        if self._is_allowed(update):
            return False
        if update.effective_message:
            await update.effective_message.reply_text("This is a private receipt bot.")
        return True

    async def start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_message
        await update.effective_message.reply_text(
            "Send a SlipApp .db backup to load receipts incrementally into bronze and silver.\n"
            "Use /add for a manual product entry; the shop can be selected from inline buttons, "
            "or you can send free text in the format shop | product | unit | quantity.\n\n"
            "Use /status to inspect bronze/silver counts. Raw data is in the bronze schema, "
            "derived English data in silver, and app metadata in meta."
        )


    async def status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_chat and update.effective_message
        if isinstance(self.repository, WarehouseRepository):
            counts = self.repository.layer_counts()
            await update.effective_message.reply_text(
                "Bronze SlipApp receipts: {bronze_slipapp_receipts}\n"
                "Bronze manual inputs: {bronze_input_entries}\n"
                "Silver receipts: {silver_receipts}\n"
                "Silver receipt items: {silver_receipt_items}\n"
                "Silver manual inputs: {silver_input_entries}".format(**counts)
            )
            return
        counts = self.repository.status_counts(update.effective_chat.id)
        if not counts:
            await update.effective_message.reply_text("No receipts have been uploaded yet.")
            return
        order = ("QUEUED", "PROCESSING", "NEEDS_REVIEW", "COMPLETED", "FAILED")
        message = "\n".join(f"{status.title().replace('_', ' ')}: {counts.get(status, 0)}" for status in order)
        await update.effective_message.reply_text(message)

    async def add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_message
        choices = self.repository.merchant_choices() if isinstance(self.repository, WarehouseRepository) else []
        context.user_data.pop("input_merchant", None)
        context.user_data.pop("input_unit", None)
        context.user_data["input_state"] = "awaiting"
        if not choices:
            await update.effective_message.reply_text(
                "No shops are known yet. Reply in this format:\nshop | product | unit | quantity\n"
                "Example: Lidl | Mleko | l | 2"
            )
            return
        buttons = [
            [InlineKeyboardButton(str(choice["name_en"] or choice["name_source"]), callback_data=f"input:merchant:{choice['id']}")]
            for choice in choices[:30]
        ]
        buttons.append([InlineKeyboardButton("New shop / free text", callback_data="input:merchant:new")])
        await update.effective_message.reply_text(
            "Choose a shop, then send: product | unit | quantity.\n"
            "Units: pcs, kg, g, l, ml, pack, set.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def input_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.callback_query and update.effective_message
        query = update.callback_query
        await query.answer()
        merchant_match = re.fullmatch(r"input:merchant:(\d+|new)", query.data or "")
        if merchant_match is not None:
            merchant_id = merchant_match.group(1)
            if merchant_id == "new":
                context.user_data["input_merchant"] = None
                context.user_data.pop("input_unit", None)
                context.user_data["input_state"] = "awaiting"
                await query.edit_message_text(
                    "Reply in this format:\nshop | product | unit | quantity\n"
                    "Example: New Shop | Apples | kg | 1.5"
                )
                return
            merchant_name = self.repository.merchant_name(int(merchant_id)) if isinstance(self.repository, WarehouseRepository) else None
            context.user_data["input_merchant"] = merchant_name
            context.user_data["input_state"] = "awaiting_unit"
            buttons = [
                [InlineKeyboardButton(unit, callback_data=f"input:unit:{unit}")]
                for unit in ("pcs", "kg", "g", "l", "ml", "pack", "set")
            ]
            await query.edit_message_text(
                f"Shop selected: {merchant_name or merchant_id}. Choose a unit:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        unit_match = re.fullmatch(r"input:unit:(pcs|kg|g|l|ml|pack|set)", query.data or "")
        if unit_match is not None:
            await self._input_unit_callback(query, context, unit_match.group(1))

    async def _input_unit_callback(self, query: Any, context: ContextTypes.DEFAULT_TYPE, unit: str) -> None:
        context.user_data["input_unit"] = unit
        context.user_data["input_state"] = "awaiting_product_qty"
        await query.edit_message_text(f"Unit selected: {unit}. Reply: product | quantity\nExample: Mleko | 2")

    async def upload(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_chat and update.effective_message
        message = update.effective_message
        if not message.document:
            return
        telegram_file_id = message.document.file_id
        filename = message.document.file_name or f"telegram-{message.message_id}"
        if not self._is_slipapp_backup(filename):
            await message.reply_text("Only SlipApp .db/.sqlite backups are accepted as document uploads.")
            return

        temporary = self.settings.data_dir / f"incoming-{uuid4().hex}"
        try:
            telegram_file = await message.get_bot().get_file(telegram_file_id)
            await telegram_file.download_to_drive(custom_path=temporary)
            path, _, _ = self.storage.persist_import(temporary, filename)
            result = await asyncio.to_thread(
                self.slipapp_importer.import_file,
                path,
                update.effective_chat.id,
                path,
                filename,
            )
        except Exception as error:
            temporary.unlink(missing_ok=True)
            await message.reply_text(f"The file could not be saved: {error}")
            return
        await message.reply_text(
            f"SlipApp backup imported: {result.records} receipts, "
            f"{result.created} new, {result.updated} already known."
        )

    @staticmethod
    def _is_slipapp_backup(filename: str) -> bool:
        return Path(filename).suffix.lower() in {".db", ".sqlite", ".sqlite3", ".backup"}

    async def review_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_chat and update.effective_message and update.effective_message.text
        try:
            draft = parse_input(
                update.effective_message.text,
                context.user_data.get("input_merchant"),
                context.user_data.get("input_unit"),
            )
            bronze_id = self.repository.add_bronze_input(
                update.effective_message.text,
                draft.merchant_name_source,
                draft.product_name_source,
                draft.unit,
                draft.quantity,
                update.effective_chat.id,
                update.effective_message.message_id,
            )
            await asyncio.to_thread(
                self.repository.build_silver_input, bronze_id, self.translator, self.category_classifier
            )
            await asyncio.to_thread(self.dbt_runner.build)
        except (InputParseError, ValueError) as error:
            await update.effective_message.reply_text(f"Could not structure that input: {error}")
            return
        context.user_data.pop("input_state", None)
        context.user_data.pop("input_merchant", None)
        context.user_data.pop("input_unit", None)
        await update.effective_message.reply_text(
            f"Saved manual input #{bronze_id} to bronze.input_entries and silver.input_entries."
        )


def main() -> None:
    settings = Settings.from_environment()
    bot = TelegramReceiptBot(settings)
    bot.build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":  # pragma: no cover
    main()
