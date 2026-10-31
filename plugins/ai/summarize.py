"""
Ultroid-Neo Plugin: Thread & Forum Topic Summarizer (.sum)
Reads the last N messages in the current chat or forum topic and produces
a structured summary with key topics, conclusions, and action items.
"""

from __future__ import annotations

import logging
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.ai_pool import ai_pool

logger = logging.getLogger("ultroid_neo.plugin.ai.summarize")


@client_cmd(
    pattern="sum",
    description="Summarize recent chat or forum topic messages (.sum [count])",
    module="ai",
)
async def summarize_cmd(event: SafeMessageWrapper, count_str: str = ""):
    count = int(count_str) if count_str and count_str.isdigit() else 50
    count = min(max(count, 10), 200)

    scope_desc = "current topic" if event.topic_id else "chat"
    await event.edit(f"⏳ **Reading last {count} messages in {scope_desc}...**")

    # Fetch messages respecting topic boundaries
    messages_log = []
    iter_kwargs = {"limit": count}
    if event.topic_id:
        iter_kwargs["reply_to"] = event.topic_id

    async for msg in event.client.iter_messages(event.chat_id, **iter_kwargs):
        # Skip userbot commands themselves
        if msg.text and not msg.text.startswith("."):
            sender = "Unknown"
            if msg.sender:
                sender = getattr(msg.sender, "first_name", "") or getattr(
                    msg.sender, "title", "User"
                )
            clean_text = msg.text.replace("\n", " ").strip()
            messages_log.append(f"{sender}: {clean_text}")

    if not messages_log:
        await event.edit("⚠️ **No readable text messages found to summarize.**")
        return

    # Chronological order
    conversation_text = "\n".join(reversed(messages_log))
    await event.edit(f"🧠 **Summarizing {len(messages_log)} messages via Groq LPU...**")

    system_prompt = (
        "You are an expert executive summarizer. Analyze the provided Telegram group/topic conversation transcript.\n"
        "Output a clean, high-density structured summary using standard Telegram markdown with these sections:\n"
        "• 📌 **Core Discussion Points**\n"
        "• 💡 **Decisions & Conclusions**\n"
        "• 📋 **Action Items & Mentioned Tasks**\n"
        "Be concise, clear, and omit idle chit-chat."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Here is the conversation log ({len(messages_log)} messages):\n\n{conversation_text}",
        },
    ]

    try:
        summary = await ai_pool.chat_completion(messages, max_tokens=1200)
        topic_header = f"in Topic `#{event.topic_id}`" if event.topic_id else ""
        header = f"📊 **Executive Summary ({len(messages_log)} msgs) {topic_header}:**\n\n"
        full_text = header + summary

        if len(full_text) > 4000:
            full_text = full_text[:3990] + "...\n*(truncated)*"

        await event.edit(full_text)
    except Exception as e:
        await event.edit(f"❌ **Summarization Failed:** `{e}`")
