"""
Ultroid-Neo Plugin: Voice-to-Text Transcription & Translation (.v2t)
Transcribes incoming audio, voice notes, and round video notes using Groq Whisper.
Supports:
- Ghost Mode (Default in private chats/groups):
  Immediately deletes the .v2t command in the chat, transcribes silently,
  and delivers the transcription to Saved Messages with a 30-minute auto-destruct timer!
- Public Mode (.v2t pub):
  Edits the message in-place in the current chat if you want to share the text.
- Translation Mode (.v2t tr or .v2t pub tr):
  Translates foreign speech into English.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.ai_pool import ai_pool
from core.database import db

logger = logging.getLogger("ultroid_neo.plugin.ai.transcribe")


@client_cmd(
    pattern="v2t",
    description="Transcribe voice note with 30m auto-destruct Ghost Mode to Saved Messages (.v2t [tr] [pub])",
    module="ai",
)
async def transcribe_cmd(event: SafeMessageWrapper, mode: str = ""):
    reply = await event.get_reply_message()
    if not reply or not (reply.voice or reply.audio or reply.video_note):
        await event.edit(
            "❌ **Usage:** Reply to an audio, voice message, or video note with `.v2t`\n\n"
            "• **Ghost Mode (Default):** Deletes `.v2t` in-chat, sends transcription to Saved Messages, & auto-deletes in 30 mins.\n"
            "• **Translation:** Use `.v2t tr` to translate foreign speech to English.\n"
            "• **Public Mode:** Use `.v2t pub` to transcribe directly in current chat."
        )
        return

    mode_lower = mode.lower() if mode else ""
    is_public = any(k in mode_lower for k in ("pub", "public", "here"))
    is_translation = any(k in mode_lower for k in ("tr", "translate"))
    status_msg = "translating to English" if is_translation else "transcribing"

    me = await event.client.get_me()
    is_saved_messages = (event.chat_id == me.id)
    is_ghost = (not is_public) and (not is_saved_messages)

    if is_ghost:
        # Immediately delete .v2t command in current chat so other person sees zero activity
        try:
            await event.delete()
        except Exception as e:
            logger.debug("Failed deleting v2t command: %s", e)
    else:
        await event.edit(f"📥 **Downloading voice note for {status_msg}...**")

    suffix = ".ogg" if reply.voice else (".mp4" if reply.video_note else ".mp3")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await event.client.download_media(reply, file=tmp_path)
        active_whisper = await ai_pool.get_whisper_model()

        if not is_ghost:
            await event.edit(f"🎙️ **Processing via Groq `{active_whisper}` ({status_msg})...**")

        if is_translation:
            text = await ai_pool.translate_audio(tmp_path)
            header = "🌐 **Voice Translation (English):**"
        else:
            text = await ai_pool.transcribe_audio(tmp_path)
            header = "🎙️ **Voice Transcription:**"

        if not text:
            if not is_ghost:
                await event.edit("⚠️ **No audible speech detected.**")
            else:
                await event.client.send_message(
                    "me",
                    f"⚠️ **Voice Transcription Empty:** No audible speech detected in `{active_whisper}`.",
                )
            return

        if is_ghost:
            # Extract sender info
            sender_name = "User"
            if reply.sender:
                sender_name = getattr(reply.sender, "first_name", "") or getattr(reply.sender, "title", "User")
                if getattr(reply.sender, "username", None):
                    sender_name += f" (@{reply.sender.username})"
                elif getattr(reply.sender, "id", None):
                    sender_name += f" (`{reply.sender.id}`)"

            # Extract chat title
            chat = await event.get_chat()
            chat_title = getattr(chat, "title", "") or getattr(chat, "first_name", "Private Chat")

            # Quote text with Telegram blockquote
            quoted_text = "\n".join(f"> {line}" for line in text.splitlines()) if text else "> _(Empty audio)_"

            ghost_card = (
                f"👻 **Ghost Voice Transcription**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **From:** {sender_name}\n"
                f"💬 **Chat:** `{chat_title}`\n"
                f"🎙️ **Model:** `{active_whisper}`\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{quoted_text}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⏳ *Auto-destructs from Saved Messages in 30 minutes.*"
            )

            if len(ghost_card) > 4000:
                ghost_card = ghost_card[:3990] + "...\n*(truncated)*"

            saved_msg = await event.client.send_message("me", ghost_card)

            # Schedule persistent 30-minute auto-deletion in SQLite
            await db.schedule_ephemeral_deletion(saved_msg.chat_id, saved_msg.id, delete_after_seconds=1800)

            # Also spawn immediate async timer
            async def _delete_timer():
                await asyncio.sleep(1800)
                try:
                    await event.client.delete_messages(saved_msg.chat_id, [saved_msg.id])
                except Exception:
                    pass

            asyncio.create_task(_delete_timer())

        else:
            quoted_text = "\n".join(f"> {line}" for line in text.splitlines()) if text else "> _(Empty audio)_"
            output = (
                f"{header}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{quoted_text}"
            )
            if len(output) > 4000:
                output = output[:3990] + "...\n*(truncated)*"
            await event.edit(output)

    except Exception as e:
        logger.exception("V2T failed: %s", e)
        if not is_ghost:
            await event.edit(f"❌ **Audio Processing Failed:** `{e}`")
        else:
            await event.client.send_message("me", f"❌ **Ghost V2T Failed:** `{e}`")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
