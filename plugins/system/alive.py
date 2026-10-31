"""
NovaCore Plugin: Status & Health Dashboard (.alive, .setalive, .resetalive)
Displays real-time uptime, subscription status (Free vs Premium),
AI pool metrics, and active model with customizable bio/card footer.
"""

from __future__ import annotations

import sys
import time
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db
from core.ai_pool import ai_pool
from helpers.formatters import make_card, format_uptime

BOT_START_TIME = time.time()


@client_cmd(
    pattern="alive",
    description="Display automation engine status, subscription tier, and AI pool health",
    module="system",
)
async def alive_cmd(event: SafeMessageWrapper):
    me = await event.client.get_me()
    is_premium = bool(getattr(me, "premium", False))
    tier_label = "Telegram Premium 💎" if is_premium else "Standard (Free Tier) ⚡"

    uptime_str = format_uptime(BOT_START_TIME)
    current_model = await ai_pool.get_current_model()
    custom_footer = await db.get_setting("custom_alive_footer")
    footer_text = custom_footer or "Next-Gen AI Automation Engine • Fully Async & Lightweight"

    card = make_card(
        title="🤖 NovaCore 2026 Engine",
        fields=[
            ("Owner", f"{me.first_name} (`{me.id}`)"),
            ("Account Tier", tier_label),
            ("Active AI Model", current_model),
            ("AI Key Pool", f"{ai_pool.key_count} Groq Keys (Round-Robin)"),
            ("Guardian Shield", "Armed & Active (Autonomous Mode)"),
            ("Uptime", uptime_str),
            ("Python Runtime", sys.version.split()[0]),
        ],
        footer=footer_text,
        is_premium=is_premium,
    )
    await event.edit(card)


@client_cmd(
    pattern="setalive",
    description="Customize the footer message on the .alive status card (.setalive <text>)",
    module="system",
)
async def set_alive_cmd(event: SafeMessageWrapper, text_arg: str = ""):
    custom_text = text_arg.strip()
    if not custom_text:
        await event.edit("❌ **Usage:** `.setalive <your custom footer text>`")
        return

    await db.set_setting("custom_alive_footer", custom_text)
    await event.edit(f"✅ **Custom Alive Footer Updated:**\n_{custom_text}_")


@client_cmd(
    pattern="resetalive",
    description="Reset the .alive footer message to default",
    module="system",
)
async def reset_alive_cmd(event: SafeMessageWrapper):
    await db.set_setting("custom_alive_footer", "")
    await event.edit("🔄 **Alive footer reset to default.**")
