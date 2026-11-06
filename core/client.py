"""
NovaCore: High-Performance Telegram MTProto Bot Manager
Manages connection lifecycle, MTProto Bot initialization with 2GB upload/download limit,
and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from telethon import TelegramClient

from core.config import settings
from core.database import db

logger = logging.getLogger("novacore.client")


class ClientManager:
    """
    Coordinates the MTProto Bot client.
    Handles connection, bot info caching, and clean shutdown.
    """

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.bot_client: Optional[TelegramClient] = None
        self.user_client: Optional[TelegramClient] = None
        self.me = None
        self.bot_me = None
        self.is_premium: bool = True  # MTProto bot supports 2GB uploads/downloads
        self._is_running: bool = False

    def build_bot_client(self) -> TelegramClient:
        """Instantiates MTProto Bot client."""
        client = TelegramClient(
            session="data/utility_bot",
            api_id=settings.API_ID,
            api_hash=settings.API_HASH,
        )
        return client

    async def start(self) -> None:
        """Initializes database and connects MTProto Bot client."""
        logger.info("Initializing NovaCore database...")
        await db.connect()

        # Seed initial Groq keys from .env if present
        for key in settings.groq_keys:
            await db.add_api_key("groq", key)

        if not settings.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is required in .env for Utility Bot operation!")

        logger.info("Connecting MTProto Bot Client (@utilitybymnvbot)...")
        self.client = self.build_bot_client()
        await self.client.start(bot_token=settings.BOT_TOKEN)

        self.me = await self.client.get_me()
        self.bot_me = self.me
        self.bot_client = self.client
        self.user_client = self.client  # compatibility alias
        self._is_running = True

        logger.info(
            "Utility Bot successfully connected as: @%s (ID: %d) | 2GB MTProto Streaming Ready",
            self.me.username,
            self.me.id,
        )

    async def stop(self) -> None:
        """Disconnects clients and closes database safely."""
        logger.info("Gracefully stopping NovaCore engine...")
        self._is_running = False
        if self.client and self.client.is_connected():
            await self.client.disconnect()
        await db.close()
        logger.info("Shutdown complete.")


manager = ClientManager()
