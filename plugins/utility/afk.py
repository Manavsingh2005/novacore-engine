"""
NovaCore Plugin: Smart AFK Mode & Mention Logger (.afk, .unafk, .back)
Features:
- Rate-limited group auto-reply (max 1 auto-reply per chat every 5 minutes).
- Logs mentions and message links to Saved Messages while away.
- Automatically clears AFK upon sending any outgoing message or via .unafk.
- Auto-Cleanup: Automatically deletes the AFK log from Saved Messages AND the AFK auto-reply from the other person's chat once you reply to them!
"""

from __future__ import annotations

import asyncio
import logging
import time
from telethon import events
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db
from helpers.formatters import format_uptime

logger = logging.getLogger("novacore.plugin.utility.afk")

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
    """
    Detects when the owner speaks in any chat:
    1. Clears AFK automatically.
    2. Cleans up any pending AFK mention logs from Saved Messages for this chat!
    3. Cleans up any AFK auto-reply messages from this chat!
    4. Auto-approves the contact in PM-Permit if in a private chat.
    """
    if not event.out:
        return

    # Don't trigger on command triggers themselves
    if event.text and (event.text.startswith(".afk") or event.text.startswith(".unafk") or event.text.startswith(".back")):
        return

    # 1. Clean up pending alerts for this chat (Saved Messages log + Remote AFK message)
    try:
        chat_id = event.chat_id
        pending_alerts = await db.get_and_clear_chat_alerts(chat_id)
        if pending_alerts:
            saved_ids = [a["saved_msg_id"] for a in pending_alerts if a.get("saved_msg_id")]
            remote_ids = [a["remote_msg_id"] for a in pending_alerts if a.get("remote_msg_id")]

            if saved_ids:
                try:
                    await event.client.delete_messages("me", saved_ids)
                except Exception as e:
                    logger.debug("Failed deleting saved mention alerts: %s", e)

            if remote_ids:
                try:
                    await event.client.delete_messages(chat_id, remote_ids)
                except Exception as e:
                    logger.debug("Failed deleting remote AFK messages: %s", e)

        # 2. Auto-approve in private chat so you never have to type .approve
        if event.is_private:
            await db.approve_pm(chat_id)

    except Exception as e:
        logger.debug("Error in outgoing auto-cleanup: %s", e)

    # 3. Clear AFK status if active
    was_afk, reason, afk_since = await db.clear_afk()
    if was_afk:
        duration = format_uptime(afk_since)
        mentions_cnt = await db.get_setting("afk_mentions_count", "0")
        notice = f"👋 **Back online!** (Away for `{duration}` | Mentions logged: `{mentions_cnt}`)"
        try:
            msg = await event.reply(notice)
            # Auto-delete return notification after 3 seconds to keep chat clean
            await asyncio.sleep(3)
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

    sent_reply_id = None
    try:
        sent_reply = await event.reply(auto_reply)
        if sent_reply:
            sent_reply_id = sent_reply.id
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
    saved_msg_id = None
    try:
        saved_msg = await event.client.send_message("me", alert)
        if saved_msg:
            saved_msg_id = saved_msg.id
    except Exception:
        pass

    # Track alert for automatic deletion once you reply to them!
    if saved_msg_id or sent_reply_id:
        try:
            await db.add_chat_alert(
                target_chat_id=chat_id,
                saved_msg_id=saved_msg_id,
                remote_msg_id=sent_reply_id,
                alert_type="afk",
            )
        except Exception:
            pass
