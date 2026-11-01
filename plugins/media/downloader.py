"""
NovaCore Plugin: High-Speed Media & Song Downloader (.dl, .yt, .song, .cookies)
Features:
- .song <name>: Interactive search with 5 top results, artist, and duration.
  - Pick by replying `1`-`5` or sending `.song <number>`.
  - Use `.song -1 <name>` or `.song -d <name>` for instant auto-download.
  - Supports direct audio URLs.
- .dl / .yt: Downloads video from YouTube, X, Instagram Reels, TikTok, Reddit, etc.
  - Supports resolution tags: `.dl 1080 <url>`, `.dl 720 <url>`, `.dl 480 <url>`, `.dl 360 <url>`, `.dl audio <url>`.
  - Supports interactive resolution selector: `.dl select <url>`.
- Anti-Ban 3.5s progress edit throttling with thread-safe loop scheduling.
- Dynamic 2GB Free tier / 4GB Premium single uploads.
- Auto-sanitizes Telegram markdown formatted URLs [text](url).
- Cloud cookies support (.cookies) to bypass YouTube datacenter bot challenges.
- Deno / EJS remote component solver for YouTube JavaScript challenges.
- Guaranteed disk cleanup in finally blocks to keep EC2 storage 100% clean.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
import re
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional
import yt_dlp
from telethon import events
from telethon.tl.types import DocumentAttributeAudio

from core.dispatcher import client_cmd, client_raw_handler, SafeMessageWrapper
from helpers.formatters import format_bytes

logger = logging.getLogger("novacore.plugin.media.downloader")

# Active search sessions keyed by chat_id
SONG_SEARCH_CACHE: Dict[int, Dict[str, Any]] = {}
DL_SELECT_CACHE: Dict[int, Dict[str, Any]] = {}


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


def format_duration(seconds: Optional[int | float]) -> str:
    """Formats seconds into mm:ss or hh:mm:ss."""
    if not seconds or seconds <= 0:
        return "N/A"
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def get_base_ydl_opts(temp_dir: str, progress_hook=None) -> dict:
    """Constructs base yt-dlp options with isolated cookie snapshot, mobile clients, and progress hooks."""
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

    # Isolate cookies: yt-dlp writes back session updates to the cookiefile on exit.
    # On datacenter IPs, this corrupts or strips existing auth cookies after a single request!
    # To keep data/cookies.txt permanently intact, we copy it into an isolated temporary file per request.
    master_cookie = os.path.join("data", "cookies.txt")
    backup_cookie = os.path.join("data", "cookies_master.txt")

    # Auto-restore from backup if master cookie was somehow deleted
    if os.path.exists(backup_cookie) and os.path.getsize(backup_cookie) > 0:
        if not os.path.exists(master_cookie) or os.path.getsize(master_cookie) == 0:
            try:
                shutil.copyfile(backup_cookie, master_cookie)
            except Exception:
                pass

    if os.path.exists(master_cookie) and os.path.getsize(master_cookie) > 0:
        try:
            vault_dir = os.path.join(temp_dir, ".cookie_vault")
            os.makedirs(vault_dir, exist_ok=True)
            isolated_cookie = os.path.join(vault_dir, "isolated_cookies.txt")
            shutil.copyfile(master_cookie, isolated_cookie)
            opts["cookiefile"] = isolated_cookie
        except Exception as ce:
            logger.warning("Failed to isolate cookies file: %s", ce)
            opts["cookiefile"] = master_cookie

    return opts


# ==========================================
# AUDIO / SONG DOWNLOAD ENGINE
# ==========================================

async def _download_and_send_song(
    event: SafeMessageWrapper,
    target_url: str,
    meta_hint: Optional[Dict[str, Any]] = None,
):
    """Executes high-speed 320kbps MP3 extraction and uploads to Telegram."""
    loop = asyncio.get_running_loop()
    temp_dir = tempfile.mkdtemp(prefix="novacore_song_")
    last_hook_time = [0.0]

    def song_progress_hook(d: dict):
        if d.get("status") == "downloading":
            now = time.time()
            if now - last_hook_time[0] >= 3.5:
                last_hook_time[0] = now
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                speed = d.get("speed", 0) or 0
                pct = f"{int(downloaded / total * 100)}%" if total else "..."
                text = (
                    f"📥 **Downloading Audio:** `{pct}`\n"
                    f"• **Downloaded:** `{format_bytes(downloaded)}` / `{format_bytes(total)}`\n"
                    f"• **Speed:** `{format_bytes(int(speed))}/s`"
                )
                try:
                    asyncio.run_coroutine_threadsafe(event.edit(text), loop)
                except Exception:
                    pass

    ydl_opts = get_base_ydl_opts(temp_dir, song_progress_hook)
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
        await event.edit("⚡ **Streaming audio track...**")

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(target_url, download=True)

        info = await loop.run_in_executor(None, _extract)
        downloaded = [
            os.path.join(temp_dir, f)
            for f in os.listdir(temp_dir)
            if f.endswith(".mp3")
        ]

        if not downloaded:
            await event.edit("❌ **Error:** No audio track could be extracted.")
            return

        file_path = downloaded[0]
        file_size = os.path.getsize(file_path)

        meta_info = info.get("entries", [info])[0] if isinstance(info, dict) and "entries" in info else info
        title = (
            meta_info.get("title")
            or (meta_hint and meta_hint.get("title"))
            or "Track"
        )
        performer = (
            meta_info.get("uploader")
            or meta_info.get("channel")
            or (meta_hint and meta_hint.get("uploader"))
            or "NovaCore Audio"
        )
        duration = int(
            meta_info.get("duration")
            or (meta_hint and meta_hint.get("duration"))
            or 0
        )

        await event.edit(f"📤 **Uploading MP3 Audio:** `{format_bytes(file_size)}`...")

        audio_attr = DocumentAttributeAudio(
            duration=duration,
            title=str(title)[:64],
            performer=str(performer)[:64],
            voice=False,
        )

        last_up_time = [0.0]

        async def upload_progress(current, total):
            now = time.time()
            if now - last_up_time[0] >= 3.5:
                last_up_time[0] = now
                pct = int(current / total * 100)
                await event.edit(
                    f"📤 **Uploading MP3:** `{pct}%` (`{format_bytes(current)}` / `{format_bytes(total)}`)"
                )

        await event.client.send_file(
            event.chat_id,
            file=file_path,
            caption=f"🎵 **{title}**\n👤 *{performer}* • ⏱️ `{format_duration(duration)}` • 💾 `{format_bytes(file_size)}`",
            attributes=[audio_attr],
            voice_note=False,
            supports_streaming=True,
            progress_callback=upload_progress,
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
                "1. Switch to your **YouTube** tab in Chrome (ensure you are logged in).\n"
                "2. Click the `Get cookies.txt locally` extension icon on that tab.\n"
                "3. Send the exported `.txt` file here and reply with `.cookies`!\n\n"
                "*(Instagram, Twitter/X, TikTok, Reddit, and SoundCloud work without cookies)*"
            )
        else:
            await event.edit(f"❌ **Song Download Failed:** `{err_msg}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@client_cmd(
    pattern="song",
    description="Interactive song search & 320kbps MP3 downloader (.song <title or url>)",
    module="media",
)
async def song_cmd(event: SafeMessageWrapper, query: str = ""):
    raw_target = query.strip()
    if not raw_target:
        reply = await event.get_reply_message()
        if reply and reply.text:
            raw_target = reply.text.strip()

    if not raw_target:
        await event.edit(
            "❌ **Usage:** `.song <title or URL>`\n\n"
            "• `.song treat you better` — Search top 5 tracks with selection menu\n"
            "• `.song 1` to `.song 5` — Select track from previous search\n"
            "• `.song -1 treat you better` — Auto-download top match immediately\n"
            "• `.song <url>` — Direct MP3 download"
        )
        return

    # Check for direct track selection number: e.g. .song 1, .song 2
    if re.fullmatch(r"[1-5]", raw_target):
        choice = int(raw_target)
        cached = SONG_SEARCH_CACHE.get(event.chat_id)
        if not cached or (time.time() - cached.get("time", 0)) > 900:  # 15 min expiry
            await event.edit("❌ **No active song search found.** Search first with `.song <title>`.")
            return

        entries = cached.get("entries", [])
        if choice > len(entries):
            await event.edit(f"❌ **Invalid selection.** Choose between `1` and `{len(entries)}`.")
            return

        selected = entries[choice - 1]
        SONG_SEARCH_CACHE.pop(event.chat_id, None)
        await event.edit(f"⚡ **Downloading Track #{choice}:** `{selected['title']}`...")
        await _download_and_send_song(event, selected["url"], meta_hint=selected)
        return

    # Check for instant auto-download flag: -1, -d, --direct
    auto_download_top = False
    clean_query = raw_target
    if re.search(r"^(-[1-5]|-d|--direct)\b", clean_query):
        auto_download_top = True
        clean_query = re.sub(r"^(-[1-5]|-d|--direct)\s*", "", clean_query).strip()
    elif re.search(r"\s+(-[1-5]|-d|--direct)$", clean_query):
        auto_download_top = True
        clean_query = re.sub(r"\s+(-[1-5]|-d|--direct)$", "", clean_query).strip()

    target = clean_media_target(clean_query)

    # If it's a direct URL, download immediately
    if target.startswith(("http://", "https://")):
        await event.edit(f"🎵 **Extracting audio from URL:** `{target}`...")
        await _download_and_send_song(event, target)
        return

    # Search for top 5 candidates
    await event.edit(f"🔎 **Searching music for:** `{target}`...")
    loop = asyncio.get_running_loop()

    def _search_tracks() -> List[Dict[str, Any]]:
        search_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,  # Flat search retrieves titles & URLs without triggering bot checks
            "skip_download": True,
            "remote_components": ["ejs:github"],
        }
        tracks: List[Dict[str, Any]] = []
        # 1. Search YouTube first
        try:
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                res = ydl.extract_info(f"ytsearch5:{target}", download=False)
                for entry in (res.get("entries") or []):
                    if not entry:
                        continue
                    v_id = entry.get("id")
                    url = entry.get("url") or (f"https://www.youtube.com/watch?v={v_id}" if v_id else None)
                    if url:
                        tracks.append({
                            "title": entry.get("title") or "Unknown Title",
                            "uploader": entry.get("uploader") or entry.get("channel") or "Unknown Artist",
                            "duration": entry.get("duration") or 0,
                            "url": url,
                            "id": v_id,
                        })
        except Exception as e:
            logger.warning("YouTube search failed: %s, trying SoundCloud...", e)

        # 2. Fallback to SoundCloud if YouTube gave no results
        if not tracks:
            try:
                with yt_dlp.YoutubeDL(search_opts) as ydl:
                    res = ydl.extract_info(f"scsearch5:{target}", download=False)
                    for entry in (res.get("entries") or []):
                        if not entry:
                            continue
                        tracks.append({
                            "title": entry.get("title") or "Unknown Title",
                            "uploader": entry.get("uploader") or "SoundCloud Artist",
                            "duration": entry.get("duration") or 0,
                            "url": entry.get("url") or entry.get("webpage_url"),
                            "id": entry.get("id"),
                        })
            except Exception as sc_e:
                logger.error("SoundCloud search also failed: %s", sc_e)

        return tracks[:5]

    results = await loop.run_in_executor(None, _search_tracks)

    if not results:
        await event.edit(f"❌ **No results found for:** `{target}`.\nTry including the artist name.")
        return

    # If auto-download flag was passed, download #1 immediately
    if auto_download_top:
        top_track = results[0]
        await event.edit(f"⚡ **Auto-downloading top match:** `{top_track['title']}`...")
        await _download_and_send_song(event, top_track["url"], meta_hint=top_track)
        return

    # Save to interactive session cache
    SONG_SEARCH_CACHE[event.chat_id] = {
        "prompt_msg_id": event.message.id,
        "topic_id": event.topic_id,
        "time": time.time(),
        "query": target,
        "entries": results,
    }

    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    menu_lines = [
        "🎧 **NovaCore Music Search**",
        f"🔍 **Query:** `{target}`\n",
    ]

    for idx, track in enumerate(results):
        emoji = number_emojis[idx] if idx < len(number_emojis) else f"`[{idx+1}]`"
        dur = format_duration(track["duration"])
        menu_lines.append(
            f"{emoji} **{track['title']}**\n"
            f"   👤 `{track['uploader']}` • ⏱️ `{dur}`\n"
        )

    menu_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    menu_lines.append("👉 **To download:** Reply with `1` - `5` or send `.song <number>`!")
    menu_lines.append("💡 *Tip: Use `.song -1 {query}` to auto-download the top match directly.*")

    await event.edit("\n".join(menu_lines))


# ==========================================
# VIDEO / MEDIA DOWNLOAD ENGINE
# ==========================================

def is_youtube_url(url: str) -> bool:
    """Determines if a URL is hosted on YouTube."""
    if not url:
        return False
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u or "youtube-nocookie.com" in u


@client_cmd(
    pattern=r"dl|yt",
    description="Download media with quality menu (.dl <url> or .dl [1080|720|480|360|audio|best] <url>)",
    module="media",
)
async def download_cmd(event: SafeMessageWrapper, url: str = ""):
    raw_args = url.strip()
    if not raw_args:
        reply = await event.get_reply_message()
        if reply and reply.text:
            raw_args = reply.text.strip()

    if not raw_args:
        await event.edit(
            "❌ **Usage:** `.dl <url>` or reply to a link.\n\n"
            "• `.dl <url>` — Shows interactive quality selection menu\n"
            "• `.dl 720 <url>` — Direct 720p HD download\n"
            "• `.dl 1080 <url>` — Direct 1080p Full HD download\n"
            "• `.dl best <url>` — Direct highest quality download\n"
            "• `.dl audio <url>` — Extract MP3 directly"
        )
        return

    # Check if user is replying with .dl 1 - .dl 5 to a quality menu
    if re.fullmatch(r"[1-5]", raw_args):
        choice = int(raw_args)
        cached = DL_SELECT_CACHE.get(event.chat_id)
        if not cached or (time.time() - cached.get("time", 0)) > 900:
            await event.edit("❌ **No active quality selector found.** Provide a URL first: `.dl <url>`.")
            return

        resolutions = ["1080", "720", "480", "360", "audio"]
        chosen_res = resolutions[choice - 1]
        target_url = cached["url"]
        DL_SELECT_CACHE.pop(event.chat_id, None)
        await event.edit(f"⚡ **Downloading in {chosen_res} resolution...**")
        return await _execute_dl(event, target_url, quality=chosen_res)

    # Check if explicit quality is passed (e.g. .dl 720 <url>, .dl 1080 <url>, .dl best <url>, .dl -y <url>)
    explicit_quality = None
    target_str = raw_args
    parts = raw_args.split(maxsplit=1)

    if parts[0].lower() in ["1080", "720", "480", "360", "audio", "mp3", "best", "-y", "auto"]:
        explicit_quality = parts[0].lower().lstrip("-")
        if explicit_quality in ["y", "auto"]:
            explicit_quality = "best"
        target_str = parts[1] if len(parts) > 1 else ""

    target_url = clean_media_target(target_str)
    if not target_url:
        reply = await event.get_reply_message()
        if reply and reply.text:
            target_url = clean_media_target(reply.text)

    if not target_url:
        await event.edit("❌ **Error:** No valid media URL found.")
        return

    # If explicit quality was specified, download immediately
    if explicit_quality:
        return await _execute_dl(event, target_url, quality=explicit_quality)

    # NON-YOUTUBE PLATFORMS (Instagram, TikTok, Twitter/X, Reddit, etc.):
    # Automatically download at highest available quality without showing quality menu!
    if not is_youtube_url(target_url):
        return await _execute_dl(event, target_url, quality="best")

    # YouTube: show interactive quality selection menu!
    await event.edit(f"🔎 **Inspecting YouTube media options...**\n`{target_url}`")
    loop = asyncio.get_running_loop()

    def _probe_info():
        probe_dir = tempfile.mkdtemp(prefix="novacore_probe_")
        try:
            opts = get_base_ydl_opts(probe_dir)
            opts["skip_download"] = True
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(target_url, download=False)
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)

    try:
        info = await loop.run_in_executor(None, _probe_info)
        title = info.get("title", "Media Video") if isinstance(info, dict) else "Media Video"
        uploader = (info.get("uploader") or info.get("channel") or "Online Video") if isinstance(info, dict) else "Online"
        dur_secs = info.get("duration", 0) if isinstance(info, dict) else 0
        dur_str = format_duration(dur_secs) if dur_secs else "N/A"
    except Exception:
        title = "Media Video"
        uploader = "Online Video"
        dur_str = "N/A"

    DL_SELECT_CACHE[event.chat_id] = {
        "prompt_msg_id": event.message.id,
        "topic_id": event.topic_id,
        "time": time.time(),
        "url": target_url,
        "title": title,
    }

    menu_text = (
        f"🎬 **Choose Video Quality:**\n"
        f"• **Title:** `{title[:50]}`\n"
        f"• **Channel:** `{uploader[:30]}` • ⏱️ `{dur_str}`\n\n"
        f"1️⃣ **1080p** — Full HD\n"
        f"2️⃣ **720p** — HD *(Recommended)*\n"
        f"3️⃣ **480p** — Standard\n"
        f"4️⃣ **360p** — Data Saver\n"
        f"5️⃣ **Audio Only** — MP3 320kbps\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 **Reply with `1` - `5` or send `.dl <number>`!**\n"
        f"💡 *Direct download without menu: `.dl 720 <url>` or `.dl best <url>`*"
    )
    await event.edit(menu_text)


async def _execute_dl(event: SafeMessageWrapper, target_url: str, quality: str = "best"):
    """Downloads video according to specified resolution."""
    # Route audio quality to MP3 pipeline
    if quality in ["audio", "mp3"]:
        await event.edit(f"🎵 **Extracting MP3 Audio:** `{target_url}`...")
        return await _download_and_send_song(event, target_url)

    await event.edit(f"🔎 **Inspecting media URL ({quality})...**\n`{target_url}`")

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
                    f"📥 **Downloading Media ({quality}):** `{pct}`\n"
                    f"• **Downloaded:** `{format_bytes(downloaded)}` / `{format_bytes(total)}`\n"
                    f"• **Speed:** `{format_bytes(int(speed))}/s`"
                )
                try:
                    asyncio.run_coroutine_threadsafe(event.edit(text), loop)
                except Exception:
                    pass

    ydl_opts = get_base_ydl_opts(temp_dir, ytdl_progress_hook)

    # Resolution format mapping with resilient vertical (Instagram/Shorts/TikTok),
    # landscape, and single/muxed stream fallbacks
    if quality == "1080":
        fmt = (
            "bestvideo[height<=?1080]+bestaudio/bestvideo[width<=?1080]+bestaudio/"
            "best[height<=?1080]/best[width<=?1080]/bestvideo+bestaudio/best"
        )
    elif quality == "720":
        fmt = (
            "bestvideo[height<=?720]+bestaudio/bestvideo[width<=?720]+bestaudio/"
            "best[height<=?720]/best[width<=?720]/bestvideo+bestaudio/best"
        )
    elif quality == "480":
        fmt = (
            "bestvideo[height<=?480]+bestaudio/bestvideo[width<=?480]+bestaudio/"
            "best[height<=?480]/best[width<=?480]/bestvideo+bestaudio/best"
        )
    elif quality == "360":
        fmt = (
            "bestvideo[height<=?360]+bestaudio/bestvideo[width<=?360]+bestaudio/"
            "best[height<=?360]/best[width<=?360]/bestvideo+bestaudio/best"
        )
    else:
        fmt = "bestvideo*+bestaudio/best"

    ydl_opts.update({
        "format": fmt,
        "merge_output_format": "mp4",
    })

    try:
        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(target_url, download=True)

        await event.edit(f"⚡ **Streaming media ({quality})...**")
        info = await loop.run_in_executor(None, _extract)

        MEDIA_EXTS = (
            ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts",
            ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"
        )
        downloaded_files = [
            os.path.join(temp_dir, f)
            for f in os.listdir(temp_dir)
            if os.path.isfile(os.path.join(temp_dir, f))
            and f.lower().endswith(MEDIA_EXTS)
            and not f.endswith((".part", ".ytdl"))
        ]
        downloaded_files.sort(key=lambda x: os.path.getsize(x), reverse=True)

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
            caption=f"🎬 **{title}**\n💾 `{format_bytes(file_size)}` • 🎯 `{quality}`",
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
                "1. Export your YouTube cookies from your **YouTube tab** using `Get cookies.txt locally`.\n"
                "2. Send that `cookies.txt` file here and reply to it with `.cookies`!\n\n"
                "*(Instagram, Twitter/X, TikTok, Reddit, and SoundCloud work without cookies)*"
            )
        elif "No video formats found" in err_msg or "verify your age" in err_msg:
            await event.edit(
                "🔞 **Content Restricted / No Streams Available**\n\n"
                "This video is age-gated, requires an account login on the hosting site, or has been removed.\n\n"
                "*(Public videos on this site are supported)*"
            )
        elif "HTTP Error 404" in err_msg or "video has been removed" in err_msg:
            await event.edit("❌ **Video Not Found:** This link appears to be deleted or private.")
        else:
            await event.edit(f"❌ **Download Failed:** `{err_msg[:250]}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ==========================================
# INTERACTIVE QUICK-SELECTION HANDLER
# ==========================================

@client_raw_handler(events.NewMessage(outgoing=True, pattern=r"^[1-5]$"))
async def quick_choice_listener(event: SafeMessageWrapper):
    """
    Listens for outgoing single digit replies (1-5) and automatically resolves
    any pending .song search or .dl quality selection menu!
    """
    raw_text = (event.message.text or "").strip()
    if not re.fullmatch(r"[1-5]", raw_text):
        return

    choice = int(raw_text)
    chat_id = event.chat_id
    now = time.time()

    song_cached = SONG_SEARCH_CACHE.get(chat_id)
    dl_cached = DL_SELECT_CACHE.get(chat_id)

    song_time = song_cached.get("time", 0) if song_cached else 0
    dl_time = dl_cached.get("time", 0) if dl_cached else 0

    # Whichever menu was active most recently receives the choice
    if dl_time >= song_time and dl_cached and (now - dl_time) <= 300:
        resolutions = ["1080", "720", "480", "360", "audio"]
        chosen_res = resolutions[choice - 1]
        target_url = dl_cached["url"]
        DL_SELECT_CACHE.pop(chat_id, None)
        await event.edit(f"⚡ **Downloading in {chosen_res} resolution...**")
        await _execute_dl(event, target_url, quality=chosen_res)
        return

    if song_cached and (now - song_time) <= 300:
        entries = song_cached.get("entries", [])
        if 1 <= choice <= len(entries):
            selected = entries[choice - 1]
            SONG_SEARCH_CACHE.pop(chat_id, None)
            await event.edit(f"⚡ **Downloading Track #{choice}:** `{selected['title']}`...")
            await _download_and_send_song(event, selected["url"], meta_hint=selected)
            return


# ==========================================
# CLOUD COOKIES SYSTEM (PROTECTED MASTER VAULT)
# ==========================================

@client_cmd(
    pattern=r"cookies(?:\s+(status|backup|restore|info))?",
    description="Manage cloud cookies vault for YouTube, age-gated videos & sites (.cookies)",
    module="media",
)
async def cookies_cmd(event: SafeMessageWrapper, query: str = ""):
    subcmd = (query or "").strip().lower()
    reply = await event.get_reply_message()

    os.makedirs("data", exist_ok=True)
    cookie_path = os.path.join("data", "cookies.txt")
    backup_path = os.path.join("data", "cookies_master.txt")

    # If user wants to restore from master backup
    if subcmd == "restore":
        if not os.path.exists(backup_path) or os.path.getsize(backup_path) == 0:
            await event.edit("❌ **No master backup found.** Upload a fresh `cookies.txt` first.")
            return
        shutil.copyfile(backup_path, cookie_path)
        await event.edit("✅ **Cookies restored successfully from master backup vault!**")
        return

    # If user wants to backup
    if subcmd == "backup":
        if not os.path.exists(cookie_path) or os.path.getsize(cookie_path) == 0:
            await event.edit("❌ **No active cookies found to back up.**")
            return
        shutil.copyfile(cookie_path, backup_path)
        await event.edit("✅ **Pristine master cookie backup snapshot created!**")
        return

    # If NOT replying to a file, show the live status & instructions
    if not reply or not reply.document:
        if os.path.exists(cookie_path) and os.path.getsize(cookie_path) > 0:
            domains: Dict[str, int] = {}
            total_cookies = 0
            try:
                with open(cookie_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if line.startswith("#") or not line.strip():
                            continue
                        parts = line.strip().split("\t")
                        if len(parts) >= 6:
                            d = parts[0].lstrip(".").lower()
                            domains[d] = domains.get(d, 0) + 1
                            total_cookies += 1
            except Exception:
                pass

            top_domains = ", ".join([f"`{k}` ({v})" for k, v in list(domains.items())[:6]])
            mtime = datetime.fromtimestamp(os.path.getmtime(cookie_path)).strftime("%d %b %H:%M")
            has_yt = "✅ Present" if any("youtube" in d for d in domains) else "⚠️ Missing YouTube"
            has_backup = "✅ Present" if os.path.exists(backup_path) else "❌ None"

            await event.edit(
                f"🍪 **NovaCore Cloud Cookies Vault**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• **Status:** 🟢 Active (`{total_cookies}` cookies stored)\n"
                f"• **YouTube Auth:** {has_yt}\n"
                f"• **Master Backup:** {has_backup}\n"
                f"• **Last Modified:** `{mtime}`\n"
                f"• **Active Domains:** {top_domains or 'None'}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🛡️ **Permanent Protection Enabled:**\n"
                f"> NovaCore automatically isolates cookies in throwaway sandboxes per download. yt-dlp will **never** wipe or overwrite your master cookie vault!\n\n"
                f"📥 **To Add/Update Cookies (1-Time Setup):**\n"
                f"1. Open Chrome in **Incognito mode**.\n"
                f"2. Log into YouTube (or a spare Google account).\n"
                f"3. Go to `https://www.youtube.com/robots.txt`.\n"
                f"4. Click `Get cookies.txt locally` extension -> Export.\n"
                f"5. **Close the Incognito window immediately** (prevents token rotation).\n"
                f"6. Send the `.txt` file here and reply with `.cookies`!"
            )
            return
        else:
            await event.edit(
                "🍪 **No Cookies Installed in Cloud Vault**\n\n"
                "To permanently unlock YouTube & age-gated videos:\n"
                "1. Open Chrome in **Incognito mode**.\n"
                "2. Log into YouTube, go to `https://www.youtube.com/robots.txt`.\n"
                "3. Export `cookies.txt` with `Get cookies.txt locally`.\n"
                "4. Close Incognito immediately.\n"
                "5. Send the `.txt` file here and reply with `.cookies`!\n\n"
                "💡 *Subcommands:* `.cookies status`, `.cookies backup`, `.cookies restore`"
            )
            return

    await event.edit("🍪 **Validating and merging cookies into cloud vault...**")
    try:
        temp_cookie_path = os.path.join("data", "cookies_temp.txt")
        await event.client.download_media(reply, file=temp_cookie_path)

        # Parse new domains from uploaded file
        new_domains: Dict[str, List[str]] = {}
        with open(temp_cookie_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_str = line.strip()
                if not line_str or line_str.startswith("#"):
                    continue
                parts = line_str.split("\t")
                if len(parts) >= 6:
                    domain = parts[0].lstrip(".").lower()
                    new_domains.setdefault(domain, []).append(line)

        if not new_domains:
            if os.path.exists(temp_cookie_path):
                os.remove(temp_cookie_path)
            await event.edit("❌ **Error:** No valid Netscape cookies found in the uploaded file.")
            return

        # Keep existing domains that are NOT in new_domains (preserves other sites)
        all_active_domains = set(new_domains.keys())
        kept_lines: List[str] = []
        if os.path.exists(cookie_path):
            with open(cookie_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.strip().split("\t")
                    if len(parts) >= 6:
                        domain = parts[0].lstrip(".").lower()
                        if domain in new_domains:
                            continue  # Replace with updated cookie
                        all_active_domains.add(domain)
                        kept_lines.append(line)

        # Write merged cookies to master vault
        with open(cookie_path, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n# Managed by NovaCore Engine Vault (Protected Master)\n\n")
            for line in kept_lines:
                f.write(line.rstrip("\r\n") + "\n")
            for domain, lines in new_domains.items():
                for line in lines:
                    f.write(line.rstrip("\r\n") + "\n")

        # Create persistent master backup
        shutil.copyfile(cookie_path, backup_path)

        if os.path.exists(temp_cookie_path):
            os.remove(temp_cookie_path)

        updated_sample = ", ".join(list(new_domains.keys())[:3])
        total_sample = ", ".join(list(all_active_domains)[:5])

        await event.edit(
            "✅ **Cookies Successfully Saved & Permanently Vaulted!**\n\n"
            f"• **Updated Site(s):** `{updated_sample}`\n"
            f"• **Total Active Sites:** `{total_sample}`\n"
            f"• **Master Backup:** Created (`data/cookies_master.txt`)\n"
            f"• **Sandbox Isolation:** Active (yt-dlp will never touch your master vault)\n\n"
            "🎉 YouTube, adult sites, and age-gated videos are now permanently unlocked!"
        )
    except Exception as e:
        logger.exception("Failed to save cookies: %s", e)
        await event.edit(f"❌ Failed to save cookies: `{e}`")
