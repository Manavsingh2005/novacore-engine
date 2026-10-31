"""
Ultroid-Neo Plugin: Smart Conversational PM-Permit & Anti-Report Shield (Phase 4)
Engineered to 100% prevent SpamBot account restrictions:
1. Context-Aware AI Greeting (Groq): Replies like a natural executive assistant.
2. 24-Hour Hard Rate Cap: Max 1 auto-reply per stranger every 24 hours.
3. Zero Auto-Bans / Zero Robotic Warnings: Completely eliminates "Report Spam" strikes.
4. Auto-Exemption: Contacts and official Telegram bots bypass automatically.
5. In-Chat Controls: .approve, .disapprove, .listapproved, .setpmai, .setpmmsg, .pmstats, .block, .unblock
"""

from __future__ import annotations

import logging
import time
from telethon import events
from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db
from core.ai_pool import ai_pool
from core.config import settings
from helpers.formatters import make_card, format_uptime

logger = logging.getLogger("ultroid_neo.plugin.pmpermit")


@client_cmd(
    pattern="pmpermit",
    description="Toggle PM-Permit protection (.pmpermit on/off)",
    module="pmpermit",
)
async def pmpermit_toggle(event: SafeMessageWrapper, mode: str = ""):
    mode = mode.lower().strip() if mode else ""
    if mode in ("on", "enable", "true"):
        await db.set_setting("pmpermit_enabled", "true")
        await event.edit("🛡️ **PM-Permit Shield:** `ACTIVATED (Anti-Report Mode)`")
    elif mode in ("off", "disable", "false"):
        await db.set_setting("pmpermit_enabled", "false")
        await event.edit("🔓 **PM-Permit Shield:** `DEACTIVATED`")
    else:
        current = await db.get_setting("pmpermit_enabled", "false")
        status = "Active 🟢" if current == "true" else "Inactive ⚪"
        ai_mode = await db.get_setting("pmpermit_ai", "true")
        await event.edit(
            f"🛡️ **PM-Permit Status:** `{status}`\n"
            f"• AI Conversational Mode: `{'Enabled 🧠' if ai_mode == 'true' else 'Static 📝'}`\n\n"
            f"• Use `.pmpermit on` to enable.\n"
            f"• Use `.pmpermit off` to disable.\n"
            f"• Use `.pmstats` for detailed metrics."
        )


@client_cmd(
    pattern="setpmai",
    description="Toggle Groq AI conversational replies for PM-Permit (.setpmai on/off)",
    module="pmpermit",
)
async def set_pm_ai_cmd(event: SafeMessageWrapper, mode: str = ""):
    mode = mode.lower().strip() if mode else ""
    if mode in ("on", "enable", "true"):
        await db.set_setting("pmpermit_ai", "true")
        await event.edit("🧠 **PM-Permit AI Mode:** `ENABLED (Groq Conversational Assistant)`")
    elif mode in ("off", "disable", "false"):
        await db.set_setting("pmpermit_ai", "false")
        await event.edit("📝 **PM-Permit AI Mode:** `DISABLED (Static Template Only)`")
    else:
        current = await db.get_setting("pmpermit_ai", "true")
        state = "Enabled 🧠" if current == "true" else "Disabled 📝"
        await event.edit(f"🧠 **PM-Permit AI Mode is currently:** `{state}`")


@client_cmd(
    pattern="setpmmsg",
    description="Set custom fallback greeting for PM-Permit (.setpmmsg <text>)",
    module="pmpermit",
)
async def set_pm_msg_cmd(event: SafeMessageWrapper, text_arg: str = ""):
    custom_msg = text_arg.strip()
    if not custom_msg:
        await event.edit("❌ **Usage:** `.setpmmsg <your custom message>`")
        return

    await db.set_setting("custom_pm_message", custom_msg)
    await event.edit(f"✅ **Custom PM Greeting Saved:**\n\n_{custom_msg}_")


