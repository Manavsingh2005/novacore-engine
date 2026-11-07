"""
NovaCore: Vocal & Instrumental Splitter (/split, /karaoke)
- Separates audio into Instrumental (Karaoke backing track) and Isolated Vocals (Acapella)
- Powered by FFmpeg acoustic phase cancellation + Groq Whisper AI lyrics transcription.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from telethon import Button
from telethon.tl.types import DocumentAttributeAudio

from core.dispatcher import bot_cmd, bot_callback, SafeMessageWrapper
from core.ai_pool import ai_pool
from helpers.formatters import format_bytes, format_duration

logger = logging.getLogger("novacore.plugin.utility.karaoke")

KARAOKE_SESSIONS = {}


def get_audio_metadata(file_path: str) -> dict:
    """Extracts duration, title, and artist using ffprobe."""
    meta = {"duration": 0, "title": "Track", "artist": "NovaCore"}
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", file_path
        ]
        res = subprocess.check_output(cmd).decode("utf-8")
        import json
        data = json.loads(res)
        fmt = data.get("format", {})
        meta["duration"] = int(float(fmt.get("duration", 0)))
        tags = fmt.get("tags", {})
        meta["title"] = tags.get("title") or tags.get("TITLE") or os.path.splitext(os.path.basename(file_path))[0]
        meta["artist"] = tags.get("artist") or tags.get("ARTIST") or tags.get("album_artist") or "Audio"
    except Exception:
        pass
    return meta


def process_vocal_split(src_path: str, out_path: str, mode: str) -> None:
    """
    Applies high-grade phase inversion & spectral isolation:
    - 'instrumental' / 'karaoke': Inverts center lead vocal channel while retaining stereo width.
    - 'vocals' / 'acapella': Extracts center mono channel with vocal formant bandpass filter (220Hz-4kHz).
    """
    if mode in ["instrumental", "karaoke"]:
        # Phase cancellation of center-panned mono vocal frequencies + bass preserve
        filter_expr = (
            "[0:a]asplit=2[orig][tocancel];"
            "[tocancel]highpass=f=200,lowpass=f=4000,pan=stereo|c0=c0-c1|c1=c1-c0[inv];"
            "[orig]lowpass=f=200[bass];"
            "[inv][bass]amix=inputs=2:weights=1.0 1.2"
        )
    else:
        # Vocal isolation: center-channel summing + vocal presence EQ
        filter_expr = (
            "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,"
            "highpass=f=220,lowpass=f=3800,"
            "volume=1.5"
        )

    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-filter_complex", filter_expr,
        "-c:a", "libmp3lame", "-b:a", "320k",
        out_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@bot_cmd(
    pattern=r"(?:split|karaoke|acapella)(?:\s+(.*))?",
    description="Split songs into Instrumental (Karaoke) and Vocals (/split [mode])",
    module="utility",
)
async def karaoke_cmd(event: SafeMessageWrapper, query: str = ""):
    reply = await event.get_reply_message()
    if not reply or not (
        getattr(reply, "audio", None)
        or getattr(reply, "voice", None)
        or getattr(reply, "video", None)
        or getattr(reply, "document", None)
    ):
        await event.reply(
            "🎤 **Vocal & Instrumental Separator:**\n\n"
            "• Reply `/split instrumental` — Create a Karaoke backing track (removes vocals)\n"
            "• Reply `/split vocals` — Extract isolated lead vocals (acapella)\n"
            "• Reply `/split lyrics` — Extract lyrics & transcript via Groq Whisper AI\n\n"
            "💡 *Tip: Reply `/split` without arguments to see interactive buttons!*"
        )
        return

    raw_arg = (query or "").strip().lower()
    cmd_name = (event.raw_text or "").split()[0].lstrip("/.").lower()

    if cmd_name == "karaoke":
        raw_arg = "instrumental"
    elif cmd_name == "acapella":
        raw_arg = "vocals"

    if not raw_arg:
        token = f"{event.chat_id}_{reply.id}_{int(time.time())}"
        KARAOKE_SESSIONS[token] = {
            "chat_id": event.chat_id,
            "msg_id": reply.id,
            "time": time.time(),
        }
        buttons = [
            [
                Button.inline("🎤 Instrumental (Karaoke)", data=f"krk:instrumental:{token}"),
                Button.inline("🗣️ Isolated Vocals (Acapella)", data=f"krk:vocals:{token}"),
            ],
            [
                Button.inline("📜 AI Lyrics Transcript (Groq)", data=f"krk:lyrics:{token}"),
                Button.inline("❌ Cancel", data=f"krk:cancel:{token}"),
            ],
        ]
        await event.reply("🎵 **Choose Separation Mode:**", buttons=buttons)
        return

    await execute_split_track(event, reply, raw_arg)


@bot_callback(r"^krk:([^:]+):(.+)$")
async def karaoke_callback_handler(event: SafeMessageWrapper):
    data = event.data.decode("utf-8")
    parts = data.split(":", 2)
    mode = parts[1]
    token = parts[2]

    if mode == "cancel":
        KARAOKE_SESSIONS.pop(token, None)
        await event.edit("🚫 **Separation cancelled.**")
        return

    sess = KARAOKE_SESSIONS.pop(token, None)
    if not sess:
        await event.edit("❌ **Session expired.** Please reply `/split` again.")
        return

    source_msg = await event.client.get_messages(sess["chat_id"], ids=sess["msg_id"])
    if not source_msg:
        await event.edit("❌ **Original track not found.**")
        return

    await event.edit(f"⚡ **Processing track ({mode})...**")
    await execute_split_track(event, source_msg, mode, is_callback=True)


async def execute_split_track(event: SafeMessageWrapper, source_msg, mode: str, is_callback: bool = False):
    """Downloads audio, executes separation/transcription, and delivers result."""
    temp_dir = tempfile.mkdtemp(prefix="novacore_karaoke_")
    src_file = os.path.join(temp_dir, "input_track.mp3")
    out_file = os.path.join(temp_dir, f"output_{mode}.mp3")

    status_msg = event if is_callback else await event.reply("⏳ **Downloading track for processing...**")
    loop = asyncio.get_running_loop()

    try:
        saved = await event.client.download_media(source_msg, file=src_file)
        if not saved or not os.path.exists(saved):
            await status_msg.edit("❌ **Error:** Failed to download track.")
            return

        # Handle AI lyrics transcription via Groq Whisper
        if mode in ["lyrics", "transcript", "text"]:
            await status_msg.edit("🎙️ **Transcribing lyrics via Groq Whisper AI...**")
            lyrics_text = await ai_pool.transcribe_audio(saved)
            if not lyrics_text or not lyrics_text.strip():
                await status_msg.edit("❌ **No vocals detected in the audio track.**")
                return

            meta = get_audio_metadata(saved)
            caption = (
                f"📜 **Lyrics & Transcript for:** `{meta['title']}`\n"
                f"👤 *{meta['artist']}* • ⏱️ `{format_duration(meta['duration'])}`\n\n"
                f"{lyrics_text[:3800]}\n\n"
                "⚡ *Powered by Groq Whisper-large-v3*"
            )
            await status_msg.edit(caption)
            return

        # Handle Audio Separation (Instrumental or Vocals)
        label = "Karaoke Instrumental" if mode in ["instrumental", "karaoke"] else "Isolated Vocals"
        await status_msg.edit(f"🎛️ **Extracting {label} (320kbps MP3)...**")
        await loop.run_in_executor(None, process_vocal_split, saved, out_file, mode)

        if not os.path.exists(out_file):
            await status_msg.edit("❌ **Error:** Separation process failed.")
            return

        fsize = os.path.getsize(out_file)
        meta = get_audio_metadata(out_file)
        new_title = f"{meta['title']} [{label}]"

        audio_attr = DocumentAttributeAudio(
            duration=meta["duration"],
            title=new_title[:64],
            performer=meta["artist"][:64],
            voice=False,
        )

        caption = (
            f"🎵 **{new_title}**\n"
            f"👤 *{meta['artist']}* • ⏱️ `{format_duration(meta['duration'])}` • 🎛️ `{label}`\n"
            f"💾 `{format_bytes(fsize)}`"
        )

        await event.client.send_file(
            event.chat_id,
            file=out_file,
            caption=caption,
            attributes=[audio_attr],
            supports_streaming=True,
            reply_to=getattr(event, "topic_id", None),
        )

        if is_callback:
            await event.delete()
        else:
            await status_msg.delete()

    except Exception as e:
        logger.exception("Vocal split failed: %s", e)
        await status_msg.edit(f"❌ **Split Error:** `{e}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
