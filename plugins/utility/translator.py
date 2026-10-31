"""
NovaCore Plugin: Real-Time Multi-Language Translator (.tr)
Uses NovaCore's N-Key AI Pool to provide instant, human-grade translation
supporting 100+ languages, slang, idioms, and code formatting.
"""

from __future__ import annotations

import logging
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.ai_pool import ai_pool

logger = logging.getLogger("novacore.plugin.utility.translator")

# Common language aliases
LANG_MAP = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese (Simplified)",
    "hi": "Hindi",
    "ar": "Arabic",
    "tr": "Turkish",
    "id": "Indonesian",
    "vi": "Vietnamese",
    "th": "Thai",
    "pl": "Polish",
    "uk": "Ukrainian",
    "nl": "Dutch",
    "el": "Greek",
    "he": "Hebrew",
    "fa": "Persian",
    "ur": "Urdu",
    "bn": "Bengali",
    "pa": "Punjabi",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "gu": "Gujarati",
}


@client_cmd(
    pattern="tr",
    description="Translate text or replied message into any language (.tr <lang> [text])",
    module="utility",
)
async def translate_cmd(event: SafeMessageWrapper, args: str = ""):
    parts = args.strip().split(maxsplit=1) if args else []
    target_lang = "en"
    inline_text = ""

    if parts:
        candidate_code = parts[0].lower()
        if candidate_code in LANG_MAP:
            target_lang = candidate_code
            inline_text = parts[1] if len(parts) > 1 else ""
        elif len(candidate_code) in (2, 3) and candidate_code.isalpha():
            # Likely an ISO language code
            target_lang = candidate_code
            inline_text = parts[1] if len(parts) > 1 else ""
        else:
            # First arg was part of the text, default to English
            target_lang = "en"
            inline_text = args.strip()

    source_text = inline_text
    if not source_text:
        reply = await event.get_reply_message()
        if reply and reply.text:
            source_text = reply.text
        elif reply and reply.caption:
            source_text = reply.caption

    if not source_text:
        await event.edit(
            "❌ **Usage:**\n"
            "• Reply to any message: `.tr <lang_code>` (e.g. `.tr en`, `.tr es`, `.tr hi`)\n"
            "• Translate directly: `.tr <lang_code> <your text>`\n"
            "• Default translation is English (`.tr`)."
        )
        return

    lang_display_name = LANG_MAP.get(target_lang, target_lang.upper())
    await event.edit(f"🌐 **Translating into {lang_display_name}...**")

    system_prompt = (
        f"You are a master real-time translator. "
        f"Translate the provided text accurately and naturally into {lang_display_name}. "
        "Strict rules:\n"
        "1. Output ONLY the translated text. Do not wrap the entire response in markdown code blocks unless the input was code.\n"
        "2. Do NOT add conversational filler, preambles, notes, or explanations.\n"
        "3. Preserve all original emojis, links, @mentions, and line breaks exactly."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": source_text},
    ]

    try:
        translated = await ai_pool.chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=2000,
        )

        response_text = (
            f"🌐 **Translation ➔ {lang_display_name}**\n"
            "──────────────────────────────\n"
            f"{translated.strip()}\n"
            "──────────────────────────────\n"
            "⚡ *NovaCore Neural Translation*"
        )
        await event.edit(response_text)
    except Exception as e:
        logger.exception("Translation error: %s", e)
        await event.edit(f"❌ **Translation Failed:** `{e}`")