@client_cmd(
    pattern="resetpmmsg",
    description="Reset PM-Permit message to default template",
    module="pmpermit",
)
async def reset_pm_msg_cmd(event: SafeMessageWrapper):
    await db.set_setting("custom_pm_message", "")
    await event.edit("🔄 **PM-Permit greeting reset to default.**")


@client_cmd(
    pattern=r"approve|a",
    description="Approve user to message you in PM (.approve or .a)",
    module="pmpermit",
)
async def approve_pm(event: SafeMessageWrapper):
    target_id = None
    reply = await event.get_reply_message()

    if reply and reply.sender_id:
        target_id = reply.sender_id
    elif event.is_private:
        target_id = event.chat_id

    if not target_id:
        await event.edit("❌ Reply to a user or use inside their private chat.")
        return

    await db.approve_pm(target_id)
    await event.edit(f"✅ User `[{target_id}]` has been **approved** to direct message.")


@client_cmd(
    pattern=r"disapprove|da",
    description="Revoke PM approval for a user (.disapprove or .da)",
    module="pmpermit",
)
async def disapprove_pm(event: SafeMessageWrapper):
    target_id = None
    reply = await event.get_reply_message()

    if reply and reply.sender_id:
        target_id = reply.sender_id
    elif event.is_private:
        target_id = event.chat_id

    if not target_id:
        await event.edit("❌ Reply to a user or use inside their private chat.")
        return

    await db.disapprove_pm(target_id)
    await event.edit(f"🔒 Approval for user `[{target_id}]` has been **revoked**.")


@client_cmd(
    pattern="listapproved",
    description="List all currently approved direct message users",
    module="pmpermit",
)
async def list_approved_cmd(event: SafeMessageWrapper):
    approved = await db.get_approved_users()
    if not approved:
        await event.edit("📂 **No users currently approved via PM-Permit.**")
        return

    lines = [
        f"📋 **Approved PM Users ({len(approved)}):**",
        "──────────────────────────────",
    ]
    for row in approved[:50]:
        uid = row["user_id"]
        lines.append(f"• User ID: `[{uid}]` (Approved)")

    if len(approved) > 50:
        lines.append(f"*(and {len(approved) - 50} more...)*")

    lines.append("──────────────────────────────")
    lines.append("💡 *Use `.disapprove` to revoke access.*")
    await event.edit("\n".join(lines))


@client_cmd(
    pattern="pmstats",
    description="Display PM-Permit screening metrics and counts",
    module="pmpermit",
)
async def pm_stats_cmd(event: SafeMessageWrapper):
    stats = await db.get_pm_stats()
    is_enabled = await db.get_setting("pmpermit_enabled", "false")
    ai_mode = await db.get_setting("pmpermit_ai", "true")

    card = make_card(
        title="🛡️ PM-Permit Shield Metrics",
        fields=[
            ("Shield Status", "Active 🟢" if is_enabled == "true" else "Inactive ⚪"),
            ("AI Assistant Mode", "Enabled 🧠 (Groq)" if ai_mode == "true" else "Static 📝"),
            ("Approved Contacts", str(stats.get("approved", 0))),
            ("Pending Inquiries", str(stats.get("pending", 0))),
            ("Total Screened", str(stats.get("total", 0))),
            ("Spam Protection", "Strict 24h Rate-Cap Active"),
        ],
        footer="Zero-Ban Shield • No robotic warnings or auto-blocks.",
    )
    await event.edit(card)


@client_cmd(
    pattern="block",
    description="Block user explicitly (.block)",
    module="pmpermit",
)
async def block_user_cmd(event: SafeMessageWrapper):
    target_id = None
    reply = await event.get_reply_message()
    if reply and reply.sender_id:
        target_id = reply.sender_id
    elif event.is_private:
        target_id = event.chat_id

    if not target_id:
        await event.edit("❌ Reply to a user or use inside their private chat.")
        return

    try:
        await event.client(BlockRequest(target_id))
        await db.disapprove_pm(target_id)
        await event.edit(f"⛔ User `[{target_id}]` has been **blocked**.")
    except Exception as e:
        await event.edit(f"❌ Failed to block: `{e}`")


