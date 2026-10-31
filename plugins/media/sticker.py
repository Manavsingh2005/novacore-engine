"""
NovaCore Plugin: Sticker Kanger & Inspector (.kang, .stickerinfo)
Converts any image, photo, or replied sticker into an official Telegram sticker format (512x512 PNG).
Inspects sticker pack attributes and document metadata.
"""

from __future__ import annotations

import io
import logging
from PIL import Image
from telethon.tl.types import DocumentAttributeSticker, DocumentAttributeImageSize
from core.dispatcher import client_cmd, SafeMessageWrapper
from helpers.formatters import make_card, format_bytes

logger = logging.getLogger("novacore.plugin.media.kang")


def resize_image_for_sticker(image_bytes: bytes) -> io.BytesIO:
    """Resizes an image so that its longest side is exactly 512px, maintaining aspect ratio."""
    im = Image.open(io.BytesIO(image_bytes))
    im = im.convert("RGBA")

    width, height = im.size
    if width > height:
        new_width = 512
        new_height = int((512 / width) * height)
    else:
        new_height = 512
        new_width = int((512 / height) * width)

    im_resized = im.resize((new_width, new_height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    im_resized.save(output, format="PNG")
    output.seek(0)
    output.name = "sticker.png"
    return output


@client_cmd(
    pattern="kang",
    description="Format and convert replied image or sticker into Telegram sticker format (.kang [emoji])",
    module="media",
)
async def kang_cmd(event: SafeMessageWrapper, emoji_arg: str = ""):
    reply = await event.get_reply_message()
    if not reply or not (reply.photo or reply.sticker or reply.document):
        await event.edit("❌ **Usage:** Reply to a photo, sticker, or image file with `.kang`")
        return

    emoji = emoji_arg.strip() if emoji_arg else "✨"
    await event.edit("🎨 **Processing image for sticker format...**")

    # Download replied media to memory
    media_bytes = await event.client.download_media(reply, file=bytes)
    if not media_bytes:
        await event.edit("❌ Failed to download media.")
        return

    try:
        sticker_io = resize_image_for_sticker(media_bytes)
        await event.edit(f"📤 **Uploading formatted sticker ({emoji})...**")

        await event.client.send_file(
            event.chat_id,
            file=sticker_io,
            force_document=False,
            caption=f"✨ **Kanged Sticker** | Emoji: {emoji}",
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
