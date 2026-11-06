"""
NovaCore: High-Performance Telegram Utility Bot Engine
Main Application Entry Point.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

from core.config import settings
from core.client import manager
from core.dispatcher import load_all_plugins
from core.database import db
from core.web_server import WebHealthServer

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("novacore.main")


async def main():
    logger.info("==========================================")
    logger.info("    Starting Manav's Utility Bot 2026     ")
    logger.info("==========================================")

    start_time = time.time()

    # 1. Start MTProto Bot Client
    await manager.start()

    if not manager.client:
        logger.error("Bot client failed to initialize. Check BOT_TOKEN in .env.")
        return

    # 2. Discover and load all bot plugins
    loaded_plugins = load_all_plugins(manager.client)
    logger.info("Successfully loaded %d plugin modules.", loaded_plugins)

    # 3. Start Micro Web Health Server on PORT 7860
    web_server = None
    if settings.PORT:
        web_server = WebHealthServer(port=settings.PORT, start_time=start_time)
        await web_server.start()

    logger.info("Manav's Utility Bot (@%s) is ONLINE and running 24/7 on AWS!", manager.me.username)

    # Keep running until disconnected or cancelled
    try:
        await manager.client.run_until_disconnected()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutdown signal received.")
    finally:
        if web_server:
            await web_server.stop()
        await manager.stop()
        logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
