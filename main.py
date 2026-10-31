"""
Ultroid-Neo: Master Entry Point (2026 Edition)
Initializes database, AI multi-key pool, MTProto user client,
Assistant bot, and micro-web health server.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from telethon import events

# Reconfigure stdout to UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.config import settings
from core.client import manager
from core.dispatcher import load_all_plugins
from core.ai_pool import ai_pool
from core.database import db
from core.web_server import WebHealthServer
from plugins.pmpermit.pmpermit import incoming_pm_handler
from plugins.utility.afk import afk_incoming_mention_handler, afk_outgoing_clear_handler

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ultroid_neo.main")


async def run_ephemeral_sweeper(client):
    """Background worker to purge expired ephemeral messages (e.g. 30m voice transcriptions)."""
    while True:
        try:
            due_records = await db.get_due_ephemeral_deletions()
            if due_records:
                to_remove = []
                for rec_id, chat_id, msg_id in due_records:
                    try:
                        await client.delete_messages(chat_id, [msg_id])
                    except Exception:
                        pass
                    to_remove.append(rec_id)
                await db.remove_ephemeral_records(to_remove)
        except Exception as e:
            logger.debug("Ephemeral sweeper check: %s", e)
        await asyncio.sleep(15)


async def main():
    logger.info("==========================================")
    logger.info("   Starting Ultroid-Neo (2026 Edition)    ")
    logger.info("==========================================")

    start_time = time.time()

    # 1. Start Client Manager (DB, MTProto User, Assistant Bot)
    await manager.start()

    if not manager.user_client:
        logger.error(
            "User client is not started. Please generate a STRING_SESSION with session_generator.py and add it to .env"
        )
        return

    # 2. Cloud State Sync: If database is brand new (e.g. freshly deployed Render container), restore from Saved Messages!
    active_keys = await db.get_active_keys("groq")
    if not active_keys:
        logger.info("Local database has 0 keys. Checking Telegram Saved Messages for cloud backup...")
        from core.cloud_sync import restore_database_from_cloud
        restored = await restore_database_from_cloud(manager.user_client)
        if restored:
            await db.close()
            await db.connect()

    # 3. Reload AI Multi-Key Pool
    key_count = await ai_pool.reload_keys()
    logger.info("AI Pool initialized with %d active keys.", key_count)

    # 3. Discover and load all plugins
    loaded_plugins = load_all_plugins(manager.user_client)
    logger.info("Successfully loaded %d plugin modules.", loaded_plugins)

    # 4. Register passive event listeners
    manager.user_client.add_event_handler(
        incoming_pm_handler,
        events.NewMessage(incoming=True, func=lambda e: e.is_private),
    )
    manager.user_client.add_event_handler(
        afk_incoming_mention_handler,
        events.NewMessage(incoming=True),
    )
    manager.user_client.add_event_handler(
        afk_outgoing_clear_handler,
        events.NewMessage(outgoing=True),
    )
    logger.info("Registered passive PM-Permit and AFK listeners.")

    # 5. Start Ephemeral Message Sweeper Worker
    sweeper_task = asyncio.create_task(run_ephemeral_sweeper(manager.user_client))
    logger.info("Ephemeral message auto-delete sweeper started.")

    # 6. Start Micro Web Health Server (if PORT is set for 24/7 PaaS keepalive)
    web_server = None
    if settings.PORT:
        web_server = WebHealthServer(port=settings.PORT, start_time=start_time)
        await web_server.start()

    logger.info("Ultroid-Neo is fully armed and running!")

    # Keep running until disconnected or cancelled
    try:
        await manager.user_client.run_until_disconnected()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutdown signal received.")
    finally:
        sweeper_task.cancel()
        if web_server:
            await web_server.stop()

        # Automatic state snapshot to Saved Messages on container restart/shutdown
        if manager.user_client and manager.user_client.is_connected():
            try:
                from core.cloud_sync import backup_database_to_cloud
                await backup_database_to_cloud(manager.user_client)
            except Exception:
                pass

        await manager.stop()
        logger.info("Ultroid-Neo stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
