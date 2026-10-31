"""
NovaCore Plugin: Long-Term Memory Engine (.remember, .memories, .forget)
Allows NovaCore to remember personal facts, preferences, project details,
and names across conversations and server restarts.
"""

from __future__ import annotations

import logging
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db
from helpers.formatters import make_card

logger = logging.getLogger("novacore.plugin.ai.memory")


@client_cmd(
    pattern="remember",
    description="Store a permanent fact in NovaCore's long-term memory (.remember <fact> or key=val)",
    module="ai",
)
async def remember_cmd(event: SafeMessageWrapper, query: str = ""):
    text = query.strip()
    reply = await event.get_reply_message()

    if not text and reply and reply.text:
        text = reply.text.strip()

    if not text:
        await event.edit(
            "❌ **Usage:** `.remember <fact>` or `.remember <key> = <value>`\n\n"
            "• *Example:* `.remember my dog's name is Dogo`\n"
            "• *Example:* `.remember timezone = IST`\n"
            "• *Example:* `.remember favorite food = Sushi`\n"
            "💡 *NovaCore will automatically use these facts during .ask conversations!*"
        )
        return

    # Parse key-value or extract core fact
    if "=" in text:
        key_fact, val = text.split("=", 1)
        key_fact = key_fact.strip()
        val = val.strip()
    elif text.lower().startswith("that "):
        cleaned = text[5:].strip()
        key_fact = cleaned
        val = cleaned
    else:
        key_fact = text
        val = text

    await db.add_or_update_memory(key_fact=key_fact, value_text=val, category="user_fact")
    await event.edit(
        f"🧠 **Stored in Long-Term Memory:**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **Fact:** `{key_fact}`\n"
        f"💡 **Value:** `{val}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"> *I will remember this in all future conversations.*"
    )


@client_cmd(
    pattern="memories",
    description="List all facts stored in NovaCore's long-term memory (.memories)",
    module="ai",
)
async def list_memories_cmd(event: SafeMessageWrapper):
    memories = await db.get_all_memories()
    if not memories:
        await event.edit(
            "🧠 **Long-Term Memory is Empty.**\n\n"
            "Teach me things using `.remember <fact>`\n"
            "*(e.g. `.remember my dog's name is Dogo`)*"
        )
        return

    lines = [
        f"🧠 **NovaCore Long-Term Memory ({len(memories)} facts)**",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for m in memories:
        lines.append(f"• **[#{m['id']}]** `{m['key_fact']}`: _{m['value_text']}_")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("> 💡 *Forget a fact using: `.forget <id>` or `.forget <name>`*")
    await event.edit("\n".join(lines))


@client_cmd(
    pattern="forget",
    description="Delete a fact from long-term memory (.forget <id or fact>)",
    module="ai",
)
async def forget_cmd(event: SafeMessageWrapper, query: str = ""):
    arg = query.strip()
    if not arg:
        await event.edit("❌ **Usage:** `.forget <id>` or `.forget <fact_name>`\nUse `.memories` to view IDs.")
        return

    success = await db.delete_memory(arg)
    if success:
        await event.edit(f"🗑️ **Forgotten:** Successfully removed `{arg}` from memory.")
    else:
        await event.edit(f"⚠️ Memory item `{arg}` not found.")
