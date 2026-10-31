"""
NovaCore: High-Performance Enterprise Async Microservice Engine
Main Application Entry Point.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from telethon import events

from core.config import settings
from core.client import manager
from core.dispatcher import load_all_plugins
from core.ai_pool import ai_pool
from core.database import db
from core.web_server import WebHealthServer
from plugins.security.inbox_guard import incoming_pm_handler
from plugins.utility.afk import afk_incoming_mention_handler, afk_outgoing_clear_handler

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("novacore.main")


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


async def run_reminder_sweeper(client):
    """Background worker to trigger scheduled voice call alarms and alerts."""
    from plugins.utility.reminder import execute_voice_alarm
    while True:
        try:
            due_reminders = await db.get_due_reminders()
            for rem in due_reminders:
                await db.mark_reminder_triggered(rem["id"])
                asyncio.create_task(execute_voice_alarm(client, rem))
        except Exception as e:
            logger.debug("Reminder sweeper check: %s", e)
        await asyncio.sleep(5)


async def main():
    logger.info("==========================================")
    logger.info("       Starting NovaCore 2026 Engine      ")
    logger.info("==========================================")

    start_time = time.time()

    # 1. Start Client Manager (DB, MTProto User, Assistant Bot)
    await manager.start()

    if not manager.user_client:
        logger.error(
            "User client is not started. Please generate a STRING_SESSION with session_generator.py and add it to .env"
        )
        return

    # 2. Cloud State Sync: If database is brand new (e.g. freshly deployed container), restore from Saved Messages!
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

    # 4. Discover and load all plugins
    loaded_plugins = load_all_plugins(manager.user_client)
    logger.info("Successfully loaded %d plugin modules.", loaded_plugins)

    # 5. Register passive event listeners
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
    logger.info("Registered passive Inbox-Guard and AFK listeners.")

    # 6. Start Background Sweepers (Ephemeral cleaner & Voice Alarm scheduler)
    sweeper_task = asyncio.create_task(run_ephemeral_sweeper(manager.user_client))
    reminder_task = asyncio.create_task(run_reminder_sweeper(manager.user_client))
    logger.info("Background workers started (Ephemeral sweeper & Voice Alarm scheduler).")

    # 7. Start Micro Web Health Server (if PORT is set for 24/7 PaaS keepalive)
    web_server = None
    if settings.PORT:
        web_server = WebHealthServer(port=settings.PORT, start_time=start_time)
        await web_server.start()

    # 8. Check if reboot was triggered by .update or .restart marker
    marker_path = os.path.join("data", "restart_marker.json")
    if os.path.exists(marker_path):
        try:
            with open(marker_path, "r", encoding="utf-8") as f:
                marker_data = json.load(f)
            os.remove(marker_path)
            chat_id = marker_data.get("chat_id")
            msg_id = marker_data.get("msg_id")
            m_type = marker_data.get("type", "restart")
            if chat_id and msg_id:
                if m_type == "update":
                    msg = (
                        "🚀 **NovaCore Engine Successfully Updated & Online!**\n\n"
                        f"• **Active Plugins:** `{loaded_plugins}` modules\n"
                        f"• **AI Key Pool:** `{key_count}` active keys\n"
                        "• **Server:** AWS EC2 (24/7 Cloud)\n"
                        "• **Status:** 100% Operational"
                    )
                else:
                    msg = (
                        "🔄 **NovaCore Engine Successfully Restarted!**\n\n"
                        f"• **Active Plugins:** `{loaded_plugins}` modules\n"
                        "• **Status:** 100% Operational"
                    )
                await manager.user_client.edit_message(chat_id, msg_id, msg)
        except Exception as e:
            logger.debug("Failed to edit restart marker: %s", e)

    logger.info("NovaCore is fully armed and running!")

    # Keep running until disconnected or cancelled
    try:
        await manager.user_client.run_until_disconnected()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutdown signal received.")
    finally:
        sweeper_task.cancel()
        reminder_task.cancel()
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
        logger.info("NovaCore stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
