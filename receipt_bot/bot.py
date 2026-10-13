from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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
from .db import ReceiptRepository
from .documents import DocumentRenderer
from .ocr import PaddleOcrEngine
from .parser import SerbianReceiptParser
from .processor import ProcessOutcome, ReceiptProcessor
from .storage import LocalStorage
from .translation import CachedTranslator, LocalMarianTranslator


CompletionHandler = Callable[[int, ProcessOutcome | None, Exception | None], Awaitable[None]]


class ReceiptWorker:
    """One persistent worker deliberately processes one receipt at a time."""

    def __init__(self, processor: ReceiptProcessor, completion: CompletionHandler) -> None:
        self.processor = processor
        self.completion = completion
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="receipt-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def enqueue(self, receipt_id: int) -> None:
        self.queue.put_nowait(receipt_id)

    async def _run(self) -> None:
        while True:
            receipt_id = await self.queue.get()
            try:
                outcome = await asyncio.to_thread(self.processor.process, receipt_id)
                await self.completion(receipt_id, outcome, None)
            except Exception as error:
                await self.completion(receipt_id, None, error)
            finally:
                self.queue.task_done()


class TelegramReceiptBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_directories()
        self.repository = ReceiptRepository(settings.database_path)
        self.repository.initialise()
        self.storage = LocalStorage(settings.data_dir)
        translator = CachedTranslator(
            self.repository, LocalMarianTranslator(settings.translation_model)
        )
        self.processor = ReceiptProcessor(
            self.repository,
            DocumentRenderer(self.storage),
            PaddleOcrEngine(),
            SerbianReceiptParser(),
            translator,
            settings.confidence_threshold,
        )
        self.worker = ReceiptWorker(self.processor, self._processing_complete)
        self.application: Application | None = None

    def build_application(self) -> Application:
        application = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .post_init(self._startup)
            .post_shutdown(self._shutdown)
            .build()
        )
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("batch", self.batch))
        application.add_handler(CommandHandler("done", self.done))
        application.add_handler(CommandHandler("cancel", self.cancel))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("item", self.item))
        application.add_handler(CallbackQueryHandler(self.review_callback, pattern=r"^review:"))
        application.add_handler(
            MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF, self.upload)
        )
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.review_reply))
        self.application = application
        return application

    async def _startup(self, _: Application) -> None:
        self.worker.start()
        for receipt_id in self.repository.queued_receipt_ids():
            self.worker.enqueue(receipt_id)

    async def _shutdown(self, _: Application) -> None:
        await self.worker.stop()

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
            "Send a receipt photo or PDF to import it. Each upload is one receipt by default.\n\n"
            "Use /batch before sending several photos of one long receipt, then /done.\n"
            "Use /status to check processing. I only message again when review is needed."
        )

    async def batch(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_chat and update.effective_message
        batch_id = self.repository.begin_batch(update.effective_chat.id)
        await update.effective_message.reply_text(
            f"Batch {batch_id} is active. Send all pages of this receipt, then use /done."
        )

    async def done(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_chat and update.effective_message
        receipt_id = self.repository.finish_batch(update.effective_chat.id)
        if receipt_id is None:
            await update.effective_message.reply_text("There is no active batch with uploaded files.")
            return
        self.worker.enqueue(receipt_id)
        await update.effective_message.reply_text(f"Receipt #{receipt_id} queued for processing.")

    async def cancel(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_chat and update.effective_message
        paths = self.repository.cancel_batch(update.effective_chat.id)
        for path in paths:
            self.storage.discard(path)
        await update.effective_message.reply_text(
            "Batch cancelled." if paths else "There is no active batch to cancel."
        )

    async def status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_chat and update.effective_message
        counts = self.repository.status_counts(update.effective_chat.id)
        if not counts:
            await update.effective_message.reply_text("No receipts have been uploaded yet.")
            return
        order = ("QUEUED", "PROCESSING", "NEEDS_REVIEW", "COMPLETED", "FAILED")
        message = "\n".join(f"{status.title().replace('_', ' ')}: {counts.get(status, 0)}" for status in order)
        await update.effective_message.reply_text(message)

    async def upload(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_chat and update.effective_message
        message = update.effective_message
        telegram_file_id: str
        filename: str
        if message.photo:
            telegram_file_id = message.photo[-1].file_id
            filename = f"telegram-{message.message_id}.jpg"
        elif message.document:
            telegram_file_id = message.document.file_id
            filename = message.document.file_name or f"telegram-{message.message_id}"
        else:
            return

        temporary = self.settings.data_dir / f"incoming-{uuid4().hex}"
        try:
            telegram_file = await message.get_bot().get_file(telegram_file_id)
            await telegram_file.download_to_drive(custom_path=temporary)
            path, digest, mime_type = self.storage.persist_download(temporary, filename)
            stored, inserted = self.repository.add_file(
                path, digest, mime_type, filename, telegram_file_id, message.message_id
            )
        except Exception as error:
            temporary.unlink(missing_ok=True)
            await message.reply_text(f"The file could not be saved: {error}")
            return
        if not inserted:
            await message.reply_text("That exact file was already imported; I skipped the duplicate.")
            return

        batch_id = self.repository.active_batch(update.effective_chat.id)
        if batch_id is not None:
            self.repository.stage_file_in_batch(batch_id, stored.id)
            await message.reply_text(f"Added page to active batch {batch_id}. Send /done when complete.")
            return
        receipt_id = self.repository.queue_single_receipt(update.effective_chat.id, stored.id)
        if receipt_id is None:
            await message.reply_text("The upload could not be queued.")
            return
        self.worker.enqueue(receipt_id)
        await message.reply_text(f"Receipt #{receipt_id} queued for processing.")

    async def review_callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.callback_query and update.effective_chat
        query = update.callback_query
        await query.answer()
        match = re.fullmatch(r"review:(\d+):(approve|correct)", query.data or "")
        if match is None:
            return
        task_id, action = int(match.group(1)), match.group(2)
        if action == "approve":
            receipt_id = self.repository.approve_review_task(task_id)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(f"Verified receipt #{receipt_id} field.")
            await self._notify_next_review(update.effective_chat.id)
            return
        self.repository.mark_awaiting_correction(task_id)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Reply to this chat with the corrected value now.")

    async def review_reply(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_chat and update.effective_message and update.effective_message.text
        task = self.repository.awaiting_review_task(update.effective_chat.id)
        if task is None:
            await update.effective_message.reply_text("Send a receipt photo/PDF, or use /batch for multiple pages.")
            return
        try:
            receipt_id = await asyncio.to_thread(
                self.processor.apply_review_correction, task, update.effective_message.text
            )
        except Exception as error:
            await update.effective_message.reply_text(f"That correction could not be saved: {error}")
            return
        await update.effective_message.reply_text(f"Correction saved for receipt #{receipt_id}.")
        await self._notify_next_review(update.effective_chat.id)

    async def item(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._reject_if_needed(update):
            return
        assert update.effective_message
        content = " ".join(context.args)
        try:
            target, quantity, total = [part.strip() for part in content.split("|")]
            receipt_id_text, name_source = target.split(" ", 1)
            receipt_id = int(receipt_id_text)
            await asyncio.to_thread(
                self.processor.add_manual_item, receipt_id, name_source, quantity, total
            )
        except (ValueError, IndexError):
            await update.effective_message.reply_text(
                "Use: /item <receipt id> Serbian item name | quantity | line total"
            )
            return
        await update.effective_message.reply_text(f"Item added to receipt #{receipt_id}.")

    async def _processing_complete(
        self, receipt_id: int, outcome: ProcessOutcome | None, error: Exception | None
    ) -> None:
        if self.application is None:
            return
        if outcome is not None:
            chat_id = self.repository.owner_chat_id(outcome.receipt_id)
            if outcome.needs_review:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=f"Receipt #{outcome.receipt_id} needs review.\n\n{outcome.summary}",
                )
                await self._notify_next_review(chat_id)
            return
        chat_id = self.repository.owner_chat_id(receipt_id)
        await self.application.bot.send_message(
            chat_id=chat_id,
            text=f"Receipt #{receipt_id} could not be processed: {error}. Send a clearer image or check /status.",
        )

    async def _notify_next_review(self, chat_id: int) -> None:
        if self.application is None:
            return
        task = self.repository.next_review_task(chat_id)
        if task is None:
            return
        task_id = int(task["id"])
        field_key = str(task["field_key"])
        text = f"Receipt #{task['receipt_id']}: {task['prompt']}"
        if task["current_value"]:
            text += f"\nCurrent value: {task['current_value']}"
        buttons = [InlineKeyboardButton("Mark verified", callback_data=f"review:{task_id}:approve")]
        if field_key in {"receipt.merchant", "receipt.date", "receipt.gross_total"} or field_key.endswith(".name_source"):
            buttons.append(InlineKeyboardButton("Correct", callback_data=f"review:{task_id}:correct"))
        await self.application.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup([buttons]),
        )


def main() -> None:
    settings = Settings.from_environment()
    bot = TelegramReceiptBot(settings)
    bot.build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":  # pragma: no cover
    main()
