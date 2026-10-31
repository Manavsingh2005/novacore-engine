"""
Ultroid-Neo Plugin: Smart AFK Mode & Mention Logger (.afk, .unafk, .back)
Features:
- Rate-limited group auto-reply (max 1 auto-reply per chat every 5 minutes).
- Logs mentions and message links to Saved Messages while away.
- Automatically clears AFK upon sending any outgoing message or via .unafk.
- Reports total mentions collected during away period.
"""

from __future__ import annotations

import asyncio
import logging
import time
from telethon import events
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db
from helpers.formatters import format_uptime

logger = logging.getLogger("ultroid_neo.plugin.utility.afk")

# Chat-level rate limiter: chat_id -> last_afk_notice_timestamp
CHAT_NOTICE_TIMESTAMPS: dict[int, float] = {}


@client_cmd(
    pattern="afk",
    description="Set AFK status with optional reason (.afk [reason])",
    module="utility",
)
async def set_afk_cmd(event: SafeMessageWrapper, reason: str = ""):
    reason_str = reason.strip() or "Busy right now."
    await db.set_afk(reason_str)
    await db.set_setting("afk_mentions_count", "0")

    text = (
        f"💤 **I am now AFK.**\n"
        f"• **Reason:** _{reason_str}_\n"
        f"• *Mentions and messages will be safely logged to Saved Messages.*"
    )
    await event.edit(text)


@client_cmd(
    pattern=r"unafk|back",
    description="Manually clear AFK status (.unafk or .back)",
    module="utility",
)
async def unafk_cmd(event: SafeMessageWrapper):
    was_afk, reason, afk_since = await db.clear_afk()
    if was_afk:
        duration = format_uptime(afk_since)
        mentions_cnt = await db.get_setting("afk_mentions_count", "0")
        await event.edit(
            f"👋 **Welcome back!**\n"
            f"• Away for: `{duration}`\n"
            f"• Mentions logged: `{mentions_cnt}`"
        )
    else:
        await event.edit("💡 You were not marked as AFK.")


async def afk_outgoing_clear_handler(event: events.NewMessage.Event):
    """Detects when the owner speaks, clearing AFK automatically."""
    if not event.out:
        return

    # Don't clear immediately when setting AFK or running unafk
    if event.text and (event.text.startswith(".afk") or event.text.startswith(".unafk") or event.text.startswith(".back")):
        return

    was_afk, reason, afk_since = await db.clear_afk()
    if was_afk:
        duration = format_uptime(afk_since)
        mentions_cnt = await db.get_setting("afk_mentions_count", "0")
        notice = f"👋 **Back online!** (Away for `{duration}` | Mentions logged: `{mentions_cnt}`)"
        try:
            msg = await event.reply(notice)
            # Auto-delete return notification after 5 seconds to keep chat clean
            await asyncio.sleep(5)
            await msg.delete()
        except Exception:
            pass


async def afk_incoming_mention_handler(event: events.NewMessage.Event):
    """Handles incoming mentions or replies while the user is AFK."""
    if event.out:
        return

    is_afk, reason, afk_since = await db.get_afk_status()
    if not is_afk:
        return

    me = await event.client.get_me()
    if not me:
        return

    # Check if mentioned or replied
    is_mentioned = getattr(event.message, "mentioned", False)
    is_replied = False

    if event.message.reply_to:
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.sender_id == me.id:
            is_replied = True

    if not (is_mentioned or is_replied or event.is_private):
        return

    # Increment mentions count in DB
    try:
        current_cnt = int(await db.get_setting("afk_mentions_count", "0") or "0")
        await db.set_setting("afk_mentions_count", str(current_cnt + 1))
    except Exception:
        pass

    # Enforce chat-level rate limiting: max 1 reply per chat every 5 minutes
    chat_id = event.chat_id
    now = time.time()
    last_notice = CHAT_NOTICE_TIMESTAMPS.get(chat_id, 0.0)
    if now - last_notice < 300:  # 5 minutes
        return

    CHAT_NOTICE_TIMESTAMPS[chat_id] = now
    duration = format_uptime(afk_since)

    auto_reply = (
        f"🌙 **{me.first_name} is currently AFK** (away for `{duration}`).\n"
        f"• **Reason:** _{reason}_\n"
        f"*(They will see your message upon return)*"
    )

    try:
        await event.reply(auto_reply)
    except Exception:
        pass

    # Log mention into Saved Messages
    sender = await event.get_sender()
    sender_name = sender.first_name if sender else "User"
    chat_title = getattr(event.chat, "title", "Direct Message")
    alert = (
        f"📌 **AFK Mention Logged**\n"
        f"• **From:** [{sender_name}](tg://user?id={event.sender_id})\n"
        f"• **Chat:** {chat_title}\n"
        f"• **Message:** _{event.text[:250] if event.text else 'Attachment'}_\n"
        f"• **Time:** `{duration} into AFK`"
    )
    try:
        await event.client.send_message("me", alert)
    except Exception:
        pass
