"""
NovaCore: Custom QR Studio (/qr, /readqr)
- /qr <text/link> - Generates high-resolution custom styled QR codes
- /readqr (reply to photo) - Scans and decodes QR codes with smart WiFi, URL & text detection
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from typing import Optional

from PIL import Image, ImageDraw, ImageOps
import qrcode
from qrcode.constants import ERROR_CORRECT_H

from core.dispatcher import bot_cmd, SafeMessageWrapper
from helpers.formatters import format_bytes

logger = logging.getLogger("novacore.plugin.utility.qrstudio")


def generate_styled_qr(data: str, style: str = "classic") -> str:
    """Generates a styled QR code image and saves it to a file path."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=12,
        border=3,
    )
    qr.add_data(data)
    qr.make(fit=True)

    # Color themes
    themes = {
        "classic": ("#000000", "#FFFFFF"),
        "dark": ("#00F2FE", "#0B0C10"),
        "purple": ("#8B5CF6", "#0B0C10"),
        "gold": ("#F59E0B", "#18181B"),
        "cyan": ("#06B6D4", "#FFFFFF"),
        "emerald": ("#10B981", "#0B0C10"),
    }
    fill_col, back_col = themes.get(style.lower(), themes["classic"])

    base_img = qr.make_image(fill_color=fill_col, back_color=back_col).convert("RGBA")

    # Add a subtle stylish padded border
    pad = 24
    w, h = base_img.size
    final_w, final_h = w + (pad * 2), h + (pad * 2)

    bg_color = (11, 12, 16, 255) if back_col.startswith("#0") or back_col.startswith("#1") else (255, 255, 255, 255)
    canvas = Image.new("RGBA", (final_w, final_h), bg_color)
    canvas.paste(base_img, (pad, pad))

    temp_dir = tempfile.mkdtemp(prefix="novacore_qr_")
    out_path = os.path.join(temp_dir, f"qrcode_{int(time.time())}.png")
    canvas.save(out_path, "PNG")
    return out_path


def parse_wifi_payload(payload: str) -> Optional[dict]:
    """Parses standard WIFI:S:...;T:...;P:...;; format."""
    if not payload.startswith("WIFI:"):
        return None
    res = {}
    m_ssid = re.search(r'S:([^;]+);', payload)
    m_type = re.search(r'T:([^;]+);', payload)
    m_pass = re.search(r'P:([^;]+);', payload)
    if m_ssid:
        res["ssid"] = m_ssid.group(1)
        res["type"] = m_type.group(1) if m_type else "WPA"
        res["password"] = m_pass.group(1) if m_pass else ""
        return res
    return None


@bot_cmd(
    pattern=r"(?:qr|qrcode)(?:\s+(.*))?",
    description="Generate high-resolution QR codes (/qr [style] <text/link>)",
    module="utility",
)
async def qr_cmd(event: SafeMessageWrapper, query: str = ""):
    raw_text = (query or "").strip()
    if not raw_text:
        reply = await event.get_reply_message()
        if reply and reply.text:
            raw_text = reply.text.strip()

    if not raw_text:
        await event.reply(
            "📱 **Custom QR Studio:**\n\n"
            "• `/qr <link or text>` — Generate clean high-resolution QR code\n"
            "• `/qr dark <link or text>` — Sleek dark-mode neon cyan QR\n"
            "• `/qr purple <link or text>` — Electric purple theme\n"
            "• `/qr gold <link or text>` — Luxury gold & obsidian theme\n\n"
            "💡 *Tip: Reply `/qr` to any message to turn it into a QR code!*"
        )
        return

    # Check for optional style flag
    parts = raw_text.split(maxsplit=1)
    style = "classic"
    content = raw_text
    valid_styles = ["classic", "dark", "purple", "gold", "cyan", "emerald"]

    if parts[0].lower() in valid_styles and len(parts) > 1:
        style = parts[0].lower()
        content = parts[1].strip()

    status_msg = await event.reply("⚡ **Generating high-resolution QR code...**")
    loop = asyncio.get_running_loop()

    out_file = None
    try:
        out_file = await loop.run_in_executor(None, generate_styled_qr, content, style)
        fsize = os.path.getsize(out_file)

        caption = (
            f"📱 **QR Code Generated**\n"
            f"• **Theme:** `{style.capitalize()}`\n"
            f"• **Length:** `{len(content)} characters`\n"
            f"• **Payload Preview:** `{content[:120]}`\n\n"
            f"💾 `{format_bytes(fsize)}`"
        )

        await event.client.send_file(
            event.chat_id,
            file=out_file,
            caption=caption,
            reply_to=getattr(event, "topic_id", None),
        )
        await status_msg.delete()
    except Exception as e:
        logger.exception("QR generation failed: %s", e)
        await status_msg.edit(f"❌ **QR Generation Failed:** `{e}`")
    finally:
        if out_file and os.path.exists(out_file):
            shutil.rmtree(os.path.dirname(out_file), ignore_errors=True)


