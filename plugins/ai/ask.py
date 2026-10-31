"""
Ultroid-Neo Plugin: Instant AI Query & Conversational Thread (.ask / .ai)
Powered by Groq's LPU inference engine (250-300+ tokens/sec).
Supports:
- Direct queries: .ask <prompt>
- Inquiring about replied messages: reply to any message with .ask <question>
- Multi-turn conversation memory: replying to a previous AI response continues the dialogue!
"""

from __future__ import annotations

import base64
import logging
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.ai_pool import ai_pool

logger = logging.getLogger("ultroid_neo.plugin.ai.ask")


@client_cmd(
    pattern=r"ask|ai",
    description="Ask Groq AI anything instantly (.ask <question>, reply to message, or reply to photo for Vision analysis)",
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
            "💡 *Replying to an existing AI answer continues the conversational thread!*\n"
            "🖼️ *Replying to any photo or screenshot activates Groq Vision!*"
        )
        return

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

    messages = [
        {
            "role": "system",
            "content": (
                "You are an intelligent, concise, and helpful Telegram AI assistant. "
                "Keep answers direct, informative, and formatted with clean Telegram markdown. "
                "Avoid unnecessary filler or repetitive pleasantries."
            ),
        }
    ]

    # Context continuity: if replying to a previous AI response, preserve history!
    if reply and reply.text and ("AI Response:" in reply.text or "🧠" in reply.text):
        prior_ai_text = reply.text.replace("🧠 **AI Response:**", "").replace("🧠", "").strip()
        messages.append({"role": "assistant", "content": prior_ai_text[:1000]})

    if image_payload:
        user_content = [
            {
                "type": "text",
                "text": prompt or "Describe and explain what you see in this image in detail.",
            },
            {"type": "image_url", "image_url": {"url": image_payload}},
        ]
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": prompt})

    try:
        current_model = await ai_pool.get_current_model()
        response_text = await ai_pool.chat_completion(messages)
        full_output = f"🧠 **AI Response** (`{current_model}`):\n\n{response_text}"
        if len(full_output) > 4000:
            full_output = full_output[:3990] + "...\n*(truncated)*"
        await event.edit(full_output)
    except Exception as e:
        await event.edit(f"❌ **AI Query Failed:** `{e}`")
