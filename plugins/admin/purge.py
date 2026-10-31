"""
Ultroid-Neo Plugin: Safe Bulk Message Purger (.purge, .purgeme, .del)
Deletes messages rapidly and safely using Telegram's native 100-message bulk deletion API.
Paces requests with human micro-jitter to prevent anti-spam flags.
"""

from __future__ import annotations

import asyncio
import logging
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.ratelimiter import guardian

logger = logging.getLogger("ultroid_neo.plugin.admin.purge")


@client_cmd(
    pattern="purge",
    description="Purge messages up to replied message or N count (.purge [count] or reply)",
    module="admin",
)
async def purge_cmd(event: SafeMessageWrapper, count_str: str = ""):
    reply = await event.get_reply_message()
    from_msg_id = reply.id if reply else None

    cmd_id = event.message.id
    chat_id = event.chat_id
    messages_to_delete = []

    if from_msg_id:
        async for msg in event.client.iter_messages(
            chat_id,
            min_id=from_msg_id - 1,
            max_id=cmd_id + 1,
        ):
            messages_to_delete.append(msg.id)
    else:
        count = int(count_str) if count_str and count_str.isdigit() else 10
        count = min(count, 500)
        async for msg in event.client.iter_messages(chat_id, limit=count + 1):
            messages_to_delete.append(msg.id)

    if not messages_to_delete:
        await event.edit("⚠️ No messages found to delete.")
        return

    total_deleted = 0
    chunk_size = 100

    for i in range(0, len(messages_to_delete), chunk_size):
        chunk = messages_to_delete[i : i + chunk_size]
        try:
            await event.client.delete_messages(chat_id, chunk)
            total_deleted += len(chunk)
            await guardian.pace_outbound(base_delay=0.2)
        except Exception as e:
            logger.warning("Purge batch error: %s", e)

    try:
        conf = await event.respond(f"🗑️ Purged `{total_deleted}` messages.")
        await asyncio.sleep(3)
        await conf.delete()
    except Exception:
        pass


@client_cmd(
    pattern="purgeme",
    description="Purge only your own outgoing messages (.purgeme [count])",
    module="admin",
)
async def purgeme_cmd(event: SafeMessageWrapper, count_str: str = ""):
    count = int(count_str) if count_str and count_str.isdigit() else 10
    count = min(count, 300)
    chat_id = event.chat_id

    messages_to_delete = []
    async for msg in event.client.iter_messages(chat_id, from_user="me", limit=count + 1):
        messages_to_delete.append(msg.id)

    if not messages_to_delete:
        await event.edit("⚠️ No outgoing messages found to delete.")
        return

    total_deleted = 0
    chunk_size = 100

    for i in range(0, len(messages_to_delete), chunk_size):
        chunk = messages_to_delete[i : i + chunk_size]
        try:
            await event.client.delete_messages(chat_id, chunk)
            total_deleted += len(chunk)
            await guardian.pace_outbound(base_delay=0.2)
        except Exception as e:
            logger.warning("Purgeme batch error: %s", e)

    try:
        conf = await event.respond(f"🧹 Cleaned `{total_deleted}` of your own messages.")
        await asyncio.sleep(3)
        await conf.delete()
    except Exception:
        pass


@client_cmd(
    pattern="del",
    description="Delete the replied message and command trigger (.del)",
    module="admin",
)
async def del_msg_cmd(event: SafeMessageWrapper):
    reply = await event.get_reply_message()
    if not reply:
        await event.edit("❌ Reply to a message with `.del` to delete it.")
        return

    try:
        await event.client.delete_messages(event.chat_id, [reply.id, event.message.id])
    except Exception as e:
        await event.edit(f"❌ Failed to delete: `{e}`")
