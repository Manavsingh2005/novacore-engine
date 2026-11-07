"""
NovaCore: Universal File Conversion Studio (/convert)
Converts:
- Videos (MKV, WEBM, AVI, MOV, FLV, MP4) -> MP4, MP3 320k, GIF, Voice Note, Video Note
- Audio (MP3, WAV, M4A, FLAC, OGG, AAC) -> MP3, WAV, M4A, Voice Note, FLAC
- Images (JPG, PNG, WEBP, BMP) -> JPG, PNG, PDF, WebP Sticker (512x512)
- Documents (PDF -> PNG images, Text -> PDF)
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
from typing import Any, Dict, List, Optional
from PIL import Image
import fitz  # PyMuPDF
from telethon import Button
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
)

from core.dispatcher import bot_cmd, bot_callback, SafeMessageWrapper
from helpers.formatters import format_bytes

logger = logging.getLogger("novacore.plugin.utility.converter")

# Active conversion sessions: {token: {"chat_id": int, "msg_id": int, "time": float}}
CONV_SESSIONS: Dict[str, Dict[str, Any]] = {}


def get_file_name(msg) -> str:
    """Extracts filename or default from message."""
    if not msg:
        return "file"
    if msg.file and msg.file.name:
        return msg.file.name
    if msg.document:
        for attr in msg.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name
    if msg.video:
        return "video.mp4"
    if msg.audio:
        return "audio.mp3"
    if msg.voice:
        return "voice.ogg"
    if msg.photo:
        return "photo.jpg"
    return "file"


def get_file_category(msg, filename: str) -> str:
    """Categorizes file into 'video', 'audio', 'image', 'pdf', or 'text'."""
    fn = filename.lower()
    if getattr(msg, "video", False) or fn.endswith((".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m4v", ".ts", ".3gp")):
        return "video"
    if getattr(msg, "audio", False) or getattr(msg, "voice", False) or fn.endswith((".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma")):
        return "audio"
    if getattr(msg, "photo", False) or fn.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff")):
        return "image"
    if fn.endswith(".pdf"):
        return "pdf"
    if fn.endswith((".txt", ".py", ".json", ".csv", ".log", ".md", ".html", ".js")):
        return "text"
    return "unknown"


@bot_cmd(
    pattern=r"(?:convert|conv)(?:\s+(.*))?",
    description="Universal media & file format converter (/convert)",
    module="utility",
)
async def convert_cmd(event: SafeMessageWrapper, query: str = ""):
    reply = await event.get_reply_message()
    target_msg = reply if (reply and (reply.media or reply.file)) else (event.message if (event.message.media or event.message.file) else None)

    if not target_msg or not (target_msg.media or target_msg.file):
        await event.reply(
            "🔄 **How to Convert Files:**\n\n"
            "Reply `/convert` to any video, audio, image, PDF, or text file.\n"
            "The bot will display an interactive inline menu with all available conversion targets!"
        )
        return

    filename = get_file_name(target_msg)
    category = get_file_category(target_msg, filename)

    token = f"{event.chat_id}_{target_msg.id}_{int(time.time())}"
    CONV_SESSIONS[token] = {
        "chat_id": event.chat_id,
        "msg_id": target_msg.id,
        "filename": filename,
        "category": category,
        "time": time.time(),
    }

    buttons = []
    if category == "video":
        buttons = [
            [
                Button.inline("🎵 Extract MP3 (320k)", data=f"cv:to_mp3:{token}"),
                Button.inline("🎬 Convert to MP4", data=f"cv:to_mp4:{token}"),
            ],
            [
                Button.inline("🎞️ Animated GIF", data=f"cv:to_gif:{token}"),
                Button.inline("🗣️ Voice Note", data=f"cv:to_voice:{token}"),
            ],
            [
                Button.inline("⭕ Round Video Note", data=f"cv:to_vnote:{token}"),
                Button.inline("📱 To M4A Audio", data=f"cv:to_m4a:{token}"),
            ],
            [Button.inline("❌ Cancel", data=f"cv:cancel:{token}")],
        ]
        prompt = f"🎬 **Video Detected:** `{filename}`\nSelect your desired output format:"

    elif category == "audio":
        buttons = [
            [
                Button.inline("🎵 MP3 (320kbps)", data=f"cv:to_mp3:{token}"),
                Button.inline("🔊 Lossless WAV", data=f"cv:to_wav:{token}"),
            ],
            [
                Button.inline("🗣️ Voice Note (Opus)", data=f"cv:to_voice:{token}"),
                Button.inline("📱 AAC / M4A", data=f"cv:to_m4a:{token}"),
            ],
            [
                Button.inline("🎧 FLAC", data=f"cv:to_flac:{token}"),
                Button.inline("❌ Cancel", data=f"cv:cancel:{token}"),
            ],
        ]
        prompt = f"🎵 **Audio Detected:** `{filename}`\nSelect your desired output format:"

    elif category == "image":
        buttons = [
            [
                Button.inline("🖼️ To JPG", data=f"cv:to_jpg:{token}"),
                Button.inline("🎨 To PNG", data=f"cv:to_png:{token}"),
            ],
            [
                Button.inline("📄 To PDF Document", data=f"cv:to_pdf:{token}"),
                Button.inline("🎭 WebP Sticker (512x512)", data=f"cv:to_sticker:{token}"),
            ],
            [Button.inline("❌ Cancel", data=f"cv:cancel:{token}")],
        ]
        prompt = f"🖼️ **Image Detected:** `{filename}`\nSelect your desired output format:"

    elif category == "pdf":
        buttons = [
            [
                Button.inline("🖼️ Extract Pages as PNG", data=f"cv:pdf_to_images:{token}"),
                Button.inline("📄 Extract Text (.txt)", data=f"cv:pdf_to_txt:{token}"),
            ],
            [Button.inline("❌ Cancel", data=f"cv:cancel:{token}")],
        ]
        prompt = f"📄 **PDF Document Detected:** `{filename}`\nSelect your desired action:"

    elif category == "text":
        buttons = [
            [
                Button.inline("📄 Convert to PDF", data=f"cv:txt_to_pdf:{token}"),
                Button.inline("❌ Cancel", data=f"cv:cancel:{token}"),
            ]
        ]
        prompt = f"📝 **Text File Detected:** `{filename}`\nSelect your desired action:"

    else:
        await event.reply(f"⚠️ **Unsupported file type:** `{filename}`")
        return

    await event.reply(prompt, buttons=buttons)


@bot_callback(r"^cv:([^:]+):(.+)$")
async def converter_callback_handler(event: SafeMessageWrapper):
    data = event.data.decode("utf-8")
    parts = data.split(":", 2)
    action = parts[1]
    token = parts[2]

    if action == "cancel":
        CONV_SESSIONS.pop(token, None)
        await event.edit("🚫 **Conversion cancelled.**")
        return

    sess = CONV_SESSIONS.get(token)
    if not sess:
        await event.edit("❌ **Conversion session expired.** Reply `/convert` again.")
        return

    chat_id = sess["chat_id"]
    msg_id = sess["msg_id"]
    filename = sess["filename"]

    try:
        source_msg = await event.client.get_messages(chat_id, ids=msg_id)
    except Exception:
        source_msg = None

    if not source_msg or not (source_msg.media or source_msg.file):
        await event.edit("❌ **Original message not found or deleted.**")
        return

    await event.edit(f"⚡ **Downloading `{filename}` for conversion...**")
    loop = asyncio.get_running_loop()
    temp_dir = tempfile.mkdtemp(prefix="novacore_conv_")
    src_path = os.path.join(temp_dir, filename)

    try:
        last_down_time = [0.0]

        async def download_progress(current, total):
            now = time.time()
            if now - last_down_time[0] >= 3.5:
                last_down_time[0] = now
                pct = int(current / total * 100) if total else 0
                await event.edit(f"📥 **Downloading:** `{pct}%` (`{format_bytes(current)}` / `{format_bytes(total)}`)...")

        await event.client.download_media(source_msg, file=src_path, progress_callback=download_progress)
        base_name = os.path.splitext(filename)[0]

        await event.edit("🔄 **Executing format conversion...**")

        out_path: Optional[str] = None
        extra_attrs = []
        is_voice = False
        is_video_note = False
        multiple_files: List[str] = []

        # ==================== VIDEO CONVERSIONS ====================
        if action == "to_mp3":
            out_path = os.path.join(temp_dir, f"{base_name}.mp3")
            cmd = ["ffmpeg", "-y", "-i", src_path, "-vn", "-acodec", "libmp3lame", "-b:a", "320k", out_path]
            subprocess.run(cmd, check=True, capture_output=True)
            extra_attrs.append(DocumentAttributeAudio(duration=0, title=base_name, performer="NovaCore Converter"))

        elif action == "to_mp4":
            out_path = os.path.join(temp_dir, f"{base_name}_converted.mp4")
            cmd = ["ffmpeg", "-y", "-i", src_path, "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", out_path]
            subprocess.run(cmd, check=True, capture_output=True)
            extra_attrs.append(DocumentAttributeVideo(duration=0, w=1280, h=720, supports_streaming=True))

        elif action == "to_gif":
            out_path = os.path.join(temp_dir, f"{base_name}.gif")
            cmd = [
                "ffmpeg", "-y", "-i", src_path, "-t", "30",
                "-vf", "fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                out_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True)

        elif action == "to_voice":
            out_path = os.path.join(temp_dir, f"{base_name}.ogg")
            cmd = ["ffmpeg", "-y", "-i", src_path, "-vn", "-c:a", "libopus", "-b:a", "64k", out_path]
            subprocess.run(cmd, check=True, capture_output=True)
            is_voice = True

        elif action == "to_vnote":
            out_path = os.path.join(temp_dir, f"{base_name}_vnote.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", src_path, "-t", "60",
                "-vf", "crop=min(iw\\,ih):min(iw\\,ih),scale=384:384",
                "-c:v", "libx264", "-c:a", "aac", out_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            is_video_note = True

        elif action == "to_m4a":
            out_path = os.path.join(temp_dir, f"{base_name}.m4a")
            cmd = ["ffmpeg", "-y", "-i", src_path, "-vn", "-c:a", "aac", "-b:a", "256k", out_path]
            subprocess.run(cmd, check=True, capture_output=True)
            extra_attrs.append(DocumentAttributeAudio(duration=0, title=base_name, performer="NovaCore Converter"))

        elif action == "to_wav":
            out_path = os.path.join(temp_dir, f"{base_name}.wav")
            cmd = ["ffmpeg", "-y", "-i", src_path, "-vn", "-acodec", "pcm_s16le", out_path]
            subprocess.run(cmd, check=True, capture_output=True)
            extra_attrs.append(DocumentAttributeAudio(duration=0, title=base_name, performer="NovaCore Converter"))

        elif action == "to_flac":
            out_path = os.path.join(temp_dir, f"{base_name}.flac")
            cmd = ["ffmpeg", "-y", "-i", src_path, "-vn", "-acodec", "flac", out_path]
            subprocess.run(cmd, check=True, capture_output=True)
            extra_attrs.append(DocumentAttributeAudio(duration=0, title=base_name, performer="NovaCore Converter"))

        # ==================== IMAGE CONVERSIONS ====================
        elif action == "to_jpg":
            out_path = os.path.join(temp_dir, f"{base_name}.jpg")
            with Image.open(src_path) as im:
                im.convert("RGB").save(out_path, "JPEG", quality=95)

        elif action == "to_png":
            out_path = os.path.join(temp_dir, f"{base_name}.png")
            with Image.open(src_path) as im:
                im.save(out_path, "PNG")

        elif action == "to_pdf":
            out_path = os.path.join(temp_dir, f"{base_name}.pdf")
            with Image.open(src_path) as im:
                im.convert("RGB").save(out_path, "PDF", resolution=100.0)

        elif action == "to_sticker":
            out_path = os.path.join(temp_dir, f"{base_name}_sticker.webp")
            with Image.open(src_path) as im:
                im.thumbnail((512, 512), Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
                offset = ((512 - im.width) // 2, (512 - im.height) // 2)
                canvas.paste(im, offset)
                canvas.save(out_path, "WEBP")

        # ==================== PDF & TEXT CONVERSIONS ====================
        elif action == "pdf_to_images":
            doc = fitz.open(src_path)
            for page_idx in range(len(doc)):
                page = doc.load_page(page_idx)
                pix = page.get_pixmap(dpi=150)
                img_path = os.path.join(temp_dir, f"{base_name}_page_{page_idx+1}.png")
                pix.save(img_path)
                multiple_files.append(img_path)
            doc.close()

        elif action == "pdf_to_txt":
            out_path = os.path.join(temp_dir, f"{base_name}_extracted.txt")
            doc = fitz.open(src_path)
            all_text = []
            for page_idx in range(len(doc)):
                page = doc.load_page(page_idx)
                all_text.append(f"--- PAGE {page_idx+1} ---\n" + page.get_text())
            doc.close()
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(all_text))

        elif action == "txt_to_pdf":
            out_path = os.path.join(temp_dir, f"{base_name}.pdf")
            with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
                txt_content = f.read()

            doc = fitz.open()
            page = doc.new_page()
            # Simple text insertion into PDF
            page.insert_text((50, 72), txt_content[:4000], fontsize=11)
            doc.save(out_path)
            doc.close()

        # ==================== UPLOAD RESULTS ====================
        if multiple_files:
            await event.edit(f"📤 **Uploading `{len(multiple_files)}` converted page images...**")
            for idx, img_p in enumerate(multiple_files[:10]):
                await event.client.send_file(
                    chat_id,
                    file=img_p,
                    caption=f"📄 Page {idx+1} of `{filename}`",
                    reply_to=getattr(event, "topic_id", None),
                )
            if len(multiple_files) > 10:
                await event.reply(f"ℹ️ Uploaded first 10 of **{len(multiple_files)} pages**.")
            await event.delete()
            return

        if not out_path or not os.path.exists(out_path):
            await event.edit("❌ **Conversion failed:** output file was not produced.")
            return

        out_size = os.path.getsize(out_path)
        out_name = os.path.basename(out_path)
        await event.edit(f"📤 **Uploading `{out_name}` ({format_bytes(out_size)})...**")

        last_up_time = [0.0]

        async def upload_progress(current, total):
            now = time.time()
            if now - last_up_time[0] >= 3.5:
                last_up_time[0] = now
                pct = int(current / total * 100) if total else 0
                await event.edit(f"📤 **Uploading:** `{pct}%` (`{format_bytes(current)}` / `{format_bytes(total)}`)...")

        await event.client.send_file(
            chat_id,
            file=out_path,
            caption=f"✅ **Converted:** `{filename}` ➔ `{out_name}`\n💾 `{format_bytes(out_size)}`",
            attributes=extra_attrs if extra_attrs else None,
            voice_note=is_voice,
            video_note=is_video_note,
            progress_callback=upload_progress,
            reply_to=getattr(event, "topic_id", None),
        )
        await event.delete()

    except Exception as e:
        logger.exception("Conversion failed: %s", e)
        await event.edit(f"❌ **Conversion Error:** `{str(e)[:300]}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        CONV_SESSIONS.pop(token, None)
