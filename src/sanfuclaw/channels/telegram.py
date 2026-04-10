"""Telegram channel — bot adapter using python-telegram-bot."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.types import MessageRole

logger = logging.getLogger(__name__)


class TelegramChannel:
    """Telegram bot channel adapter.

    Receives messages from Telegram users and sends responses back.
    Uses an internal queue to bridge the callback-driven telegram-bot
    library with our async iterator interface.
    """

    name: str = "telegram"

    def __init__(self, bot_token: str, allowed_users: list[str] | None = None):
        self._bot_token = bot_token
        self._allowed_users = allowed_users  # Telegram usernames or user IDs
        self._queue: asyncio.Queue[Envelope] = asyncio.Queue()
        self._app = None
        self._response_buffers: dict[int, str] = {}  # chat_id -> accumulated response

    async def start(self) -> None:
        from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters

        self._app = ApplicationBuilder().token(self._bot_token).build()

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        # Initialize and start polling in background
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram channel started")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram channel stopped")

    async def _handle_start(self, update, context) -> None:
        """Handle /start command."""
        await update.message.reply_text(
            "Hello! I'm Sanfuclaw, your personal AI assistant. Send me a message!"
        )

    async def _handle_message(self, update, context) -> None:
        """Handle incoming text messages — push to queue for the router."""
        if not update.message or not update.message.text:
            return

        user = update.message.from_user
        sender_id = str(user.id)

        # Check allowlist if configured
        if self._allowed_users:
            username = user.username or ""
            if sender_id not in self._allowed_users and username not in self._allowed_users:
                await update.message.reply_text("Sorry, you are not authorized to use this bot.")
                return

        message = Message(
            role=MessageRole.USER,
            content=update.message.text,
            channel_id=self.name,
            session_id=f"tg-{update.message.chat_id}",
            sender_id=sender_id,
            metadata={"chat_id": update.message.chat_id, "username": user.username or ""},
        )
        self._queue.put_nowait(Envelope(
            message=message,
            source_channel=self.name,
        ))

    async def send(self, session_id: str, content: str, **kwargs) -> None:
        """Send a message back to Telegram."""
        if not self._app:
            return

        # Extract chat_id from session_id (format: "tg-{chat_id}")
        chat_id = int(session_id.replace("tg-", ""))
        streaming = kwargs.get("streaming", False)

        if streaming:
            # Accumulate streaming chunks and send periodically
            if chat_id not in self._response_buffers:
                self._response_buffers[chat_id] = ""

            self._response_buffers[chat_id] += content

            # Send when we hit a newline at the end (response complete)
            if content == "\n" and self._response_buffers[chat_id].strip():
                text = self._response_buffers.pop(chat_id).strip()
                await self._send_text(chat_id, text)
        else:
            await self._send_text(chat_id, content)

    async def _send_text(self, chat_id: int, text: str) -> None:
        """Send text to a Telegram chat, splitting if too long."""
        bot = self._app.bot
        # Telegram message limit is 4096 characters
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            try:
                await bot.send_message(chat_id=chat_id, text=chunk)
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")

    async def send_typing(self, session_id: str) -> None:
        """Send typing action to Telegram."""
        if not self._app:
            return
        try:
            chat_id = int(session_id.replace("tg-", ""))
            await self._app.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass

    async def receive(self) -> AsyncIterator[Envelope]:
        """Yield envelopes from the internal queue."""
        while True:
            envelope = await self._queue.get()
            yield envelope
