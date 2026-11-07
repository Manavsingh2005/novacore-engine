"""
NovaCore: Audio Speed & Nightcore / Slowed Studio (/speed, /nightcore, /slowed)
Modifies playback speed, pitch, nightcore, and slowed+reverb effects via FFmpeg.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from telethon import Button
from telethon.tl.types import DocumentAttributeAudio

from core.dispatcher import bot_cmd, bot_callback, SafeMessageWrapper
from helpers.formatters import format_bytes, format_duration

logger = logging.getLogger("novacore.plugin.utility.audiospeed")

SPEED_SESSIONS = {}


def get_audio_info(file_path: str) -> dict:
    """Extracts duration, title, and artist using ffprobe."""
    meta = {"duration": 0, "title": "Audio", "artist": "NovaCore"}
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", file_path
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


def apply_audio_effect(src_path: str, out_path: str, mode: str) -> None:
    """Applies speed, nightcore, or slowed+reverb filters using ffmpeg."""
    filter_expr = "atempo=1.25"

    mode = mode.lower().strip()
    if mode in ["nightcore", "nc"]:
        # Nightcore: increase sample rate by 1.25x (higher pitch + faster tempo)
        filter_expr = "asetrate=44100*1.25,aresample=44100"
    elif mode in ["slowed", "slow"]:
        # Slowed + gentle reverb
        filter_expr = "asetrate=44100*0.85,aresample=44100,aecho=0.8:0.85:35:0.35"
    elif mode in ["0.5", "0.5x"]:
        filter_expr = "atempo=0.5"
    elif mode in ["0.75", "0.75x"]:
        filter_expr = "atempo=0.75"
    elif mode in ["1.25", "1.25x"]:
        filter_expr = "atempo=1.25"
    elif mode in ["1.5", "1.5x"]:
        filter_expr = "atempo=1.5"
    elif mode in ["1.75", "1.75x"]:
        filter_expr = "atempo=1.75"
    elif mode in ["2.0", "2x", "2.0x"]:
        filter_expr = "atempo=2.0"
    else:
        # Custom float tempo
        try:
            val = float(mode.replace("x", ""))
            val = max(0.5, min(3.0, val))
            if val > 2.0:
                filter_expr = f"atempo=2.0,atempo={val/2.0:.2f}"
            else:
                filter_expr = f"atempo={val:.2f}"
        except Exception:
            filter_expr = "atempo=1.25"

    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-filter:a", filter_expr,
        "-c:a", "libmp3lame", "-b:a", "320k",
        out_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@bot_cmd(
    pattern=r"(?:speed|audiospeed|nightcore|slowed)(?:\s+(.*))?",
    description="Change audio speed, pitch, Nightcore & Slowed effects (/speed [0.5x-2x])",
    module="utility",
)
async def audiospeed_cmd(event: SafeMessageWrapper, query: str = ""):
    reply = await event.get_reply_message()
    if not reply or not (getattr(reply, "audio", None) or getattr(reply, "voice", None) or getattr(reply, "document", None)):
        await event.reply(
            "⚡ **Audio Speed & Nightcore Studio:**\n\n"
            "• Reply `/speed 1.25x` or `/speed 1.5x` to speed up podcasts & audio\n"
            "• Reply `/speed 0.75x` or `/speed 0.85x` to slow down audio\n"
            "• Reply `/nightcore` to apply Nightcore pitch + tempo boost\n"
            "• Reply `/slowed` to apply dreamy Slowed + Reverb aesthetic\n\n"
            "💡 *Tip: Reply `/speed` without arguments to see interactive preset buttons!*"
        )
        return

    raw_arg = (query or "").strip()
    cmd_name = (event.raw_text or "").split()[0].lstrip("/.").lower()

    if cmd_name == "nightcore":
        raw_arg = "nightcore"
    elif cmd_name == "slowed":
        raw_arg = "slowed"

    if not raw_arg:
        # Show interactive buttons
        token = f"{event.chat_id}_{reply.id}_{int(time.time())}"
        SPEED_SESSIONS[token] = {
            "chat_id": event.chat_id,
            "msg_id": reply.id,
            "time": time.time(),
        }
        buttons = [
            [
                Button.inline("⚡ 1.25x Quick", data=f"spd:1.25x:{token}"),
                Button.inline("🚀 1.5x Fast", data=f"spd:1.5x:{token}"),
                Button.inline("🔥 2.0x Turbo", data=f"spd:2.0x:{token}"),
            ],
            [
                Button.inline("🐢 0.75x Slow", data=f"spd:0.75x:{token}"),
                Button.inline("✨ Nightcore", data=f"spd:nightcore:{token}"),
                Button.inline("🌌 Slowed & Reverb", data=f"spd:slowed:{token}"),
            ],
            [Button.inline("❌ Cancel", data=f"spd:cancel:{token}")],
        ]
        await event.reply("🎛️ **Select Audio Effect / Playback Speed:**", buttons=buttons)
        return

    await execute_speed_effect(event, reply, raw_arg)


@bot_callback(r"^spd:([^:]+):(.+)$")
async def speed_callback_handler(event: SafeMessageWrapper):
    data = event.data.decode("utf-8")
    parts = data.split(":", 2)
    mode = parts[1]
    token = parts[2]

    if mode == "cancel":
        SPEED_SESSIONS.pop(token, None)
        await event.edit("🚫 **Operation cancelled.**")
        return

    sess = SPEED_SESSIONS.pop(token, None)
    if not sess:
        await event.edit("❌ **Session expired.** Please reply `/speed` again.")
        return

    source_msg = await event.client.get_messages(sess["chat_id"], ids=sess["msg_id"])
    if not source_msg:
        await event.edit("❌ **Original message not found.**")
        return

    await event.edit(f"🎛️ **Applying `{mode}` audio effect...**")
    await execute_speed_effect(event, source_msg, mode, is_callback=True)


async def execute_speed_effect(event: SafeMessageWrapper, source_msg, mode: str, is_callback: bool = False):
    """Downloads audio, applies ffmpeg filter, and uploads to Telegram."""
    temp_dir = tempfile.mkdtemp(prefix="novacore_speed_")
    src_file = os.path.join(temp_dir, "input_audio")
    out_file = os.path.join(temp_dir, f"audio_{mode}.mp3")

    status_msg = event if is_callback else await event.reply("⏳ **Downloading audio for processing...**")
    loop = asyncio.get_running_loop()

    try:
        saved = await event.client.download_media(source_msg, file=src_file)
        if not saved or not os.path.exists(saved):
            await status_msg.edit("❌ **Error:** Failed to download audio.")
            return

        await status_msg.edit(f"🎛️ **Applying `{mode}` filter (320kbps MP3)...**")
        await loop.run_in_executor(None, apply_audio_effect, saved, out_file, mode)

        if not os.path.exists(out_file):
            await status_msg.edit("❌ **Error:** Audio processing failed.")
            return

        out_size = os.path.getsize(out_file)
        meta = get_audio_info(out_file)

        await status_msg.edit(f"📤 **Uploading processed track ({format_bytes(out_size)})...**")

        new_title = f"{meta['title']} ({mode})"
        audio_attr = DocumentAttributeAudio(
            duration=meta["duration"],
            title=new_title[:64],
            performer=meta["artist"][:64],
            voice=False,
        )

        caption = (
            f"🎵 **{new_title}**\n"
            f"👤 *{meta['artist']}* • ⏱️ `{format_duration(meta['duration'])}` • 🎛️ `{mode}`\n"
            f"💾 `{format_bytes(out_size)}`"
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
        logger.exception("Audio speed processing failed: %s", e)
        await status_msg.edit(f"❌ **Audio Processing Failed:** `{e}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
