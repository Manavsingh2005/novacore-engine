"""
NovaCore Plugin: AI Vision, OCR & Visual Intelligence (.see, .ocr, .analyze)
Combines high-speed optical character recognition (OCR) and Groq Neural Reasoning.
Extracts text from images, solves handwritten math, analyzes receipts, diagrams,
and transcribes screenshots directly within Telegram.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import tempfile
import aiohttp
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

from core.dispatcher import client_cmd, SafeMessageWrapper
from core.ai_pool import ai_pool
from core.database import db

logger = logging.getLogger("novacore.plugin.ai.vision")


async def extract_text_via_ocr(image_bytes: bytes) -> str:
    """Extracts text from image bytes via local Tesseract or cloud OCR."""
    # 1. Try pytesseract if available
    try:
        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(image).strip()
        if text:
            return text
    except Exception as e:
        logger.debug("Local tesseract unavailable or failed: %s", e)

    # 2. Free OCR.space API fallback
    try:
        url = "https://api.ocr.space/parse/image"
        data = aiohttp.FormData()
        data.add_field("apikey", "helloworld")  # Public free demo key
        data.add_field("file", image_bytes, filename="image.jpg", content_type="image/jpeg")
        data.add_field("language", "eng")
        data.add_field("isOverlayRequired", "false")

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    parsed = res_json.get("ParsedResults", [])
                    if parsed:
                        out = parsed[0].get("ParsedText", "").strip()
                        if out:
                            return out
    except Exception as e:
        logger.debug("OCR.space fallback failed: %s", e)

    return ""


async def analyze_with_gemini(image_bytes: bytes, prompt: str, gemini_key: str) -> str:
    """Uses Gemini 1.5 Flash Vision for direct multimodal visual analysis."""
    import base64
    b64_img = base64.b64encode(image_bytes).decode("utf-8")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt or "Describe and analyze this image thoroughly. Highlight key visual details, text, and context."},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": b64_img
                        }
                    }
                ]
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 200:
                data = await resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
            err_text = await resp.text()
            raise RuntimeError(f"Gemini API returned {resp.status}: {err_text[:200]}")


@client_cmd(
    pattern="ocr",
    description="Extract all visible text from a replied photo or image document (.ocr)",
    module="ai",
)
async def ocr_cmd(event: SafeMessageWrapper):
    reply = await event.get_reply_message()
    if not reply or not (reply.photo or (reply.document and "image" in getattr(reply.document, "mime_type", ""))):
        await event.edit("❌ **Usage:** Reply to any photo or image document with `.ocr`.")
        return

    await event.edit("🔍 **Scanning image and extracting text via OCR...**")
    try:
        image_bytes = await reply.download_media(file=bytes)
        if not image_bytes:
            await event.edit("❌ Failed to download image.")
            return

        text = await extract_text_via_ocr(image_bytes)
        if not text:
            await event.edit("⚠️ **No readable text detected in this image.**")
            return

        card = (
            "📄 **Extracted OCR Text:**\n"
            "──────────────────────────────\n"
            f"```\n{text[:3500]}\n```\n"
            "──────────────────────────────\n"
            f"📊 *Total characters extracted: {len(text)}*"
        )
        await event.edit(card)
    except Exception as e:
        logger.exception("OCR extraction failed: %s", e)
        await event.edit(f"❌ **OCR Failed:** `{e}`")


@client_cmd(
    pattern=r"see|analyze",
    description="Analyze replied image, solve math, or answer questions (.see [question])",
    module="ai",
)
async def vision_see_cmd(event: SafeMessageWrapper, question: str = ""):
    reply = await event.get_reply_message()
    if not reply or not (reply.photo or (reply.document and "image" in getattr(reply.document, "mime_type", ""))):
        await event.edit("❌ **Usage:** Reply to an image or photo with `.see <optional question>`.")
        return

    query = question.strip() if question else "Explain and analyze the key information in this image in detail."
    await event.edit(f"👁️ **Analyzing image with NovaCore Vision...**\n`{query}`")

    try:
        image_bytes = await reply.download_media(file=bytes)
        if not image_bytes:
            await event.edit("❌ Could not download image.")
            return

        # Check if user has added a Gemini key for full native vision
        gemini_keys = await db.get_active_keys("gemini")
        if gemini_keys:
            try:
                g_key = gemini_keys[0]["key"]
                analysis = await analyze_with_gemini(image_bytes, query, g_key)
                card = (
                    f"👁️ **NovaCore Visual Intelligence**\n"
                    f"❓ *Query:* `{query}`\n"
                    "──────────────────────────────\n"
                    f"{analysis}\n"
                    "──────────────────────────────\n"
                    "⚡ *Multimodal Engine: Gemini 1.5 Flash Vision*"
                )
                await event.edit(card)
                return
            except Exception as e:
                logger.warning("Gemini vision failed, falling back to OCR + Groq: %s", e)

        # High-Speed OCR + Groq Neural Reasoning pipeline
        extracted_text = await extract_text_via_ocr(image_bytes)

        if not extracted_text:
            await event.edit(
                "⚠️ **No readable text detected in this image.**\n\n"
                "💡 *Tip:* To enable full scene, object, and landscape vision without text, add a free Gemini API key:\n"
                "`.addkey gemini <YOUR_KEY>`"
            )
            return

        system_prompt = (
            "You are NovaCore Visual Intelligence Assistant. "
            "You have been provided with the raw optical text extracted from an image provided by the user. "
            "Analyze the content carefully, answer any questions posed by the user, solve math problems step-by-step if present, "
            "or summarize the key takeaways cleanly."
        )

        user_content = (
            f"User Question / Instruction: {query}\n\n"
            f"--- EXTRACTED IMAGE CONTENT ---\n"
            f"{extracted_text}\n"
            f"-------------------------------"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        response = await ai_pool.chat_completion(
            messages=messages,
            temperature=0.4,
            max_tokens=2000,
        )

        card = (
            f"👁️ **NovaCore Visual Intelligence**\n"
            f"❓ *Query:* `{query}`\n"
            "──────────────────────────────\n"
            f"{response}\n"
            "──────────────────────────────\n"
            "⚡ *Processed via OCR & Groq Neural Engine*"
        )
        await event.edit(card)

    except Exception as e:
        logger.exception("Vision analysis error: %s", e)
        await event.edit(f"❌ **Vision Analysis Failed:** `{e}`")
