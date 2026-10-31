"""
NovaCore Plugin: Instant AI Query & Conversational Memory Thread (.ask / .ai)
Powered by Groq's LPU inference engine (250-300+ tokens/sec).
Supports:
- Multi-turn conversation buffer: Automatically preserves context across recent messages.
- Long-term memory recall: Integrates all facts stored via .remember.
- Auto-memory extraction: Automatically remembers when you say "remember that...", "my dog is...", etc.
- Multimodal Vision: Reply to any image or screenshot with .ask <query>.
"""

from __future__ import annotations

import base64
import logging
import re
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.ai_pool import ai_pool
from core.database import db

logger = logging.getLogger("novacore.plugin.ai.ask")


@client_cmd(
    pattern=r"ask|ai",
    description="Ask AI with persistent memory & Vision (.ask <question> or reply to message/photo)",
    module="ai",
)
async def ask_cmd(event: SafeMessageWrapper, query: str = ""):
    reply = await event.get_reply_message()
    prompt = query.strip()

    # Check for image attachment on replied message (Multimodal Vision)
    has_image = bool(
        reply and (
            reply.photo
            or (
                reply.document
                and getattr(reply, "file", None)
                and getattr(reply.file, "mime_type", "").startswith("image/")
            )
        )
    )

    if not prompt and reply and reply.text and not has_image:
        prompt = reply.text.strip()

    if not prompt and not has_image:
        await event.edit(
            "❌ **Usage:** `.ask <prompt>` or reply to a message/photo with `.ask`\n"
            "💡 *Conversational memory automatically preserves thread context!*\n"
            "🧠 *Use `.remember <fact>` to save permanent facts.*"
        )
        return

    # Auto-detect explicit memory intentions in prompt (e.g. "remember my dog's name is Dogo", "name it Dogo")
    auto_remember_match = re.search(
        r"(?:remember\s+(?:that\s+)?|my\s+(\w+)\s+(?:is|named)\s+|name\s+(?:it|him|her)\s+)(.+)",
        prompt,
        re.IGNORECASE,
    )
    if auto_remember_match:
        fact_text = prompt
        try:
            await db.add_or_update_memory(key_fact=fact_text[:80], value_text=fact_text, category="auto_learned")
        except Exception:
            pass

    image_payload = None
    if has_image:
        await event.edit("🖼️ **Processing image for Groq Vision analysis...**")
        raw_bytes = await event.client.download_media(reply, file=bytes)
        if raw_bytes:
            b64_data = base64.b64encode(raw_bytes).decode("utf-8")
            mime = getattr(getattr(reply, "file", None), "mime_type", "image/jpeg") or "image/jpeg"
            image_payload = f"data:{mime};base64,{b64_data}"
        else:
            await event.edit("⚠️ Failed to download image for vision.")

    await event.edit("⚡ **Thinking via Groq LPU...**")

    # 1. Fetch long-term memories to inject into system prompt
    stored_memories = await db.get_all_memories()
    memory_context = ""
    if stored_memories:
        memory_lines = [f"- {m['key_fact']}: {m['value_text']}" for m in stored_memories]
        memory_context = (
            "\n\n[Persistent Long-Term User Memories & Facts]:\n"
            + "\n".join(memory_lines)
            + "\nAlways reference and respect these known user facts seamlessly."
        )

    system_content = (
        "You are NovaCore, an elite, highly intelligent, and helpful personal AI assistant. "
        "Keep answers direct, informative, insightful, and formatted with clean Telegram markdown. "
        "You possess persistent memory of user facts and conversation history."
        + memory_context
    )

    messages = [{"role": "system", "content": system_content}]

    # 2. Build multi-turn conversation buffer from recent chat messages
    # If in Saved Messages or reply thread, fetch previous messages for natural continuity
    recent_dialogue = []
    try:
        iter_kwargs = {"limit": 10}
        async for msg in event.client.iter_messages(event.chat_id, **iter_kwargs):
            if msg.id == event.id:
                continue
            if not msg.text:
                continue
            # Check if it was an AI response
            if "🧠 **AI Response**" in msg.text or "🧠" in msg.text:
                clean_ans = msg.text.split("━━━━━━━━━━━━━━━━━━━━")[-1].strip() if "━━━━━━━━━━━━━━━━━━━━" in msg.text else msg.text
                recent_dialogue.append({"role": "assistant", "content": clean_ans[:1200]})
            elif not msg.text.startswith("."):
                # Clean user message
                recent_dialogue.append({"role": "user", "content": msg.text[:1200]})
            elif msg.text.startswith(".ask "):
                clean_prompt = msg.text[5:].strip()
                if clean_prompt:
                    recent_dialogue.append({"role": "user", "content": clean_prompt[:1200]})
    except Exception as e:
        logger.debug("Could not fetch conversation buffer: %s", e)

    # Add previous messages in chronological order (up to 6 turns)
    if recent_dialogue:
        messages.extend(list(reversed(recent_dialogue))[-6:])

    # 3. Add current turn
    if image_payload:
        user_content = [
            {
                "type": "text",
                "text": prompt or "Describe and analyze what you see in this image in detail.",
            },
            {"type": "image_url", "image_url": {"url": image_payload}},
        ]
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": prompt})

    try:
        current_model = await ai_pool.get_current_model()
        response_text = await ai_pool.chat_completion(messages)
        full_output = f"🧠 **AI Response** (`{current_model}`)\n━━━━━━━━━━━━━━━━━━━━\n{response_text}"
        if len(full_output) > 4000:
            full_output = full_output[:3990] + "...\n*(truncated)*"
        await event.edit(full_output)
    except Exception as e:
        await event.edit(f"❌ **AI Query Failed:** `{e}`")
