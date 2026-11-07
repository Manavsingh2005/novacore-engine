"""
NovaCore: Bot Entry Point & Help System (/start, /help)
Provides interactive greeting, command catalog, and feature guides with inline buttons.
"""

from __future__ import annotations

from telethon import Button
from core.dispatcher import bot_cmd, bot_callback, SafeMessageWrapper


def get_start_buttons():
    return [
        [
            Button.inline("📥 Media Downloader", data="help:downloader"),
            Button.inline("🗜️ Archive Studio", data="help:archiver"),
        ],
        [
            Button.inline("🔄 File Converter", data="help:converter"),
            Button.inline("✨ Power & AI Tools", data="help:powertools"),
        ],
        [
            Button.inline("ℹ️ Bot Info & Specs", data="help:info"),
        ],
    ]


@bot_cmd(pattern=r"start", description="Start the bot and view feature catalog", module="system")
async def start_cmd(event: SafeMessageWrapper, query: str = ""):
    sender = await event.get_sender()
    name = getattr(sender, "first_name", "there") or "there"

    text = (
        f"👋 **Hello {name}! Welcome to Manav's Utility Bot** 🚀\n\n"
        "Your all-in-one, 24/7 cloud toolbox powered by high-speed AWS MTProto streaming (up to 2GB files) and Groq LPU AI.\n\n"
        "✨ **Core Features:**\n"
        "• 📥 **Media Downloader:** YouTube (1080p/720p/MP3), Instagram, TikTok, Twitter/X, Reddit & Pinterest (Zero commands needed).\n"
        "• 🗜️ **Archive Studio:** Zip, Unzip, and extract `.zip`, `.rar`, `.7z`, `.tar` with full AES password support.\n"
        "• 🔄 **File Converter:** Convert Video, Audio, Images, GIFs & Documents in one tap.\n"
        "• 📱 **QR Studio & CDN Host:** Custom QR codes, QR scanner & instant direct CDN links.\n"
        "• 🧠 **Groq AI Article Reader:** Ad-free blog/article reader with executive AI summaries.\n"
        "• 🎛️ **Audio FX & Karaoke:** Nightcore, Slowed+Reverb, Audio Speed & Vocal/Instrumental split.\n\n"
        "👇 **Tap a category below to explore commands and quick tutorials:**"
    )
    await event.reply(text, buttons=get_start_buttons())


@bot_cmd(pattern=r"help(?:\s+(.*))?", description="Detailed guide for all utility bot features", module="system")
async def help_cmd(event: SafeMessageWrapper, query: str = ""):
    sub = (query or "").strip().lower()

    if sub in ["dl", "downloader", "media", "youtube"]:
        await show_downloader_help(event)
    elif sub in ["zip", "unzip", "archive", "rar", "7z"]:
        await show_archiver_help(event)
    elif sub in ["convert", "converter", "format"]:
        await show_converter_help(event)
    elif sub in ["tools", "power", "ai", "qr", "speed", "karaoke", "read"]:
        await show_powertools_help(event)
    else:
        text = (
            "📖 **Manav's Utility Bot — Help Center**\n\n"
            "Select a module below to view detailed command usage and options:"
        )
        await event.reply(text, buttons=get_start_buttons())


async def show_downloader_help(event: SafeMessageWrapper):
    text = (
        "📥 **Media & Song Downloader Guide**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• `/dl <url>` — Downloads video from 1000+ sites.\n"
        "  ↳ **YouTube:** Shows interactive buttons (1080p, 720p, 480p, 360p, MP3).\n"
        "  ↳ **Instagram / TikTok / Twitter / Reddit / Pinterest:** Downloads highest quality instantly.\n"
        "• `/dl 720 <url>` — Direct 720p HD download.\n"
        "• `/dl 1080 <url>` — Direct 1080p Full HD download.\n"
        "• `/dl best <url>` — Direct highest resolution download.\n"
        "• `/dl audio <url>` — Direct MP3 320kbps extraction.\n"
        "• `/song <title or artist>` — Search music with 5 top picks & download 320kbps MP3 with album artwork.\n"
        "• `/cookies` — Manage YouTube protected cloud cookies vault.\n\n"
        "💡 *Tip: In private chat, you can just paste any video link directly!*"
    )
    buttons = [[Button.inline("« Back to Main Menu", data="help:main")]]
    await event.edit(text, buttons=buttons)


