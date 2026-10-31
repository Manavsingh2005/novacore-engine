"""
Ultroid-Neo Plugin: Neural Text-to-Speech Voice Note Generator (.tts / .say)
Powered by Microsoft Azure Cognitive Neural Voice models via edge-tts.
Produces natural, human-grade speech complete with breath sounds and inflection,
sent as an official Telegram Voice Note with waveform!
"""

from __future__ import annotations

import logging
import os
import tempfile
import edge_tts
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db

logger = logging.getLogger("ultroid_neo.plugin.media.tts")

# Popular, ultra-realistic neural voices and easy shortcuts
VOICE_MAP = {
    "narrator": "en-US-ChristopherNeural",
    "christopher": "en-US-ChristopherNeural",
    "guy": "en-US-GuyNeural",
    "jenny": "en-US-JennyNeural",
    "aria": "en-US-AriaNeural",
    "eric": "en-US-EricNeural",
    "ana": "en-US-AnaNeural",
    "ryan": "en-GB-RyanNeural",
    "sonia": "en-GB-SoniaNeural",
    "prabhat": "en-IN-PrabhatNeural",
    "neerja": "en-IN-NeerjaNeural",
    "keita": "ja-JP-KeitaNeural",
    "nanami": "ja-JP-NanamiNeural",
    "alvaro": "es-ES-AlvaroNeural",
    "elvira": "es-ES-ElviraNeural",
    "henri": "fr-FR-HenriNeural",
    "denise": "fr-FR-DeniseNeural",
}

DEFAULT_VOICE = "en-US-ChristopherNeural"


@client_cmd(
    pattern=r"tts|say",
    description="Convert text to realistic neural voice note (.tts <text> or reply)",
    module="media",
)
async def tts_cmd(event: SafeMessageWrapper, query: str = ""):
    reply = await event.get_reply_message()
    text = query.strip()

    if not text and reply and reply.text:
        text = reply.text.strip()

    if not text:
        await event.edit(
            "❌ **Usage:** `.tts <text>` or reply to a text message with `.tts`\n"
            "💡 *Use `.voices` to view available human neural voices.*"
        )
        return

    # Check for custom voice in DB
    selected_voice = await db.get_setting("tts_voice", DEFAULT_VOICE)

    await event.edit("🎙️ **Synthesizing neural voice note...**")

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        communicate = edge_tts.Communicate(text, selected_voice)
        await communicate.save(tmp_path)

        # Upload as Telegram Voice Note (with waveform)
        await event.client.send_file(
            event.chat_id,
            file=tmp_path,
            voice_note=True,
            reply_to=reply.id if reply else event.topic_id,
        )
        await event.delete()
    except Exception as e:
        logger.exception("TTS synthesis failed: %s", e)
        await event.edit(f"❌ **TTS Failed:** `{e}`")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@client_cmd(
    pattern="setvoice",
    description="Set default neural voice persona (.setvoice <voice_or_alias>)",
    module="media",
)
async def set_voice_cmd(event: SafeMessageWrapper, voice_arg: str = ""):
    arg = voice_arg.strip().lower()
    if not arg:
        current = await db.get_setting("tts_voice", DEFAULT_VOICE)
        await event.edit(
            f"🎙️ **Current Neural Voice:** `{current}`\n\n"
            f"• Use `.setvoice <alias>` to change.\n"
            f"• Type `.voices` to see all supported voices."
        )
        return

    target_voice = VOICE_MAP.get(arg, voice_arg.strip())
    await db.set_setting("tts_voice", target_voice)
    await event.edit(f"✅ **Active Neural Voice Persona Updated:** `{target_voice}`")


@client_cmd(
    pattern="voices",
    description="List popular human-grade neural voices for .tts",
    module="media",
)
async def list_voices_cmd(event: SafeMessageWrapper):
    current = await db.get_setting("tts_voice", DEFAULT_VOICE)
    lines = [
        "🎭 **Ultra-Realistic Neural Voices**",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🎯 **Currently Selected:** `{current}`",
        "━━━━━━━━━━━━━━━━━━━━",
        "🇺🇸 **English (US):**",
        "• `narrator` / `christopher` *(Studio Narrator - Default)*",
        "• `guy` *(Casual Conversational Male)*",
        "• `jenny` *(Warm Friendly Female)*",
        "• `aria` *(Expressive Storyteller Female)*",
        "",
        "🇬🇧 **English (UK):**",
        "• `ryan` *(British Male)*",
        "• `sonia` *(British Female)*",
        "",
        "🇮🇳 **English (India):**",
        "• `prabhat` *(Indian Male)*",
        "• `neerja` *(Indian Female)*",
        "",
        "🇯🇵 **Japanese:**",
        "• `keita` *(Japanese Male)*",
        "• `nanami` *(Japanese Female)*",
        "━━━━━━━━━━━━━━━━━━━━",
        "> 💡 *Switch voice using: `.setvoice <name>` (e.g. `.setvoice jenny`)*",
    ]
    await event.edit("\n".join(lines))
