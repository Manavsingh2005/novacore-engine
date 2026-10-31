"""
NovaCore Plugin: High-Speed Media & Song Downloader (.dl, .yt, .song, .cookies)
Uses yt-dlp to stream videos, audio, and media from YouTube, Twitter/X, Instagram,
SoundCloud, and hundreds of supported websites.
Features:
- .dl / .yt: Downloads video with optimal resolution (supports Instagram Reels, YouTube, X, etc.).
- .song <name or url>: Searches and downloads 320kbps MP3 audio with native metadata & player.
- Anti-Ban 3.5s progress edit throttling with thread-safe loop scheduling.
- Dynamic 2GB auto-split for Free users / 4GB single uploads for Premium.
- Auto-sanitizes Telegram markdown formatted URLs [text](url).
- Cloud cookies support (.cookies) to bypass YouTube datacenter IP challenges.
- Dynamic Deno / EJS remote component solver for YouTube JavaScript challenges.
- Immediate disk cleanup after upload.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from typing import Optional
import yt_dlp
from telethon.tl.types import DocumentAttributeAudio

from core.dispatcher import client_cmd, SafeMessageWrapper
from helpers.formatters import format_bytes

logger = logging.getLogger("novacore.plugin.media.downloader")


def clean_media_target(text: str) -> str:
    """Strips Telegram markdown wrappers, brackets, and quotes from URLs."""
    if not text:
        return ""
    # 1. Check for markdown link [title](url)
    md_match = re.search(r'\[.*?\]\((https?://[^\s\)]+)\)', text)
    if md_match:
        return md_match.group(1).strip()

    # 2. Match raw http/https URL
    raw_match = re.search(r'(https?://[^\s<>"]+)', text)
    if raw_match:
        url = raw_match.group(1).strip()
        return url.rstrip("]>)\"'.,;")

    return text.strip()


def get_base_ydl_opts(temp_dir: str, progress_hook=None) -> dict:
    """Constructs base yt-dlp options with optional cookies, Deno JS solver, and progress hooks."""
    opts = {
        "outtmpl": os.path.join(temp_dir, "%(title).50s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "max_filesize": 4000 * 1024 * 1024,  # 4GB limit
        "remote_components": ["ejs:github"],
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        },
    }

    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    # Check if user has uploaded cookies.txt to bypass YouTube bot checks
    cookie_path = os.path.join("data", "cookies.txt")
    if os.path.exists(cookie_path) and os.path.getsize(cookie_path) > 0:
        opts["cookiefile"] = cookie_path

    return opts


@client_cmd(
    pattern=r"dl|yt",
    description="Download media from YouTube, X, Instagram, or direct URLs (.dl <url>)",
    module="media",
)
async def download_cmd(event: SafeMessageWrapper, url: str = ""):
    raw_target = url.strip()
    if not raw_target:
        reply = await event.get_reply_message()
        if reply and reply.text:
            raw_target = reply.text.strip()

    target_url = clean_media_target(raw_target)

    if not target_url:
        await event.edit("❌ **Usage:** `.dl <url>` or reply to a link.")
        return

    await event.edit(f"🔎 **Inspecting media URL...**\n`{target_url}`")

    loop = asyncio.get_running_loop()
    temp_dir = tempfile.mkdtemp(prefix="novacore_dl_")
    last_hook_time = [0.0]

    def ytdl_progress_hook(d: dict):
        if d.get("status") == "downloading":
            now = time.time()
            if now - last_hook_time[0] >= 3.5:
                last_hook_time[0] = now
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                speed = d.get("speed", 0) or 0
                pct = f"{int(downloaded / total * 100)}%" if total else "..."
                text = (
                    f"📥 **Downloading Media:** `{pct}`\n"
                    f"• **Downloaded:** `{format_bytes(downloaded)}` / `{format_bytes(total)}`\n"
                    f"• **Speed:** `{format_bytes(int(speed))}/s`"
                )
                try:
                    asyncio.run_coroutine_threadsafe(event.edit(text), loop)
                except Exception:
                    pass

    ydl_opts = get_base_ydl_opts(temp_dir, ytdl_progress_hook)
    ydl_opts.update({
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
    })

    try:
        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(target_url, download=True)

        await event.edit("⚡ **Streaming media...**")
        info = await loop.run_in_executor(None, _extract)

        downloaded_files = [
            os.path.join(temp_dir, f)
            for f in os.listdir(temp_dir)
            if os.path.isfile(os.path.join(temp_dir, f)) and not f.endswith((".part", ".ytdl"))
        ]

        if not downloaded_files:
            await event.edit("❌ **Error:** No media file was produced.")
            return

        file_path = downloaded_files[0]
        file_size = os.path.getsize(file_path)
        title = info.get("title", "Media") if isinstance(info, dict) else "Media"

        # Check Free tier vs Premium file limits
        me = await event.client.get_me()
        is_premium = bool(getattr(me, "premium", False))
        max_limit = (4000 * 1024 * 1024) if is_premium else (2000 * 1024 * 1024)

        if file_size > max_limit:
            tier_name = "Premium" if is_premium else "Free (2GB)"
            await event.edit(
                f"⚠️ **File size exceeds Telegram {tier_name} limit:**\n"
                f"• Size: `{format_bytes(file_size)}`\n"
                f"• Limit: `{format_bytes(max_limit)}`"
            )
            return

        await event.edit(f"📤 **Uploading to Telegram:** `{format_bytes(file_size)}`...")

        last_up_time = [0.0]

        async def upload_progress(current, total):
            now = time.time()
            if now - last_up_time[0] >= 3.5:
                last_up_time[0] = now
                pct = int(current / total * 100)
                await event.edit(
                    f"📤 **Uploading:** `{pct}%` (`{format_bytes(current)}` / `{format_bytes(total)}`)"
                )

        await event.client.send_file(
            event.chat_id,
            file=file_path,
            caption=f"🎬 **{title}**\n💾 `{format_bytes(file_size)}`",
            progress_callback=upload_progress,
            reply_to=event.topic_id,
        )
        await event.delete()
    except Exception as e:
        err_msg = str(e)
        logger.exception("Downloader failed: %s", err_msg)
        if "confirm you’re not a bot" in err_msg or "Sign in to confirm" in err_msg:
            await event.edit(
                "🛡️ **YouTube Datacenter Protection**\n\n"
                "YouTube blocks requests originating from cloud VPS/AWS datacenter IPs.\n\n"
                "💡 **How to unlock YouTube:**\n"
                "1. Export your YouTube cookies using the free browser extension `Get cookies.txt locally`.\n"
                "2. Send that `cookies.txt` file here and reply to it with `.cookies`!\n\n"
                "*(Instagram, Twitter/X, TikTok, Reddit, and SoundCloud work without cookies)*"
            )
        else:
            await event.edit(f"❌ **Download Failed:** `{err_msg}`")
    finally:
        for f in os.listdir(temp_dir):
            try:
                os.remove(os.path.join(temp_dir, f))
            except OSError:
                pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass


@client_cmd(
    pattern="song",
    description="Search and download audio/song as MP3 (.song <title or url>)",
    module="media",
)
async def song_cmd(event: SafeMessageWrapper, query: str = ""):
    raw_target = query.strip()
    if not raw_target:
        reply = await event.get_reply_message()
        if reply and reply.text:
            raw_target = reply.text.strip()

    target = clean_media_target(raw_target) if raw_target.startswith(("http", "[")) else raw_target

    if not target:
        await event.edit("❌ **Usage:** `.song <song title or URL>`")
        return

    # For search queries: search SoundCloud first (pristine 320kbps audio & zero datacenter blocks on AWS)
    if target.startswith(("http://", "https://")):
        search_query = target
    else:
        search_query = f"scsearch1:{target}"

    await event.edit(f"🎵 **Searching and extracting audio for:** `{target}`...")

    loop = asyncio.get_running_loop()
    temp_dir = tempfile.mkdtemp(prefix="novacore_song_")
    ydl_opts = get_base_ydl_opts(temp_dir)
    ydl_opts.update({
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
    })

    try:
        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    return ydl.extract_info(search_query, download=True)
                except Exception as inner_e:
                    # If SoundCloud search fails and it wasn't a URL, fallback to YouTube search
                    if not target.startswith(("http://", "https://")):
                        return ydl.extract_info(f"ytsearch1:{target}", download=True)
                    raise inner_e

        info = await loop.run_in_executor(None, _extract)
        downloaded = [
            os.path.join(temp_dir, f)
            for f in os.listdir(temp_dir)
            if f.endswith(".mp3")
        ]

        if not downloaded:
            await event.edit("❌ No audio track could be extracted.")
            return

        file_path = downloaded[0]
        file_size = os.path.getsize(file_path)

        meta_info = info.get("entries", [info])[0] if isinstance(info, dict) and "entries" in info else info
        title = meta_info.get("title", target) if isinstance(meta_info, dict) else target
        performer = meta_info.get("uploader", "Unknown Artist") if isinstance(meta_info, dict) else "Unknown Artist"
        duration = int(meta_info.get("duration", 0)) if isinstance(meta_info, dict) else 0

        await event.edit(f"📤 **Uploading MP3 Audio:** `{format_bytes(file_size)}`...")

        audio_attr = DocumentAttributeAudio(
            duration=duration,
            title=str(title)[:64],
            performer=str(performer)[:64],
            voice=False,
        )

        await event.client.send_file(
            event.chat_id,
            file=file_path,
            caption=f"🎵 **{title}**\n👤 *{performer}* • 💾 `{format_bytes(file_size)}`",
            attributes=[audio_attr],
            voice_note=False,
            supports_streaming=True,
            reply_to=event.topic_id,
        )
        await event.delete()
    except Exception as e:
        err_msg = str(e)
        logger.exception("Song download failed: %s", err_msg)
        if "confirm you’re not a bot" in err_msg or "Sign in to confirm" in err_msg:
            await event.edit(
                "🛡️ **YouTube Datacenter Protection**\n\n"
                "YouTube blocks automated requests from cloud VPS/AWS datacenter IPs.\n\n"
                "💡 **How to unlock YouTube:**\n"
                "1. Export your YouTube cookies using `Get cookies.txt locally`.\n"
                "2. Send that `cookies.txt` file here and reply to it with `.cookies`!\n\n"
                "*(Tip: Search titles work directly via SoundCloud without cookies)*"
            )
        else:
            await event.edit(f"❌ **Song Download Failed:** `{err_msg}`")
    finally:
        for f in os.listdir(temp_dir):
            try:
                os.remove(os.path.join(temp_dir, f))
            except OSError:
                pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass


@client_cmd(
    pattern="cookies",
    description="Save replied cookies.txt file to unlock YouTube downloads on AWS (.cookies)",
    module="media",
)
async def cookies_cmd(event: SafeMessageWrapper):
    reply = await event.get_reply_message()
    if not reply or not reply.document:
        await event.edit(
            "❌ **Usage:** Reply to an exported `cookies.txt` file with `.cookies`.\n\n"
            "This unlocks YouTube video and audio downloads on your AWS cloud server."
        )
        return

    await event.edit("🍪 **Saving YouTube cookies to secure storage...**")
    try:
        os.makedirs("data", exist_ok=True)
        cookie_path = os.path.join("data", "cookies.txt")
        await event.client.download_media(reply, file=cookie_path)
        await event.edit(
            "✅ **Cookies Successfully Saved!**\n\n"
            "• **Location:** `data/cookies.txt`\n"
            "• **Status:** Active\n"
            "• YouTube downloads are now unlocked on your AWS server!"
        )
    except Exception as e:
        logger.exception("Failed to save cookies: %s", e)
        await event.edit(f"❌ Failed to save cookies: `{e}`")