async def show_archiver_help(event: SafeMessageWrapper):
    text = (
        "🗜️ **Archive Studio (Zip / Unzip) Guide**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• `/unzip [password]` — Reply to any `.zip`, `.rar`, `.7z`, or `.tar.gz` to extract all files.\n"
        "  ↳ Supports AES-256 passwords and password-protected archives.\n"
        "  ↳ Path-traversal protected sandbox.\n"
        "• `/zip [password]` — Reply to any file or photo to compress it into a `.zip`.\n"
        "• `/zip new [archive_name]` — Start a batch archive session.\n"
        "  ↳ Send multiple files, then send `/zip done [password]` to bundle them all into one password-protected zip!\n"
        "• `/ziplist` — Reply to any archive to view its file list and size without extracting.\n\n"
        "💡 *Example:* `/unzip mysecret123` or `/zip password=mypassword`"
    )
    buttons = [[Button.inline("« Back to Main Menu", data="help:main")]]
    await event.edit(text, buttons=buttons)


async def show_converter_help(event: SafeMessageWrapper):
    text = (
        "🔄 **Universal File Converter Guide**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• `/convert` — Reply to any media or file to open the dynamic conversion menu!\n\n"
        "🎬 **Videos:**\n"
        "  ↳ Convert to MP4 (Streamable), MP3 Audio, Animated GIF, Voice Note, or Round Video Note.\n"
        "🎵 **Audio:**\n"
        "  ↳ Convert to MP3 (320kbps), WAV, AAC/M4A, FLAC, or Telegram Voice Note with waveform.\n"
        "🖼️ **Images:**\n"
        "  ↳ Convert between JPG, PNG, WebP Sticker (512x512), and PDF Document.\n"
        "📄 **PDF & Docs:**\n"
        "  ↳ Extract all PDF pages as PNG images, or convert text files into clean PDF documents.\n\n"
        "💡 *Just reply `/convert` to any file to see available options!*"
    )
    buttons = [[Button.inline("« Back to Main Menu", data="help:main")]]
    await event.edit(text, buttons=buttons)


async def show_powertools_help(event: SafeMessageWrapper):
    text = (
        "✨ **Power & AI Tools Guide**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📱 **QR Code Studio:**\n"
        "• `/qr <text/url>` — Generate sleek high-res QR code (themes: `dark`, `purple`, `gold`, `cyan`)\n"
        "• `/readqr` — Reply to any photo or screenshot to decode QR data (WiFi, links, text)\n\n"
        "🌐 **Instant CDN Host:**\n"
        "• `/upload` or `/tgm` — Reply to any image, GIF, or document (up to 200MB) for permanent direct CDN links\n\n"
        "📖 **Groq AI Article Reader:**\n"
        "• `/read <url>` — Clean distraction-free article reader with Groq LPU executive summary & bullet points\n\n"
        "🎛️ **Audio Speed & FX:**\n"
        "• `/speed [rate]` — Change speed (0.5x–2.0x) or reply `/speed` for presets\n"
        "• `/nightcore` — High-energy Nightcore pitch + tempo boost\n"
        "• `/slowed` — Dreamy Slowed + Reverb aesthetic\n\n"
        "🎤 **Vocal & Instrumental Separator:**\n"
        "• `/split` — Separate any song into Karaoke Instrumental and Acapella Vocals\n"
        "• `/split lyrics` — Transcribe full lyrics via Groq Whisper AI\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    buttons = [[Button.inline("« Back to Main Menu", data="help:main")]]
    await event.edit(text, buttons=buttons)


async def show_info_help(event: SafeMessageWrapper):
    text = (
        "ℹ️ **Bot Specs & Infrastructure**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• **Bot:** @utilitybymnvbot\n"
        "• **Architecture:** Telethon MTProto Bot (Up to 2GB file streaming)\n"
        "• **Hosting:** AWS EC2 Cloud Daemon (24/7 Uptime)\n"
        "• **AI Engine:** Groq Elastic Multi-Key LPU Pool (250+ tokens/sec)\n"
        "• **Engines:** yt-dlp, FFmpeg, PyMuPDF, pyzipper, Pillow, qrcode, BeautifulSoup4\n"
        "• **Owner:** Manav\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    buttons = [[Button.inline("« Back to Main Menu", data="help:main")]]
    await event.edit(text, buttons=buttons)


@bot_callback(r"^help:(.+)$")
async def help_callback_handler(event: SafeMessageWrapper):
    data = event.data.decode("utf-8")
    category = data.split(":", 1)[1]

    if category == "downloader":
        await show_downloader_help(event)
    elif category == "archiver":
        await show_archiver_help(event)
    elif category == "converter":
        await show_converter_help(event)
    elif category == "powertools":
        await show_powertools_help(event)
    elif category == "info":
        await show_info_help(event)
    elif category == "main":
        text = (
            "📖 **Manav's Utility Bot — Feature Catalog**\n\n"
            "Select any module below to view commands and options:"
        )
        await event.edit(text, buttons=get_start_buttons())
