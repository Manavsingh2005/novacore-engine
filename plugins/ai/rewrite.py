"""
Ultroid-Neo Plugin: Message Tone & Style Suite (.rewrite, .fix, .explain)
Polishes draft messages, fixes grammar/spelling, and explains complex text or code.
"""

from __future__ import annotations

import logging
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.ai_pool import ai_pool

logger = logging.getLogger("ultroid_neo.plugin.ai.rewrite")

STYLES = {
    "prof": "formal, polished, and executive professional",
    "professional": "formal, polished, and executive professional",
    "short": "extremely concise and direct, removing all fluff",
    "concise": "extremely concise and direct, removing all fluff",
    "casual": "friendly, warm, and natural conversational",
    "friendly": "friendly, warm, and natural conversational",
    "sarcastic": "witty, playfully sarcastic, and humorous",
    "roast": "clever, comedic, and slightly roasting",
}


@client_cmd(
    pattern="rewrite",
    description="Rewrite message tone (.rewrite [prof/short/casual/sarcastic] [text or reply])",
    module="ai",
)
async def rewrite_cmd(event: SafeMessageWrapper, args: str = ""):
    parts = args.split(maxsplit=1) if args else []
    style_key = parts[0].lower() if parts else "professional"
    text_content = parts[1].strip() if len(parts) > 1 else ""

    if not text_content:
        reply = await event.get_reply_message()
        if reply and reply.text:
            text_content = reply.text.strip()

    if not text_content:
        await event.edit(
            "❌ **Usage:** `.rewrite [style] <text>` or reply to a message.\n"
            "**Styles:** `professional` (or `prof`), `concise` (or `short`), `casual`, `sarcastic`"
        )
        return

    style_desc = STYLES.get(style_key, f"tailored to: {style_key}")
    await event.edit(f"✨ **Refining message ({style_key})...**")

    system_prompt = (
        f"You are an expert communication specialist. Rewrite the user's message to be {style_desc}.\n"
        "Return ONLY the rewritten message text without explanations or quotation marks."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text_content},
    ]

    try:
        rewritten = await ai_pool.chat_completion(messages, temperature=0.6)
        await event.edit(rewritten.strip())
    except Exception as e:
        await event.edit(f"❌ **Rewrite Failed:** `{e}`")


@client_cmd(
    pattern="fix",
    description="Fix grammar, spelling, and phrasing (.fix <text> or reply)",
    module="ai",
)
async def fix_grammar_cmd(event: SafeMessageWrapper, text_arg: str = ""):
    text_content = text_arg.strip()
    if not text_content:
        reply = await event.get_reply_message()
        if reply and reply.text:
            text_content = reply.text.strip()

    if not text_content:
        await event.edit("❌ **Usage:** `.fix <text>` or reply to a message to fix grammar.")
        return

    await event.edit("✍️ **Fixing grammar and phrasing...**")

    system_prompt = (
        "You are a master editor. Fix all grammar, spelling, punctuation, and syntax errors in the provided text. "
        "Keep the original tone, meaning, and voice intact. "
        "Return ONLY the corrected text without any preamble, explanations, or quotes."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text_content},
    ]

    try:
        corrected = await ai_pool.chat_completion(messages, temperature=0.3)
        await event.edit(corrected.strip())
    except Exception as e:
        await event.edit(f"❌ **Fix Failed:** `{e}`")


@client_cmd(
    pattern="explain",
    description="Explain text, error log, or code simply (.explain <text> or reply)",
    module="ai",
)
async def explain_cmd(event: SafeMessageWrapper, text_arg: str = ""):
    text_content = text_arg.strip()
    if not text_content:
        reply = await event.get_reply_message()
        if reply and reply.text:
            text_content = reply.text.strip()

    if not text_content:
        await event.edit("❌ **Usage:** `.explain <text>` or reply to an error/code/concept.")
        return

    await event.edit("💡 **Analyzing and explaining...**")

    system_prompt = (
        "You are an expert tutor and senior engineer. Explain the user's text, code, or error message "
        "clearly, simply, and accurately. Break down what it means, why it happened (if error), "
        "and provide actionable insights. Format with clean Telegram markdown."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text_content},
    ]

    try:
        explanation = await ai_pool.chat_completion(messages, temperature=0.5)
        header = "💡 **Explanation:**\n\n"
        full_text = header + explanation
        if len(full_text) > 4000:
            full_text = full_text[:3990] + "...\n*(truncated)*"
        await event.edit(full_text)
    except Exception as e:
        await event.edit(f"❌ **Explain Failed:** `{e}`")
