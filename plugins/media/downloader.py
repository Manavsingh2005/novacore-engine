"""
NovaCore Plugin: High-Speed Media & Song Downloader for Telegram Bot (/dl, /song, /cookies)
Features:
- /dl <url>:
  - YouTube: Interactive Inline Keyboard (1080p, 720p, 480p, 360p, MP3 Audio).
  - Instagram Reels, TikTok, Twitter/X, Reddit, Facebook: Instant direct download at highest quality.
  - Direct resolution flags: /dl 1080 <url>, /dl 720 <url>, /dl best <url>, /dl audio <url>.
- /song <title>: Interactive 5-candidate music search with inline button selection and 320kbps MP3 extraction.
- Cloud cookies vault with per-request sandbox isolation to defeat YouTube datacenter bot checks.
- Direct URL detection in private chat (paste any video link directly).
- Deno / EJS JavaScript challenge solver for YouTube.
- 2GB MTProto streaming upload support with live progress bars.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import os
import re
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional
import urllib.request
import yt_dlp
from telethon import Button, events
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

from core.dispatcher import bot_cmd, bot_callback, client_raw_handler, SafeMessageWrapper
from helpers.formatters import format_bytes

logger = logging.getLogger("novacore.plugin.media.downloader")

# Active download & song caches keyed by token
DL_SESSIONS: Dict[str, Dict[str, Any]] = {}
SONG_SESSIONS: Dict[str, Dict[str, Any]] = {}


def clean_media_target(text: str) -> str:
    """Strips Telegram markdown wrappers, brackets, and quotes from URLs."""
    if not text:
        return ""
    md_match = re.search(r'\[.*?\]\((https?://[^\s\)]+)\)', text)
    if md_match:
        return md_match.group(1).strip()

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


def is_youtube_url(url: str) -> bool:
    """Determines if a URL is hosted on YouTube."""
    if not url:
        return False
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u or "youtube-nocookie.com" in u


def is_pinterest_url(url: str) -> bool:
    """Determines if a URL is hosted on Pinterest or pin.it."""
    if not url:
        return False
    u = url.lower()
    return "pinterest.com" in u or "pin.it" in u


def is_direct_image_url(url: str) -> bool:
    """Determines if a URL points directly to an image file."""
    if not url:
        return False
    clean = url.split("?")[0].lower()
    return clean.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))


def extract_pinterest_media(url: str) -> Optional[Dict[str, Any]]:
    """Extracts original image or video from Pinterest pins."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            final_url = resp.geturl()
            html = resp.read().decode("utf-8", errors="ignore")

        # Extract Pin ID from final URL or original URL
        pin_match = re.search(r'/pin/(\d+)', final_url) or re.search(r'/pin/(\d+)', url)
        pin_id = pin_match.group(1) if pin_match else None

        # 1. Primary: Check OpenGraph meta tags (OpenGraph is strictly pin-specific)
        og_img_match = (
            re.search(r'<meta[^>]+(?:property|name)=[\'"]og:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
            or re.search(r'<meta[^>]+content=[\'"]([^\'"]+)[\'"][^>]+(?:property|name)=[\'"]og:image[\'"]', html, re.I)
        )
        og_title_match = (
            re.search(r'<meta[^>]+(?:property|name)=[\'"]og:title[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
            or re.search(r'<meta[^>]+content=[\'"]([^\'"]+)[\'"][^>]+(?:property|name)=[\'"]og:title[\'"]', html, re.I)
        )
        og_video_match = (
            re.search(r'<meta[^>]+(?:property|name)=[\'"]og:video(?::secure_url)?[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
            or re.search(r'<meta[^>]+content=[\'"]([^\'"]+)[\'"][^>]+(?:property|name)=[\'"]og:video(?::secure_url)?[\'"]', html, re.I)
        )

        title = og_title_match.group(1).strip() if og_title_match else "Pinterest Media"
        title = re.sub(r'\s*\|\s*Pinterest$', '', title, flags=re.I)

        # If OpenGraph has a video URL:
        if og_video_match:
            v_url = og_video_match.group(1).strip()
            if v_url.startswith("http"):
                return {"type": "video", "url": v_url, "title": title}

        # 2. Check JSON data specifically matching pin_id (for videos or original image assets)
        if pin_id:
            for m in re.finditer(r'<script[^>]*data-test-id=[\'"]resource-response-data[\'"][^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    data = json.loads(m.group(1))
                    res_data = data.get("resource_response", {}).get("data", {})
                    if isinstance(res_data, dict) and str(res_data.get("id")) == str(pin_id):
                        p_title = res_data.get("title") or res_data.get("grid_title") or title
                        # Video check
                        videos = res_data.get("videos")
                        if isinstance(videos, dict):
                            v_list = videos.get("video_list", {})
                            for v_qual in ["V_720P", "V_EXP7", "V_480P", "V_HLSV4"]:
                                if v_qual in v_list and "url" in v_list[v_qual]:
                                    return {"type": "video", "url": v_list[v_qual]["url"], "title": p_title}
                        # Image check
                        imgs = res_data.get("images", {})
                        for key in ["orig", "originals", "736x"]:
                            if key in imgs and "url" in imgs[key]:
                                orig = imgs[key]["url"]
                                if "/736x/" in orig:
                                    orig = orig.replace("/736x/", "/originals/")
                                return {"type": "image", "url": orig, "title": p_title}
                except Exception:
                    pass

        # 3. If OpenGraph has an image URL, upgrade to originals resolution
        if og_img_match:
            img_url = og_img_match.group(1).strip()
            orig_candidate = re.sub(r'/(?:736x|\d+x)/', '/originals/', img_url)
            try:
                check_req = urllib.request.Request(orig_candidate, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(check_req, timeout=5) as chk:
                    if chk.status == 200:
                        return {"type": "image", "url": orig_candidate, "title": title}
            except Exception:
                pass
            return {"type": "image", "url": img_url, "title": title}

        # 4. Fallback: Search for any originals URL containing pin_id or in HTML
        if pin_id:
            pin_img = re.search(r'https://i\.pinimg\.com/originals/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)', html)
            if pin_img:
                return {"type": "image", "url": pin_img.group(0), "title": title}

    except Exception as pe:
        logger.warning("Pinterest custom extractor failed: %s", pe)

    return None


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

    master_cookie = os.path.join("data", "cookies.txt")
    backup_cookie = os.path.join("data", "cookies_master.txt")

    # Auto-restore from backup if master cookie was missing or empty
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

    is_cb = isinstance(getattr(event, "_event", None), events.CallbackQuery.Event)
    if is_cb:
        status_msg = event
        await status_msg.edit("⚡ **Streaming audio track...**")
    else:
        status_msg = await event.reply("⚡ **Streaming audio track...**")

    try:
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
            await status_msg.edit("❌ **Error:** No audio track could be extracted.")
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

        await status_msg.edit(f"📤 **Uploading MP3 Audio:** `{format_bytes(file_size)}`...")

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
                pct = int(current / total * 100) if total else 0
                await status_msg.edit(
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
            reply_to=getattr(event, "topic_id", None),
        )
        await status_msg.delete()
    except Exception as e:
        err_msg = str(e)
        logger.exception("Song download failed: %s", err_msg)
        if "confirm you’re not a bot" in err_msg or "Sign in to confirm" in err_msg:
            await status_msg.edit(
                "🛡️ **YouTube Datacenter Protection**\n\n"
                "YouTube is challenging cloud server IPs. Send your YouTube cookies via `/cookies` to unlock."
            )
        else:
            await status_msg.edit(f"❌ **Song Download Failed:** `{err_msg[:250]}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@bot_cmd(
    pattern=r"song(?:\s+(.*))?",
    description="Search & download 320kbps MP3 songs with album info (/song <query>)",
    module="media",
)
async def song_cmd(event: SafeMessageWrapper, query: str = ""):
    raw_target = query.strip()
    if not raw_target:
        reply = await event.get_reply_message()
        if reply and reply.text:
            raw_target = reply.text.strip()

    if not raw_target:
        await event.reply(
            "🎵 **How to Download Music:**\n\n"
            "• `/song <song name or artist>` — Search top 5 tracks with selection buttons\n"
            "• `/song <youtube/audio url>` — Direct MP3 extraction from URL"
        )
        return

    target = clean_media_target(raw_target)

    # If it's a direct URL, download immediately
    if target.startswith(("http://", "https://")):
        status_msg = await event.reply(f"🎵 **Extracting audio from URL:** `{target}`...")
        await _download_and_send_song(status_msg, target)
        return

    # Search for top 5 candidates
    status_msg = await event.reply(f"🔎 **Searching music for:** `{target}`...")
    loop = asyncio.get_running_loop()

    def _search_tracks() -> List[Dict[str, Any]]:
        search_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "remote_components": ["ejs:github"],
        }
        tracks: List[Dict[str, Any]] = []
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
            logger.warning("YouTube search failed: %s, falling back to SoundCloud...", e)

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
        await status_msg.edit(f"❌ **No results found for:** `{target}`.\nTry adding the artist name.")
        return

    token = f"{event.chat_id}_{int(time.time())}"
    SONG_SESSIONS[token] = {
        "chat_id": event.chat_id,
        "entries": results,
        "time": time.time(),
    }

    buttons = []
    for idx, track in enumerate(results):
        dur = format_duration(track["duration"])
        label = f"{idx+1}. {track['title'][:30]} ({dur})"
        buttons.append([Button.inline(label, data=f"sg:{idx}:{token}")])

    buttons.append([Button.inline("❌ Cancel", data=f"sg:cancel:{token}")])

    await status_msg.edit(
        f"🎧 **Search Results for:** `{target}`\n"
        "Tap a track below to download in 320kbps MP3:",
        buttons=buttons,
    )


@bot_callback(r"^sg:([^:]+):(.+)$")
async def song_callback_handler(event: SafeMessageWrapper):
    data = event.data.decode("utf-8")
    parts = data.split(":", 2)
    action = parts[1]
    token = parts[2]

    if action == "cancel":
        SONG_SESSIONS.pop(token, None)
        await event.edit("🚫 **Music search cancelled.**")
        return

    sess = SONG_SESSIONS.get(token)
    if not sess:
        await event.edit("❌ **Search session expired.** Please search again with `/song <query>`.")
        return

    idx = int(action)
    entries = sess.get("entries", [])
    if idx >= len(entries):
        await event.edit("❌ **Invalid selection.**")
        return

    selected = entries[idx]
    SONG_SESSIONS.pop(token, None)
    await event.edit(f"⚡ **Downloading Track #{idx+1}:** `{selected['title']}`...")
    await _download_and_send_song(event, selected["url"], meta_hint=selected)


# ==========================================
# VIDEO / MEDIA DOWNLOAD ENGINE
# ==========================================

@bot_cmd(
    pattern=r"(?:dl|download|yt)(?:\s+(.*))?",
    description="Download media from YouTube, Instagram, TikTok, Twitter/X, Reddit (/dl <url>)",
    module="media",
)
async def download_cmd(event: SafeMessageWrapper, query: str = ""):
    raw_args = (query or "").strip()
    if not raw_args:
        reply = await event.get_reply_message()
        if reply and reply.text:
            raw_args = reply.text.strip()

    if not raw_args:
        await event.reply(
            "📥 **How to Download Media:**\n\n"
            "• `/dl <url>` — Direct download (YouTube shows quality buttons, all other sites download highest quality instantly)\n"
            "• `/dl 720 <url>` — Direct 720p HD\n"
            "• `/dl 1080 <url>` — Direct 1080p Full HD\n"
            "• `/dl best <url>` — Direct highest available quality\n"
            "• `/dl audio <url>` — Extract MP3 directly\n\n"
            "💡 *Tip: In private chat, you can paste any video link directly without `/dl`!*"
        )
        return

    # Check for explicit quality flag: e.g. /dl 720 <url>, /dl 1080 <url>, /dl best <url>
    explicit_quality = None
    target_str = raw_args
    parts = raw_args.split(maxsplit=1)

    if parts[0].lower() in ["1080", "720", "480", "360", "audio", "mp3", "best", "auto"]:
        explicit_quality = parts[0].lower()
        if explicit_quality == "auto":
            explicit_quality = "best"
        target_str = parts[1] if len(parts) > 1 else ""

    target_url = clean_media_target(target_str)
    if not target_url:
        reply = await event.get_reply_message()
        if reply and reply.text:
            target_url = clean_media_target(reply.text)

    if not target_url:
        await event.reply("❌ **Error:** No valid media URL found.")
        return

    await handle_media_url(event, target_url, explicit_quality=explicit_quality)


async def handle_media_url(event: SafeMessageWrapper, target_url: str, explicit_quality: Optional[str] = None):
    """Core handler for routing and downloading media URLs from any source."""
    # If explicit quality was specified, download immediately
    if explicit_quality:
        return await _execute_dl(event, target_url, quality=explicit_quality)

    # NON-YOUTUBE PLATFORMS (Instagram, TikTok, Twitter/X, Reddit, Pinterest, etc.):
    # Automatically download at highest available quality without showing quality menu!
    if not is_youtube_url(target_url):
        return await _execute_dl(event, target_url, quality="best")

    # YOUTUBE: Probe metadata and show interactive quality selection inline buttons!
    status_msg = await event.reply(f"🔎 **Inspecting YouTube media options...**\n`{target_url}`")
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
        title = info.get("title", "YouTube Video") if isinstance(info, dict) else "YouTube Video"
        uploader = (info.get("uploader") or info.get("channel") or "YouTube") if isinstance(info, dict) else "YouTube"
        dur_secs = info.get("duration", 0) if isinstance(info, dict) else 0
        dur_str = format_duration(dur_secs) if dur_secs else "N/A"
    except Exception:
        title = "YouTube Video"
        uploader = "YouTube"
        dur_str = "N/A"

    token = f"{event.chat_id}_{int(time.time())}"
    DL_SESSIONS[token] = {
        "chat_id": event.chat_id,
        "url": target_url,
        "title": title,
        "time": time.time(),
    }

    buttons = [
        [
            Button.inline("🎬 1080p FHD", data=f"dl:1080:{token}"),
            Button.inline("🎥 720p HD", data=f"dl:720:{token}"),
        ],
        [
            Button.inline("📺 480p SD", data=f"dl:480:{token}"),
            Button.inline("📱 360p Data Saver", data=f"dl:360:{token}"),
        ],
        [
            Button.inline("🎵 Audio Only (MP3 320k)", data=f"dl:audio:{token}"),
        ],
        [
            Button.inline("❌ Cancel", data=f"dl:cancel:{token}"),
        ],
    ]

    menu_text = (
        f"🎬 **YouTube Video Options:**\n"
        f"• **Title:** `{title[:55]}`\n"
        f"• **Channel:** `{uploader[:30]}` • ⏱️ `{dur_str}`\n\n"
        "👉 **Choose your desired download quality:**"
    )
    await status_msg.edit(menu_text, buttons=buttons)


@bot_callback(r"^dl:([^:]+):(.+)$")
async def download_callback_handler(event: SafeMessageWrapper):
    data = event.data.decode("utf-8")
    parts = data.split(":", 2)
    quality = parts[1]
    token = parts[2]

    if quality == "cancel":
        DL_SESSIONS.pop(token, None)
        await event.edit("🚫 **Download cancelled.**")
        return

    sess = DL_SESSIONS.get(token)
    if not sess:
        await event.edit("❌ **Download session expired.** Please send the link again.")
        return

    target_url = sess["url"]
    DL_SESSIONS.pop(token, None)
    await event.edit(f"⚡ **Downloading in `{quality}` resolution...**")
    await _execute_dl(event, target_url, quality=quality)


async def _execute_dl(event: SafeMessageWrapper, target_url: str, quality: str = "best"):
    """Downloads video according to specified resolution."""
    # Route audio quality to MP3 extraction
    if quality in ["audio", "mp3"]:
        return await _download_and_send_song(event, target_url)

    is_cb = isinstance(getattr(event, "_event", None), events.CallbackQuery.Event)
    if is_cb:
        status_msg = event
        await status_msg.edit(f"⚡ **Downloading media ({quality})...**\n`{target_url}`")
    else:
        status_msg = await event.reply(f"⚡ **Downloading media ({quality})...**\n`{target_url}`")

    loop = asyncio.get_running_loop()
    temp_dir = tempfile.mkdtemp(prefix="novacore_dl_")

    try:
        # Direct image URL handler
        if is_direct_image_url(target_url):
            try:
                await status_msg.edit(f"🖼️ **Downloading image...**\n`{target_url}`")
                ext = os.path.splitext(target_url.split("?")[0])[1] or ".jpg"
                img_path = os.path.join(temp_dir, f"image_{int(time.time())}{ext}")
                req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r, open(img_path, "wb") as f:
                    f.write(r.read())
                img_size = os.path.getsize(img_path)
                await status_msg.edit(f"📤 **Uploading Image:** `{format_bytes(img_size)}`...")
                await event.client.send_file(
                    event.chat_id,
                    file=img_path,
                    caption=f"🖼️ **Image**\n💾 `{format_bytes(img_size)}`",
                    reply_to=getattr(event, "topic_id", None),
                )
                await status_msg.delete()
                return
            except Exception as ie:
                logger.exception("Direct image download failed: %s", ie)

        # Pinterest Fast-Path (instant extraction of original resolution image or video)
        if is_pinterest_url(target_url) or "pinterest" in target_url.lower() or "pin.it" in target_url.lower():
            try:
                await status_msg.edit("📌 **Extracting high-resolution Pinterest media...**")
                res = await loop.run_in_executor(None, extract_pinterest_media, target_url)
                if res and res.get("url"):
                    media_url = res["url"]
                    title = res.get("title", "Pinterest Media")
                    is_vid = res.get("type") == "video"
                    ext = ".mp4" if is_vid else (".png" if ".png" in media_url.lower() else ".jpg")
                    out_path = os.path.join(temp_dir, f"pin_{int(time.time())}{ext}")

                    req = urllib.request.Request(media_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=15) as r, open(out_path, "wb") as f:
                        f.write(r.read())

                    m_size = os.path.getsize(out_path)
                    await status_msg.edit(f"📤 **Uploading to Telegram:** `{format_bytes(m_size)}`...")
                    caption = f"📌 **{title}**\n💾 `{format_bytes(m_size)}` • 🎯 `Original Quality`"
                    await event.client.send_file(
                        event.chat_id,
                        file=out_path,
                        caption=caption,
                        reply_to=getattr(event, "topic_id", None),
                    )
                    await status_msg.delete()
                    return
            except Exception as pe:
                logger.warning("Pinterest fast-path failed, falling back to yt-dlp: %s", pe)

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
                        asyncio.run_coroutine_threadsafe(status_msg.edit(text), loop)
                    except Exception:
                        pass

        ydl_opts = get_base_ydl_opts(temp_dir, ytdl_progress_hook)

        # Resilient format selector for portrait, landscape, and fallback
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

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(target_url, download=True)

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
            await status_msg.edit("❌ **Error:** No media file was produced.")
            return

        file_path = downloaded_files[0]
        file_size = os.path.getsize(file_path)
        title = info.get("title", "Media") if isinstance(info, dict) else "Media"

        # MTProto Bot upload limit: 2000 MB (2GB)
        max_limit = 2000 * 1024 * 1024
        if file_size > max_limit:
            await status_msg.edit(
                f"⚠️ **File size exceeds Telegram 2GB limit:**\n"
                f"• Size: `{format_bytes(file_size)}`\n"
                f"• Limit: `{format_bytes(max_limit)}`"
            )
            return

        await status_msg.edit(f"📤 **Uploading to Telegram:** `{format_bytes(file_size)}`...")

        last_up_time = [0.0]

        async def upload_progress(current, total):
            now = time.time()
            if now - last_up_time[0] >= 3.5:
                last_up_time[0] = now
                pct = int(current / total * 100) if total else 0
                await status_msg.edit(
                    f"📤 **Uploading:** `{pct}%` (`{format_bytes(current)}` / `{format_bytes(total)}`)"
                )

        await event.client.send_file(
            event.chat_id,
            file=file_path,
            caption=f"🎬 **{title}**\n💾 `{format_bytes(file_size)}` • 🎯 `{quality}`",
            progress_callback=upload_progress,
            reply_to=getattr(event, "topic_id", None),
        )
        await status_msg.delete()
    except Exception as e:
        err_msg = str(e)
        logger.exception("Downloader failed: %s", err_msg)
        if is_pinterest_url(target_url) or "pinterest" in target_url.lower() or "pin.it" in target_url.lower():
            try:
                await status_msg.edit("📌 **Extracting high-resolution Pinterest media...**")
                res = await loop.run_in_executor(None, extract_pinterest_media, target_url)
                if res and res.get("url"):
                    media_url = res["url"]
                    title = res.get("title", "Pinterest Media")
                    is_vid = res.get("type") == "video"
                    ext = ".mp4" if is_vid else (".png" if ".png" in media_url.lower() else ".jpg")
                    out_path = os.path.join(temp_dir, f"pin_{int(time.time())}{ext}")

                    req = urllib.request.Request(media_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=15) as r, open(out_path, "wb") as f:
                        f.write(r.read())

                    m_size = os.path.getsize(out_path)
                    await status_msg.edit(f"📤 **Uploading to Telegram:** `{format_bytes(m_size)}`...")
                    caption = f"📌 **{title}**\n💾 `{format_bytes(m_size)}` • 🎯 `Original Quality`"
                    await event.client.send_file(
                        event.chat_id,
                        file=out_path,
                        caption=caption,
                        reply_to=getattr(event, "topic_id", None),
                    )
                    await status_msg.delete()
                    return
            except Exception as pe:
                logger.exception("Pinterest fallback extraction failed: %s", pe)

        if "confirm you’re not a bot" in err_msg or "Sign in to confirm" in err_msg:
            await status_msg.edit(
                "🛡️ **YouTube Datacenter Protection**\n\n"
                "YouTube is challenging cloud server IPs. Send your YouTube cookies via `/cookies` to unlock."
            )
        elif any(k in err_msg.lower() for k in [
            "isn't available to everyone",
            "can't be seen by certain audiences",
            "restricted video",
            "login required",
            "please log in",
            "only available for registered users",
            "gated_logged_out",
        ]):
            await status_msg.edit(
                "🔞 **Instagram Audience / Age Restriction**\n\n"
                "Instagram has flagged this specific reel behind an account login check (18+, sensitive media, or audience restriction).\n\n"
                "💡 **Standard public reels & videos download automatically without login.**\n\n"
                "🔓 **To download restricted reels:**\n"
                "1. Open Instagram in your browser and ensure you are logged in.\n"
                "2. Export `cookies.txt` using the `Get cookies.txt locally` browser extension.\n"
                "3. Send the file here and reply to it with `/cookies`!"
            )
        elif "verify your age" in err_msg.lower() or "confirm your age" in err_msg.lower():
            await status_msg.edit(
                "🔞 **Age-Restricted Video**\n\n"
                "This video is age-restricted and requires account verification.\n"
                "💡 Reply to your exported `cookies.txt` with `/cookies` to permanently unlock!"
            )
        elif "No video formats found" in err_msg:
            await status_msg.edit("🔞 **Content Restricted / No Streams Available:** Video requires login or has been removed.")
        elif "HTTP Error 404" in err_msg or "video has been removed" in err_msg:
            await status_msg.edit("❌ **Video Not Found:** This link appears to be deleted or private.")
        else:
            await status_msg.edit(f"❌ **Download Failed:** `{err_msg[:250]}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ==========================================
# DIRECT LINK LISTENER (ZERO-COMMAND AUTO DOWNLOAD)
# ==========================================

@client_raw_handler(events.NewMessage(
    incoming=True,
    func=lambda e: not (getattr(e, "text", "") or "").startswith(("/", "."))
    and ("http://" in (getattr(e, "text", "") or "") or "https://" in (getattr(e, "text", "") or ""))
))
async def direct_link_listener(event: SafeMessageWrapper):
    """Automatically detects media links pasted directly without needing /dl."""
    is_pm = getattr(event, "is_private", True)
    is_mentioned = getattr(event, "mentioned", False)

    # In groups only process if bot is tagged or mentioned; in PM process all links directly
    if not is_pm and not is_mentioned:
        return

    text = (getattr(event.message, "text", "") or getattr(event.message, "message", "") or "").strip()
    if not text:
        return

    target_url = clean_media_target(text)
    if not target_url or not target_url.startswith(("http://", "https://")):
        return

    logger.info("Auto-detected direct media link: %s in chat %s", target_url, event.chat_id)
    # Trigger download pipeline directly!
    await handle_media_url(event, target_url)


# ==========================================
# CLOUD COOKIES SYSTEM (PROTECTED MASTER VAULT)
# ==========================================

@bot_cmd(
    pattern=r"cookies(?:\s+(status|backup|restore|info))?",
    description="Manage cloud cookies vault for YouTube & age-gated videos (/cookies)",
    module="media",
)
async def cookies_cmd(event: SafeMessageWrapper, query: str = ""):
    subcmd = (query or "").strip().lower()
    reply = await event.get_reply_message()

    os.makedirs("data", exist_ok=True)
    cookie_path = os.path.join("data", "cookies.txt")
    backup_path = os.path.join("data", "cookies_master.txt")

    if subcmd == "restore":
        if not os.path.exists(backup_path) or os.path.getsize(backup_path) == 0:
            await event.reply("❌ **No master backup found.** Upload a fresh `cookies.txt` first.")
            return
        shutil.copyfile(backup_path, cookie_path)
        await event.reply("✅ **Cookies restored successfully from master backup vault!**")
        return

    if subcmd == "backup":
        if not os.path.exists(cookie_path) or os.path.getsize(cookie_path) == 0:
            await event.reply("❌ **No active cookies found to back up.**")
            return
        shutil.copyfile(cookie_path, backup_path)
        await event.reply("✅ **Pristine master cookie backup snapshot created!**")
        return

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

            await event.reply(
                f"🍪 **NovaCore Cloud Cookies Vault**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• **Status:** 🟢 Active (`{total_cookies}` cookies stored)\n"
                f"• **YouTube Auth:** {has_yt}\n"
                f"• **Master Backup:** {has_backup}\n"
                f"• **Last Modified:** `{mtime}`\n"
                f"• **Active Domains:** {top_domains or 'None'}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📥 **To update:** Send your exported `cookies.txt` file here and reply to it with `/cookies`."
            )
            return
        else:
            await event.reply(
                "🍪 **No Cookies Installed in Cloud Vault**\n\n"
                "To unlock YouTube on datacenter IPs:\n"
                "1. Export `cookies.txt` from your YouTube tab using the `Get cookies.txt locally` browser extension.\n"
                "2. Send that `.txt` file here and reply to it with `/cookies`!"
            )
            return

    status_msg = await event.reply("🍪 **Validating and merging cookies into cloud vault...**")
    try:
        temp_cookie_path = os.path.join("data", "cookies_temp.txt")
        await event.client.download_media(reply, file=temp_cookie_path)

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
            await status_msg.edit("❌ **Error:** No valid Netscape cookies found in the uploaded file.")
            return

        all_active_domains = set(new_domains.keys())
        kept_lines: List[str] = []
        if os.path.exists(cookie_path):
            with open(cookie_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.strip().split("\t")
                    if len(parts) >= 6:
                        domain = parts[0].lstrip(".").lower()
                        if domain in new_domains:
                            continue
                        all_active_domains.add(domain)
                        kept_lines.append(line)

        with open(cookie_path, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n# Managed by NovaCore Engine Vault (Protected Master)\n\n")
            for line in kept_lines:
                f.write(line.rstrip("\r\n") + "\n")
            for domain, lines in new_domains.items():
                for line in lines:
                    f.write(line.rstrip("\r\n") + "\n")

        shutil.copyfile(cookie_path, backup_path)
        if os.path.exists(temp_cookie_path):
            os.remove(temp_cookie_path)

        updated_sample = ", ".join(list(new_domains.keys())[:3])
        await status_msg.edit(
            "✅ **Cookies Successfully Saved & Permanently Vaulted!**\n\n"
            f"• **Updated Site(s):** `{updated_sample}`\n"
            f"• **Master Backup:** Created (`data/cookies_master.txt`)\n\n"
            "🎉 YouTube and age-gated videos are unlocked!"
        )
    except Exception as e:
        logger.exception("Failed to save cookies: %s", e)
        await status_msg.edit(f"❌ Failed to save cookies: `{e}`")
