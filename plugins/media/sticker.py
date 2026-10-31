"""
NovaCore Plugin: Sticker Kanger & Inspector (.kang, .stickerinfo)
Converts any image, photo, animated (.tgs), or video (.webm) sticker into Telegram sticker format.
Supports static WebP/PNG, animated Lottie TGS, and video WebM.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile
from PIL import Image
from telethon.tl.types import (
    DocumentAttributeSticker,
    DocumentAttributeImageSize,
    InputStickerSetEmpty,
)
from core.dispatcher import client_cmd, SafeMessageWrapper
from helpers.formatters import make_card, format_bytes

logger = logging.getLogger("novacore.plugin.media.kang")


def resize_image_for_sticker(image_bytes: bytes) -> io.BytesIO:
    """Resizes an image so that its longest side is 512px, maintaining aspect ratio."""
    im = Image.open(io.BytesIO(image_bytes))
    im = im.convert("RGBA")

    width, height = im.size
    if width > height:
        new_width = 512
        new_height = max(1, int((512 / width) * height))
    else:
        new_height = 512
        new_width = max(1, int((512 / height) * width))

    im_resized = im.resize((new_width, new_height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    im_resized.save(output, format="WEBP")
    output.seek(0)
    output.name = "sticker.webp"
    return output


@client_cmd(
    pattern="kang",
    description="Format and convert replied media/sticker into a Telegram sticker (.kang [emoji])",
    module="media",
)
async def kang_cmd(event: SafeMessageWrapper, emoji_arg: str = ""):
    reply = await event.get_reply_message()
    if not reply or not (reply.photo or reply.sticker or reply.document):
        await event.edit("❌ **Usage:** Reply to a photo, sticker, or image file with `.kang`")
        return

    emoji = emoji_arg.strip() if emoji_arg else "✨"
    await event.edit("🎨 **Processing sticker media...**")

    # Detect mime and sticker attributes
    mime = ""
    is_sticker = bool(reply.sticker)
    if reply.document:
        mime = getattr(reply.document, "mime_type", "")
        if any(isinstance(a, DocumentAttributeSticker) for a in getattr(reply.document, "attributes", [])):
            is_sticker = True

    try:
        # Case 1: Animated Sticker (.tgs)
        if mime == "application/x-tgsticker" or (is_sticker and mime.endswith("tgsticker")):
            media_bytes = await event.client.download_media(reply, file=bytes)
            if not media_bytes:
                await event.edit("❌ Failed to download animated sticker.")
                return

            sticker_io = io.BytesIO(media_bytes)
            sticker_io.name = "sticker.tgs"
            sticker_attr = DocumentAttributeSticker(alt=emoji, stickerset=InputStickerSetEmpty())

            await event.edit(f"📤 **Uploading animated sticker ({emoji})...**")
            await event.client.send_file(
                event.chat_id,
                file=sticker_io,
                force_document=False,
                attributes=[sticker_attr],
                reply_to=event.topic_id,
            )
            await event.delete()
            return

        # Case 2: Video Sticker (.webm)
        if is_sticker and ("webm" in mime or "video" in mime):
            media_bytes = await event.client.download_media(reply, file=bytes)
            if not media_bytes:
                await event.edit("❌ Failed to download video sticker.")
                return

            sticker_io = io.BytesIO(media_bytes)
            sticker_io.name = "sticker.webm"
            sticker_attr = DocumentAttributeSticker(alt=emoji, stickerset=InputStickerSetEmpty())

            await event.edit(f"📤 **Uploading video sticker ({emoji})...**")
            await event.client.send_file(
                event.chat_id,
                file=sticker_io,
                force_document=False,
                attributes=[sticker_attr],
                reply_to=event.topic_id,
            )
            await event.delete()
            return

        # Case 3: Regular Image, Photo, or Static WebP Sticker
        media_bytes = None
        try:
            media_bytes = await event.client.download_media(reply, file=bytes)
            sticker_io = resize_image_for_sticker(media_bytes)
        except Exception:
            # If standard image parsing failed (e.g. video/gif), try thumbnail first
            thumb_bytes = await event.client.download_media(reply, thumb=-1, file=bytes)
            if thumb_bytes:
                sticker_io = resize_image_for_sticker(thumb_bytes)
            else:
                # Try ffmpeg single frame extraction
                with tempfile.NamedTemporaryFile(suffix=".tmp", delete=False) as tf:
                    tf.write(media_bytes or b"")
                    tf_path = tf.name
                out_png = tf_path + ".png"
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", tf_path, "-vframes", "1", out_png],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True,
                    )
                    with open(out_png, "rb") as f:
                        extracted = f.read()
                    sticker_io = resize_image_for_sticker(extracted)
                finally:
                    if os.path.exists(tf_path):
                        try:
                            os.remove(tf_path)
                        except OSError:
                            pass
                    if os.path.exists(out_png):
                        try:
                            os.remove(out_png)
                        except OSError:
                            pass

        sticker_attr = DocumentAttributeSticker(alt=emoji, stickerset=InputStickerSetEmpty())
        await event.edit(f"📤 **Uploading formatted sticker ({emoji})...**")

        await event.client.send_file(
            event.chat_id,
            file=sticker_io,
            force_document=False,
            attributes=[sticker_attr],
            reply_to=event.topic_id,
        )
        await event.delete()

    except Exception as e:
        logger.exception("Kang failed: %s", e)
        await event.edit(f"❌ **Kang Failed:** `{e}`")


@client_cmd(
    pattern="stickerinfo",
    description="Inspect replied sticker's metadata, pack name, and document ID",
    module="media",
)
async def sticker_info_cmd(event: SafeMessageWrapper):
    reply = await event.get_reply_message()
    if not reply or not reply.sticker:
        await event.edit("❌ Reply to a sticker with `.stickerinfo`.")
        return

    sticker = reply.sticker
    doc_id = sticker.id
    size_str = format_bytes(sticker.size)
    mime = sticker.mime_type

    emoji = "None"
    pack_name = "Standalone"
    width, height = "512", "512"

    for attr in sticker.attributes:
        if isinstance(attr, DocumentAttributeSticker):
            emoji = attr.alt or "None"
            if attr.stickerset:
                pack_name = getattr(attr.stickerset, "short_name", "Pack")
        elif isinstance(attr, DocumentAttributeImageSize):
            width, height = str(attr.w), str(attr.h)

    pack_link = f"https://t.me/addstickers/{pack_name}" if pack_name != "Standalone" else "None"

    card = make_card(
        title="🎨 Sticker Metadata",
        fields=[
            ("Emoji", emoji),
            ("Pack", f"[{pack_name}]({pack_link})"),
            ("Document ID", str(doc_id)),
            ("Dimensions", f"{width}x{height} px"),
            ("File Size", size_str),
            ("MIME Type", mime),
        ],
        footer="Use .kang to convert media into stickers.",
    )
    await event.edit(card)
