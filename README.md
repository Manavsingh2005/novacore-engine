---
title: NovaCore Microservice
emoji: ⚡
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# 🤖 NovaCore (2026 Edition)

Next-generation, high-performance, AI-native Telegram automation engine engineered for modern Telegram features (**Topics/Forums**, **Subscription Neutrality**, and **4GB/2GB Media Streaming**).

Built with Python 3.12+, Telethon, `uv`, `aiosqlite`, and powered by a dynamic **Groq Multi-Key AI Pool**.

---

## 🛡️ The Guardian Anti-Ban Shield (Account Safety #1)
NovaCore is specifically hardened to protect primary personal accounts:
* **Official Client Mimicry:** Connects with authentic Windows 11 Desktop client signatures.
* **Human Micro-Jitter:** Randomizes pacing (+150ms to +450ms) across all outbound events to avoid mechanical bot detection.
* **Emergency Circuit Breaker:** Instantly aborts any action if Telegram issues a FloodWait $\ge 60$s and alerts your Saved Messages.
* **Anti-Report Inbox-Guard:** Uses polite, human-assistant greetings with a strict **1-message-per-24h rate cap per stranger**, completely preventing annoying spam reports.

---

## ⚡ Subscription Neutrality (First-Class Free Tier)
* **Zero Broken Formatting:** Dynamic detection via `me.premium` ensures non-Premium accounts receive clean, high-contrast Unicode formatting with zero raw entity IDs.
* **Free AI Voice-to-Text (`.v2t`):** Transcribes voice notes and video notes for free using Groq Whisper-large-v3, bringing a paid Telegram Premium feature to everyone for $0.
* **Dynamic File Streaming:** Auto-splits files exceeding 2GB into 1.99GB parts for Free accounts; supports full single-file 4GB transfers for Premium accounts.

---

## 🧠 Groq Dynamic Multi-Key Pool
* **Elastic Key Capacity ($N \ge 1$):** Add as many Groq keys as you want; each key linearly increases your rate-limit quota.
* **Zero-Downtime Management:** Use `.addkey groq <KEY>` directly from your Telegram Saved Messages without ever restarting the bot.
* **Automatic 429 Failover:** Seamlessly rotates to the next available key if one hits a rate limit.

---

## 📖 Command Reference

| Command | Syntax | Description |
| :--- | :--- | :--- |
| **Ping** | `.ping` | Check response latency and server ping. |
| **Alive** | `.alive` | Display bot status, uptime, active keys, and tier. |
| **Sysinfo** | `.sysinfo` | Real-time RAM, CPU, and process footprint monitor. |
| **Help** | `.help [module]` | Dynamic command reference and usage tips. |
| **Ask AI** | `.ask <prompt>` | Query Groq Llama 3.3 (250+ tokens/sec) with long-term memory. |
| **Remember**| `.remember <fact>` | Store personal facts and preferences into AI memory. |
| **Memories**| `.memories` | View all active memories stored in AI brain. |
| **Forget** | `.forget <id>` | Delete a memory entry from AI brain. |
| **Remind** | `.remind <time> <task>` | Set voice call alarm & high-priority reminder alert. |
| **Alarms** | `.alarms` | List upcoming scheduled voice reminders. |
| **Del Remind**| `.delremind <id>` | Cancel a scheduled reminder. |
| **Summarize**| `.sum [count]` | Summarize recent messages in current chat or topic. |
| **Voice-to-Text** | `.v2t` (reply) | Transcribe audio/voice note with Groq Whisper. |
| **Rewrite** | `.rewrite [style]` | Polish draft into professional, concise, or casual tone. |
| **Add Key** | `.addkey groq <key>` | Add new AI key to rotation pool on the fly. |
| **Check Keys**| `.keys` | Benchmark latency and health of all keys in pool. |
| **Approve** | `.approve` (or `.a`) | Whitelist user to message you in PM. |
| **Downloader**| `.dl <url>` | Download media via yt-dlp with auto-split. |
| **Sticker** | `.sticker [emoji]` | Convert photo/sticker into 512x512 Telegram sticker. |
| **AFK** | `.afk [reason]` | Away mode with mention logger & auto-reply. |
| **Purge** | `.purge [count]` | Rapid, safe chunked bulk message deletion. |
| **Save Note** | `.save <name> <text>`| Save text snippet or note into local database. |
| **Get Note** | `.get <name>` | Retrieve a saved note. |
| **Notes** | `.notes` | List all saved notes. |

---

## 🌐 24/7 Forever-Free Hosting Guide

NovaCore contains a built-in micro-web health server (`core/web_server.py`) that binds to `$PORT` (default 7860).

### Method 1: Hugging Face Spaces (100% Free Docker Cloud)
1. Go to [Hugging Face Spaces](https://huggingface.co/spaces) and click **Create new Space**.
2. Select SDK: **Docker** (Blank).
3. Connect your GitHub repository `novacore-engine` or push directly to the Space.
4. Under Space **Settings -> Variables and secrets**, add your credentials:
   - `API_ID`
   - `API_HASH`
   - `STRING_SESSION`
   - `GROQ_API_KEYS`
   - `GUARDIAN_SHIELD=true`
   - `PORT=7860`
5. **Result:** Deploys instantly with 2 vCPUs and 16 GB RAM for **$0**.

### Method 2: Docker Compose (VPS / Local)
```bash
docker compose up -d --build
```
