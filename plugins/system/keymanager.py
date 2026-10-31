"""
Ultroid-Neo Plugin: Dynamic In-Chat API Key & Model Manager
Commands:
  .addkey <provider> <key>  - Dynamically adds a new API key without server restart
  .delkey <provider> <id>   - Deletes an API key by ID
  .keys / .keycheck         - Tests latency and health of all keys in the pool
  .setmodel <model_name>    - Changes the default active text model
  .models                   - Fetches live supported models from Groq API
"""

from __future__ import annotations

import logging
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db
from core.ai_pool import ai_pool

logger = logging.getLogger("ultroid_neo.plugin.keymanager")


@client_cmd(
    pattern="addkey",
    description="Add a new API key to the rotation pool (.addkey groq <KEY>)",
    module="system",
)
async def add_key_cmd(event: SafeMessageWrapper, args: str = ""):
    parts = args.split(maxsplit=1) if args else []
    if len(parts) < 2:
        await event.edit(
            "❌ **Usage:** `.addkey <provider> <api_key>`\n"
            "Example: `.addkey groq gsk_123456789abcdef`"
        )
        return

    provider, api_key = parts[0].lower(), parts[1].strip()
    await event.edit(f"⏳ Validating and saving `{provider}` key...")

    success, msg = await db.add_api_key(provider, api_key)
    if not success:
        await event.edit(f"⚠️ {msg}")
        return

    count = await ai_pool.reload_keys()
    await event.edit(
        f"✅ **Key Added Successfully!**\n\n"
        f"• **Provider:** `{provider.upper()}`\n"
        f"• **Preview:** `...{api_key[-6:]}`\n"
        f"• **Active Pool Size:** `{count} keys in rotation`"
    )


@client_cmd(
    pattern="delkey",
    description="Remove an API key from the pool (.delkey groq <ID>)",
    module="system",
)
async def del_key_cmd(event: SafeMessageWrapper, args: str = ""):
    parts = args.split(maxsplit=1) if args else []
    if len(parts) < 2:
        await event.edit(
            "❌ **Usage:** `.delkey <provider> <key_id>`\n"
            "Use `.keys` to view IDs."
        )
        return

    provider, key_id_str = parts[0].lower(), parts[1].strip()
    try:
        key_id = int(key_id_str)
    except ValueError:
        await event.edit("❌ Key ID must be a number.")
        return

    deleted = await db.delete_api_key(provider, key_id)
    if deleted:
        count = await ai_pool.reload_keys()
        await event.edit(
            f"🗑️ Key ID `{key_id}` removed.\n"
            f"• **Remaining in Pool:** `{count} keys`"
        )
    else:
        await event.edit(f"❌ No key found with ID `{key_id}` for provider `{provider}`.")


@client_cmd(
    pattern=r"keys|keycheck|key",
    description="Benchmark and display health of all configured AI keys (.keys / .key)",
    module="system",
)
async def check_keys_cmd(event: SafeMessageWrapper):
    await event.edit("🔄 **Pinging and health-checking AI key pool...**")
    await ai_pool.reload_keys()

    if ai_pool.key_count == 0:
        await event.edit(
            "⚠️ **No AI keys found in the pool.**\n"
            "Use `.addkey groq <YOUR_KEY>` to add one."
        )
        return

    current_model = await ai_pool.get_current_model()
    results = await ai_pool.benchmark_all_keys()
    lines = [
        "🛡️ **Dynamic AI Multi-Key Pool Status**",
        f"🧠 **Active Model:** `{current_model}`",
        "──────────────────────────────",
    ]

    for item in results:
        status_icon = "🟢" if "Online" in item["status"] else "🔴"
        lines.append(
            f"{status_icon} **Key #{item['id']}** (`{item['preview']}`)\n"
            f"   • Provider: `{item['provider'].upper()}`\n"
            f"   • Latency: `{item['latency_ms']}ms`\n"
            f"   • Requests Served: `{item.get('requests', 0)}`\n"
            f"   • Status: `{item['status']}`"
        )

    lines.append("──────────────────────────────")
    lines.append(f"📊 **Total Active Keys:** `{len(results)}`")
    lines.append("💡 *Requests automatically load-balance across all online keys.*")

    await event.edit("\n".join(lines))