@client_cmd(
    pattern="unblock",
    description="Unblock user explicitly (.unblock)",
    module="pmpermit",
)
async def unblock_user_cmd(event: SafeMessageWrapper):
    target_id = None
    reply = await event.get_reply_message()
    if reply and reply.sender_id:
        target_id = reply.sender_id
    elif event.is_private:
        target_id = event.chat_id

    if not target_id:
        await event.edit("❌ Reply to a user or use inside their private chat.")
        return

    try:
        await event.client(UnblockRequest(target_id))
        await event.edit(f"🔓 User `[{target_id}]` has been **unblocked**.")
    except Exception as e:
        await event.edit(f"❌ Failed to unblock: `{e}`")


# --- Passive Incoming PM Handler (Anti-Report Guardian) ---
async def incoming_pm_handler(event: events.NewMessage.Event):
    """
    Passively monitors incoming direct messages.
    Guarantees:
    - 1-message-per-24h hard limit to avoid spam strikes.
    - AI-assisted polite acknowledgment.
    - Safe alert forwarding to Saved Messages.
    """
    if not event.is_private or event.out:
        return

    is_enabled = await db.get_setting("pmpermit_enabled", "false")
    if is_enabled != "true":
        return

    sender = await event.get_sender()
    if not sender or getattr(sender, "bot", False) or getattr(sender, "is_self", False):
        return

    user_id = event.chat_id

    # Auto-allow contacts or already approved users
    if getattr(sender, "contact", False) or await db.is_pm_approved(user_id):
        return

    # Check 24-hour rate limit: STRICTLY max 1 auto-reply per stranger every 24h
    last_interaction = await db.get_pm_last_interaction(user_id)
    now = time.time()
    if now - last_interaction < 86400:  # 24 hours
        # SILENCE: Absorb subsequent messages silently. Never spam them back!
        return

    # Record interaction timestamp
    await db.record_pm_interaction(user_id)

    # Fetch owner name
    me = await event.client.get_me()
    owner_name = me.first_name if me else "My master"

    # Determine auto-reply text (AI vs. Custom Static)
    ai_mode = await db.get_setting("pmpermit_ai", "true")
    reply_text = ""

    if ai_mode == "true" and ai_pool.key_count > 0:
        try:
            stranger_text = event.text or "a media attachment"
            system_prompt = (
                f"You are the polite personal assistant of {owner_name}.\n"
                f"A stranger on Telegram just sent a message while {owner_name} is away.\n"
                f"Write a friendly, polite 2-sentence auto-response acknowledging what they contacted about, "
                f"confirming their message was saved, and saying {owner_name} will reply as soon as they are online.\n"
                f"Never sound robotic or aggressive. Do NOT mention warnings or bans. "
                f"Return ONLY the response message text."
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": stranger_text[:300]},
            ]
            ai_reply = await ai_pool.chat_completion(messages, temperature=0.7, max_tokens=150)
            if ai_reply:
                reply_text = ai_reply.strip()
        except Exception as e:
            logger.warning("PM-Permit AI failed, falling back to static: %s", e)

    if not reply_text:
        custom_static = await db.get_setting("custom_pm_message")
        if custom_static:
            reply_text = custom_static
        else:
            reply_text = (
                f"👋 **Hello!**\n\n"
                f"I am {owner_name}'s assistant. {owner_name} is currently away or unavailable.\n\n"
                f"I have safely forwarded your message to their notification feed, and they will get back to you as soon as they are online!\n\n"
                f"*(Please avoid sending multiple messages in the meantime)*"
            )

    try:
        await event.reply(reply_text)
    except Exception as e:
        logger.warning("Could not send polite PM notice to %d: %s", user_id, e)

    # Forward alert to user's Saved Messages
    alert = (
        f"🔔 **New Screened PM Inquiry**\n"
        f"• **From:** [{sender.first_name}](tg://user?id={user_id})\n"
        f"• **Message:** _{event.text[:200] if event.text else 'Media Attachment'}_\n"
        f"• *Approve with:* `.approve`"
    )
    try:
        await event.client.send_message("me", alert)
    except Exception:
        pass
