"""
NovaCore: Telegram Cloud State Persistence
Allows 100% state persistence across ephemeral cloud container redeployments (Render, Koyeb, Railway).
Automatically saves and restores SQLite database snapshots in Telegram Saved Messages.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any
from core.config import settings

logger = logging.getLogger("novacore.cloud_sync")
BACKUP_TAG = "#NOVACORE_STATE_BACKUP"


async def backup_database_to_cloud(client: Any) -> bool:
    """Uploads the local SQLite database file to Saved Messages as a persistent cloud backup."""
    db_file = Path(settings.db_file)
    if not db_file.exists():
        logger.warning("Database file %s does not exist; skipping backup.", db_file)
        return False

    temp_backup = db_file.parent / f"backup_{int(time.time())}.db"
    try:
        shutil.copy2(db_file, temp_backup)
        caption = (
            f"💾 **NovaCore Cloud State Backup**\n"
            f"📅 Date: `{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}`\n"
            f"🏷️ Tag: {BACKUP_TAG}\n"
            f"> 💡 *Used for automatic state restoration on Render / cloud container rebuilds.*"
        )
        await client.send_file(
            "me",
            file=str(temp_backup),
            caption=caption,
            force_document=True,
        )
        logger.info("Database successfully backed up to Telegram Saved Messages.")
        return True
    except Exception as e:
        logger.exception("Failed backing up database to cloud: %s", e)
        return False
    finally:
        if temp_backup.exists():
            try:
                os.remove(temp_backup)
            except OSError:
                pass


async def restore_database_from_cloud(client: Any) -> bool:
    """
    Checks Saved Messages for the latest database backup if the local database
    is empty or missing (e.g. freshly deployed container on Render).
    """
    db_file = Path(settings.db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        logger.info("Checking Saved Messages for cloud database backups...")
        async for msg in client.iter_messages("me", limit=30):
            if msg.document and msg.text and BACKUP_TAG in msg.text:
                logger.info("Found latest cloud database backup (Msg ID: %d). Downloading...", msg.id)
                temp_restore = db_file.parent / "restored.db"
                await client.download_media(msg, file=str(temp_restore))

                if temp_restore.exists() and temp_restore.stat().st_size > 0:
                    shutil.move(temp_restore, db_file)
                    logger.info("Successfully restored cloud database from Saved Messages!")
                    return True
        logger.info("No cloud backup found in Saved Messages; using default local database.")
        return False
    except Exception as e:
        logger.warning("Could not restore database from cloud: %s", e)
        return False