@client_cmd(
    pattern="setmodel",
    description="Set default AI text model (.setmodel <model_name>)",
    module="system",
)
async def set_model_cmd(event: SafeMessageWrapper, model_arg: str = ""):
    model_name = model_arg.strip()
    if not model_name:
        current = await ai_pool.get_current_model()
        await event.edit(
            f"🧠 **Current AI Model:** `{current}`\n\n"
            f"• Use `.setmodel <model_name>` to change.\n"
            f"• Use `.models` to view available models."
        )
        return

    await ai_pool.set_current_model(model_name)
    await event.edit(f"✅ **Active AI Model Updated:** `{model_name}`")


@client_cmd(
    pattern=r"setv2t|setwhisper",
    description="Set default Whisper speech recognition model (.setv2t <model_name>)",
    module="system",
)
async def set_whisper_cmd(event: SafeMessageWrapper, model_arg: str = ""):
    arg = model_arg.strip().lower()
    if not arg:
        current = await ai_pool.get_whisper_model()
        await event.edit(
            f"🎙️ **Current Speech Model:** `{current}`\n\n"
            f"**Supported Models:**\n"
            f"• `whisper-large-v3-turbo` *(Ultra-fast, lowest latency)*\n"
            f"• `whisper-large-v3` *(Maximum accuracy & noisy environments)*\n\n"
            f"**Usage:** `.setv2t <model_name>` or `.setv2t turbo`"
        )
        return

    # User-friendly short aliases
    if arg in ("turbo", "fast", "v3-turbo"):
        target_model = "whisper-large-v3-turbo"
    elif arg in ("v3", "large", "accurate"):
        target_model = "whisper-large-v3"
    else:
        target_model = model_arg.strip()

    await ai_pool.set_whisper_model(target_model)
    await event.edit(f"✅ **Active Whisper Speech Model Updated:** `{target_model}`")


@client_cmd(
    pattern="models",
    description="Fetch live list of available models from Groq API",
    module="system",
)
async def list_models_cmd(event: SafeMessageWrapper):
    await event.edit("🔍 **Fetching live model lineup from Groq API...**")
    current = await ai_pool.get_current_model()
    current_whisper = await ai_pool.get_whisper_model()
    models = await ai_pool.list_available_models()

    chat_models = []
    audio_models = []
    tts_models = []
    guard_models = []

    for m in models:
        m_lower = m.lower()
        if "whisper" in m_lower:
            audio_models.append(m)
        elif "orpheus" in m_lower or "tts" in m_lower:
            tts_models.append(m)
        elif "guard" in m_lower or "safeguard" in m_lower:
            guard_models.append(m)
        else:
            chat_models.append(m)

    lines = [
        "🤖 **Available Groq AI Models (Live Lineup)**",
        f"🎯 **Active Chat Model:** `{current}`",
        f"🎙️ **Active Speech Model:** `{current_whisper}`",
        "──────────────────────────────",
        "🧠 **Text & Multimodal (Chat/Vision):**",
    ]

    for m in chat_models:
        marker = "👉 " if m == current else "• "
        extra = " *(Multimodal Vision)*" if "qwen" in m.lower() else (" *(120B Reasoning)*" if "120b" in m.lower() else "")
        lines.append(f"{marker}`{m}`{extra}")

    if audio_models:
        lines.append(f"\n🎙️ **Speech-to-Text (.v2t):**")
        for m in audio_models:
            marker = "👉 " if m == current_whisper else "• "
            lines.append(f"{marker}`{m}`")

    if tts_models:
        lines.append("\n🔊 **Audio & TTS (Canopy Labs):**")
        for m in tts_models:
            lines.append(f"• `{m}`")

    lines.append("──────────────────────────────")
    lines.append("💡 *Switch chat model:* `.setmodel <model_name>`")
    lines.append("💡 *Switch speech model:* `.setv2t <model_name>`")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "...\n*(truncated)*"
    await event.edit(text)
