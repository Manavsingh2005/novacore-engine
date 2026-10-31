"""
NovaCore: Dual-Client MTProto & Bot API Manager
Manages connection lifecycle, official Windows 11 device mimicry,
account tier detection (Free vs Premium), and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from telethon import TelegramClient
from telethon.sessions import StringSession

from core.config import settings
from core.database import db
from core.ratelimiter import guardian

logger = logging.getLogger("novacore.client")


class ClientManager:
    """
    Coordinates the User MTProto client and the Assistant Bot API client.
    Handles dynamic account tier detection and safe shutdown.
    """

    def __init__(self):
        self.user_client: Optional[TelegramClient] = None
        self.bot_client: Optional[TelegramClient] = None
        self.me = None
        self.bot_me = None
        self.is_premium: bool = False
        self._is_running: bool = False

    def build_user_client(self) -> TelegramClient:
        """Instantiates user client with official Windows 11 Desktop device fingerprint."""
        session = StringSession(settings.STRING_SESSION) if settings.STRING_SESSION else StringSession()
        client = TelegramClient(
            session=session,
            api_id=settings.API_ID,
            api_hash=settings.API_HASH,
            device_model=settings.DEVICE_MODEL,
            system_version=settings.SYSTEM_VERSION,
            app_version=settings.APP_VERSION,
            lang_code=settings.LANG_CODE,
            system_lang_code=settings.SYSTEM_LANG_CODE,
        )
        return client

    def build_bot_client(self) -> Optional[TelegramClient]:
        """Instantiates Assistant Bot client if BOT_TOKEN is present."""
        if not settings.BOT_TOKEN:
            return None
        client = TelegramClient(
            session="data/bot_session",
            api_id=settings.API_ID,
            api_hash=settings.API_HASH,
        )
        return client

    async def start(self) -> None:
        """Initializes database, connects clients, and verifies account tier."""
        logger.info("Initializing NovaCore database...")
        await db.connect()

        # Seed initial Groq keys from .env if present
        for key in settings.groq_keys:
            await db.add_api_key("groq", key)

        if not settings.STRING_SESSION:
            logger.warning(
                "STRING_SESSION is empty. Please run session_generator.py in Phase 7 to log in."
            )
            return

        logger.info("Connecting MTProto User Client...")
        self.user_client = self.build_user_client()
        await self.user_client.start()

        self.me = await self.user_client.get_me()
        self.is_premium = bool(getattr(self.me, "premium", False))
        tier_label = "Telegram Premium" if self.is_premium else "Standard Free Tier"
        logger.info(
            "User logged in as: %s (ID: %d) | Tier: %s",
            self.me.first_name,
            self.me.id,
            tier_label,
        )

        # Connect Assistant Bot if configured
        if settings.BOT_TOKEN:
            logger.info("Connecting Assistant Bot API Client...")
            self.bot_client = self.build_bot_client()
            if self.bot_client:
                await self.bot_client.start(bot_token=settings.BOT_TOKEN)
                self.bot_me = await self.bot_client.get_me()
                logger.info(
                    "Assistant Bot online as: @%s",
                    self.bot_me.username,
                )

        self._is_running = True

    async def stop(self) -> None:
        """Safely disconnects clients and closes database."""
        logger.info("Stopping NovaCore clients...")
        self._is_running = False

        if self.user_client and self.user_client.is_connected():
            await self.user_client.disconnect()
            logger.info("User client disconnected.")

        if self.bot_client and self.bot_client.is_connected():
            await self.bot_client.disconnect()
            logger.info("Assistant bot client disconnected.")

        await db.close()
        logger.info("NovaCore shutdown complete.")

    @property
    def is_running(self) -> bool:
        return self._is_running


# Global client manager instance
manager = ClientManager()
