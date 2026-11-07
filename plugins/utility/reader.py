"""
NovaCore: Clean Web Article Reader & AI Summarizer (/read)
Powered by Groq High-Speed LPU Multi-Key Pool (Llama 3.3).
Extracts distraction-free article text, executive summaries, and key takeaways.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.request
from typing import Optional, Tuple

from bs4 import BeautifulSoup
from telethon import Button

from core.dispatcher import bot_cmd, SafeMessageWrapper
from core.ai_pool import ai_pool
from helpers.formatters import format_bytes

logger = logging.getLogger("novacore.plugin.utility.reader")


def extract_clean_article(url: str) -> Tuple[str, str]:
    """Fetches web page and extracts clean text without ads, scripts, or navigation."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    soup = BeautifulSoup(html, "html.parser")

    # Title extraction
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.find("h1"):
        title = soup.find("h1").get_text().strip()
    else:
        title = "Article"

    # Remove clutter elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg", "form", "iframe"]):
        tag.decompose()

    # Look for common article containers first
    article_tag = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(r"article|post-content|entry-content|story-body|content", re.I))
        or soup.body
    )

    if not article_tag:
        article_tag = soup

    # Extract clean paragraphs
    paragraphs = []
    for elem in article_tag.find_all(["p", "h1", "h2", "h3", "h4", "li"]):
        text = elem.get_text().strip()
        # Filter out short cookie notices, social share links, or disclaimers
        if len(text) > 30 and not any(w in text.lower() for w in ["cookie", "subscribe to", "all rights reserved", "terms of use"]):
            paragraphs.append(text)

    clean_content = "\n\n".join(paragraphs)
    return title, clean_content


@bot_cmd(
    pattern=r"(?:read|reader|article)(?:\s+(.*))?",
    description="Clean ad-free article reader and Groq AI summary (/read <url>)",
    module="utility",
)
async def read_cmd(event: SafeMessageWrapper, query: str = ""):
    raw_target = (query or "").strip()
    if not raw_target:
        reply = await event.get_reply_message()
        if reply and reply.text:
            # Extract first URL from reply text
            m = re.search(r"https?://[^\s]+", reply.text)
            if m:
                raw_target = m.group(0)

    if not raw_target or not raw_target.startswith(("http://", "https://")):
        await event.reply(
            "📖 **Clean Web Article Reader & AI Summarizer:**\n\n"
            "• `/read <url>` — Read blogs, news & documentation without ads, popups, or clutter\n"
            "• Generates an executive summary & key takeaways powered by Groq LPU (Llama 3.3)\n\n"
            "💡 *Tip: Reply `/read` to any message containing an article link!*"
        )
        return

    target_url = raw_target.strip().rstrip(".,;!>")
    status_msg = await event.reply(f"🔎 **Extracting clean reader text from:** `{target_url[:60]}`...")
    loop = asyncio.get_running_loop()

    try:
        title, clean_text = await loop.run_in_executor(None, extract_clean_article, target_url)
        if not clean_text or len(clean_text) < 100:
            await status_msg.edit(
                "❌ **Could not extract article text.** The site may be protected by a strict paywall, anti-bot captcha, or rendered via client-side JavaScript."
            )
            return

        words = len(clean_text.split())
        est_read_min = max(1, round(words / 220))

        await status_msg.edit(f"🧠 **Synthesizing insights with Groq AI Pool...**\n⏱️ *Article length: {words} words (~{est_read_min}m read)*")

        # Prepare prompt for Groq Llama 3.3
        sample_text = clean_text[:9000]  # Feed generous sample to Groq
        system_prompt = (
            "You are a professional research reader. Analyze the given article text and output a structured, "
            "crisp briefing for Telegram in clean GitHub-style markdown.\n\n"
            "Structure:\n"
            "1. 📌 **Executive Summary**: Exactly 2-3 concise, impactful sentences.\n"
            "2. 🔑 **Key Takeaways**: 3-5 high-signal bullet points.\n"
            "3. 📖 **Quick Digest**: A brief 2-paragraph overview highlighting the most important facts."
        )

        user_content = f"Title: {title}\nURL: {target_url}\n\nArticle Content:\n{sample_text}"

        ai_response = await ai_pool.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=900,
        )

        final_text = (
            f"📰 **{title}**\n"
            f"🔗 [Original Source]({target_url}) • ⏱️ `{est_read_min} min read`\n\n"
            f"{ai_response}\n\n"
            "⚡ *Powered by Groq High-Speed LPU*"
        )

        buttons = [[Button.url("🌐 Open Original Webpage", url=target_url)]]

        # If article is substantial, offer clean reader file
        temp_dir = None
        if len(clean_text) > 2500:
            temp_dir = tempfile.mkdtemp(prefix="novacore_reader_")
            safe_name = re.sub(r'[\/:*?"<>|]', '_', title)[:45].strip() or "article"
            doc_path = os.path.join(temp_dir, f"{safe_name}.txt")
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write(f"TITLE: {title}\nSOURCE: {target_url}\nDATE: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n" + "=" * 60 + "\n\n")
                f.write(clean_text)

            await event.client.send_file(
                event.chat_id,
                file=doc_path,
                caption=final_text[:1024],
                buttons=buttons,
                reply_to=getattr(event, "topic_id", None),
            )
            await status_msg.delete()
        else:
            await status_msg.edit(final_text, buttons=buttons, link_preview=False)

    except Exception as e:
        logger.exception("Article reader failed: %s", e)
        await status_msg.edit(f"❌ **Reader Error:** `{e}`")
    finally:
        if "temp_dir" in locals() and temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