@bot_cmd(
    pattern=r"(?:readqr|scanqr|decodeqr)",
    description="Scan and decode QR codes from photos (/readqr [reply to photo])",
    module="utility",
)
async def readqr_cmd(event: SafeMessageWrapper, query: str = ""):
    reply = await event.get_reply_message()
    if not reply or not (getattr(reply, "photo", None) or getattr(reply, "document", None) or getattr(reply, "media", None)):
        await event.reply("🔍 **How to Scan QR Codes:**\n\nReply `/readqr` to any photo, screenshot, or document containing a QR code!")
        return

    status_msg = await event.reply("🔍 **Scanning and decoding QR code...**")
    temp_dir = tempfile.mkdtemp(prefix="novacore_readqr_")
    img_path = os.path.join(temp_dir, f"scan_{int(time.time())}.png")

    try:
        downloaded = await event.client.download_media(reply, file=img_path)
        if not downloaded or not os.path.exists(downloaded):
            await status_msg.edit("❌ **Error:** Could not download image.")
            return

        loop = asyncio.get_running_loop()

        def _decode_qr() -> Optional[str]:
            # Use api.qrserver.com/v1/read-qr-code/
            with open(downloaded, "rb") as f:
                img_bytes = f.read()

            boundary = uuid.uuid4().hex
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
            body = bytearray()
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(b'Content-Disposition: form-data; name="file"; filename="image.png"\r\n')
            body.extend(b"Content-Type: image/png\r\n\r\n")
            body.extend(img_bytes)
            body.extend(b"\r\n")
            body.extend(f"--{boundary}--\r\n".encode("utf-8"))

            req = urllib.request.Request(
                "https://api.qrserver.com/v1/read-qr-code/",
                data=bytes(body),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list) and len(data) > 0:
                    symbols = data[0].get("symbol", [])
                    if symbols and symbols[0].get("data"):
                        return symbols[0]["data"]
            return None

        result = await loop.run_in_executor(None, _decode_qr)

        if not result:
            await status_msg.edit("❌ **No QR code detected.** Make sure the QR code is clear, unblurred, and well-lit.")
            return

        # Smart payload formatting
        wifi_info = parse_wifi_payload(result)
        if wifi_info:
            text = (
                "📶 **WiFi Network Detected:**\n\n"
                f"• **Network (SSID):** `{wifi_info['ssid']}`\n"
                f"• **Security:** `{wifi_info['type']}`\n"
                f"• **Password:** `{wifi_info['password']}`\n\n"
                "💡 *Tap password to copy!*"
            )
        elif result.startswith(("http://", "https://")):
            text = (
                "🌐 **URL Decoded from QR:**\n\n"
                f"🔗 [{result}]({result})\n\n"
                f"📋 **Raw URL:** `{result}`"
            )
        else:
            text = (
                "📋 **QR Code Content Decoded:**\n\n"
                f"```{result}```"
            )

        await status_msg.edit(text, link_preview=False)

    except Exception as e:
        logger.exception("Failed to scan QR: %s", e)
        await status_msg.edit(f"❌ **Scan Error:** `{e}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
