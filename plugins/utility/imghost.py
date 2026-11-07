"""
NovaCore: Instant CDN Image & File Host (/upload, /tgm, /catbox)
Uploads media to high-speed public CDN (Catbox / Tmpfiles) and returns permanent direct links.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from typing import Optional, Tuple

from telethon import Button
from core.dispatcher import bot_cmd, SafeMessageWrapper
from helpers.formatters import format_bytes

logger = logging.getLogger("novacore.plugin.utility.imghost")


def get_target_filename(msg) -> str:
    """Extracts clean filename from message attributes or default."""
    if hasattr(msg, "file") and msg.file and msg.file.name:
        return msg.file.name
    if getattr(msg, "photo", None):
        return f"photo_{int(time.time())}.jpg"
    if getattr(msg, "video", None):
        return f"video_{int(time.time())}.mp4"
    if getattr(msg, "audio", None):
        return f"audio_{int(time.time())}.mp3"
    if getattr(msg, "voice", None):
        return f"voice_{int(time.time())}.ogg"
    if getattr(msg, "gif", None):
        return f"animation_{int(time.time())}.mp4"
    return f"file_{int(time.time())}.bin"


def upload_to_catbox(file_path: str) -> Optional[str]:
    """Uploads file to Catbox.moe and returns direct permanent URL."""
    try:
        boundary = uuid.uuid4().hex
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
        }
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(b'Content-Disposition: form-data; name="reqtype"\r\n\r\n')
        body.extend(b"fileupload\r\n")

        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="fileToUpload"; filename="{filename}"\r\n'.encode("utf-8"))
        body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
        body.extend(file_bytes)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        req = urllib.request.Request("https://catbox.moe/user/api.php", data=bytes(body), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            res_url = resp.read().decode("utf-8").strip()
            if res_url.startswith("http"):
                return res_url
    except Exception as e:
        logger.warning("Catbox upload failed: %s", e)
    return None


def upload_to_tmpfiles(file_path: str) -> Optional[str]:
    """Fallback upload to tmpfiles.org returning direct download link."""
    try:
        boundary = uuid.uuid4().hex
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Mozilla/5.0",
        }
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"))
        body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
        body.extend(file_bytes)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        req = urllib.request.Request("https://tmpfiles.org/api/v1/upload", data=bytes(body), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success":
                url = data.get("data", {}).get("url", "")
                if url:
                    # Convert to direct link https://tmpfiles.org/123/name -> https://tmpfiles.org/dl/123/name
                    return url.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/")
    except Exception as e:
        logger.warning("Tmpfiles upload failed: %s", e)
    return None


@bot_cmd(
    pattern=r"(?:upload|tgm|catbox|cdn)",
    description="Upload replied media/file to permanent CDN (/upload [reply])",
    module="utility",
)
async def upload_cdn_cmd(event: SafeMessageWrapper, query: str = ""):
    reply = await event.get_reply_message()
    if not reply or not (
        getattr(reply, "media", None)
        or getattr(reply, "photo", None)
        or getattr(reply, "document", None)
        or getattr(reply, "video", None)
        or getattr(reply, "audio", None)
    ):
        await event.reply(
            "🌐 **Instant CDN File Host:**\n\n"
            "• Reply `/upload` or `/tgm` to any photo, GIF, video, or document (up to 200MB).\n"
            "• Get an instant, permanent public direct URL to share on Reddit, Discord, forums, or GitHub!"
        )
        return

    filename = get_target_filename(reply)
    status_msg = await event.reply(f"⏳ **Downloading `{filename}` for CDN hosting...**")
    temp_dir = tempfile.mkdtemp(prefix="novacore_imghost_")
    local_path = os.path.join(temp_dir, filename)

    try:
        last_down_time = [0.0]

        async def download_progress(current, total):
            now = time.time()
            if now - last_down_time[0] >= 3.5:
                last_down_time[0] = now
                pct = int(current / total * 100) if total else 0
                await status_msg.edit(
                    f"📥 **Downloading:** `{pct}%` (`{format_bytes(current)}` / `{format_bytes(total)}`)..."
                )

        saved = await event.client.download_media(reply, file=local_path, progress_callback=download_progress)
        if not saved or not os.path.exists(saved):
            await status_msg.edit("❌ **Error:** Failed to download media.")
            return

        fsize = os.path.getsize(saved)
        # Limit to 200MB for free public CDNs
        if fsize > 200 * 1024 * 1024:
            await status_msg.edit("⚠️ **File too large:** Public CDN hosts support up to 200MB per file.")
            return

        await status_msg.edit(f"🚀 **Uploading `{filename}` ({format_bytes(fsize)}) to CDN...**")
        loop = asyncio.get_running_loop()

        def _do_upload() -> Tuple[Optional[str], str]:
            u = upload_to_catbox(saved)
            if u:
                return u, "Catbox CDN (Permanent)"
            u = upload_to_tmpfiles(saved)
            if u:
                return u, "Tmpfiles CDN"
            return None, ""

        direct_url, provider = await loop.run_in_executor(None, _do_upload)

        if not direct_url:
            await status_msg.edit("❌ **Upload failed:** Public CDN servers are temporarily unreachable.")
            return

        text = (
            f"✅ **File Hosted Successfully!**\n\n"
            f"• **Direct Link:** `{direct_url}`\n"
            f"• **File Name:** `{filename}`\n"
            f"• **File Size:** `{format_bytes(fsize)}`\n"
            f"• **Storage:** `{provider}`\n\n"
            f"💡 *Click the link below or tap above to copy!*"
        )
        buttons = [[Button.url("🌐 Open Direct Link", url=direct_url)]]

        await status_msg.edit(text, buttons=buttons, link_preview=False)

    except Exception as e:
        logger.exception("Upload to CDN failed: %s", e)
        await status_msg.edit(f"❌ **Upload Error:** `{e}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
