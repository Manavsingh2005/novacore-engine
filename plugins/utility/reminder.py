"""
NovaCore Plugin: Scheduled Reminders & Neural Voice Alarms (.remind, .alarms, .delremind)
Schedules high-priority reminders and triggers realistic neural voice alarms
sent as official Telegram Voice Notes with audible notification rings.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
import edge_tts
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db

logger = logging.getLogger("novacore.plugin.utility.reminder")


def parse_time_offset(time_str: str) -> Optional[float]:
    """
    Parses relative strings ('in 10m', '2h', '30s') or absolute clock times ('18:30', '8:30 PM')
    into a future epoch timestamp.
    """
    now = time.time()
    s = time_str.strip().lower()

    if s.startswith("in "):
        s = s[3:].strip()

    # Relative matching: 10s, 5m, 2h, 1d
    rel_match = re.match(r"^(\d+)\s*(s|sec|m|min|h|hr|d|day)s?$", s)
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2)
        multiplier = {
            "s": 1, "sec": 1,
            "m": 60, "min": 60,
            "h": 3600, "hr": 3600,
            "d": 86400, "day": 86400,
        }.get(unit, 60)
        return now + (val * multiplier)

    # Absolute clock time matching: 14:30 or 2:30 pm
    clock_match = re.match(r"^(\d{1,2}):(\d{2})(?:\s*(am|pm))?$", s)
    if clock_match:
        hour = int(clock_match.group(1))
        minute = int(clock_match.group(2))
        meridiem = clock_match.group(3)

        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0

        dt_now = datetime.now()
        dt_target = dt_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt_target.timestamp() <= now:
            dt_target += timedelta(days=1)
        return dt_target.timestamp()

    return None


@client_cmd(
    pattern=r"remind|alarm",
    description="Set a scheduled reminder with high-priority neural voice alarm (.remind <time> <task>)",
    module="utility",
)
async def remind_cmd(event: SafeMessageWrapper, query: str = ""):
    parts = query.strip().split(maxsplit=2)
    if not parts or len(parts) < 2:
        await event.edit(
            "❌ **Usage:** `.remind <time> <task>`\n\n"
            "• **Relative:** `.remind in 10m Take your medicine`\n"
            "• **Relative:** `.remind 2h Call client`\n"
            "• **Relative:** `.remind 45s Check oven`\n"
            "• **Clock:** `.remind 08:30 PM Dinner time`\n\n"
            "💡 *When time arrives, NovaCore rings your Telegram with an ultra-realistic voice alarm!*"
        )
        return

    # Handle 'in 10m' multi-word time or single word
    if parts[0].lower() == "in" and len(parts) >= 3:
        time_token = f"in {parts[1]}"
        task_text = parts[2]
    else:
        time_token = parts[0]
        task_text = " ".join(parts[1:])

    due_time = parse_time_offset(time_token)
    if not due_time or due_time <= time.time():
        await event.edit(
            "❌ **Invalid Time Format.**\n"
            "Use relative time (e.g. `in 10m`, `2h`, `45s`) or clock time (`08:30 PM`)."
        )
        return

    rem_id = await db.add_reminder(
        chat_id=event.chat_id,
        reminder_text=task_text,
        due_timestamp=due_time,
        voice_enabled=True,
    )

    remaining_sec = max(int(due_time - time.time()), 0)
    m, s = divmod(remaining_sec, 60)
    h, m = divmod(m, 60)
    countdown = f"{h}h {m}m {s}s" if h > 0 else (f"{m}m {s}s" if m > 0 else f"{s}s")

    await event.edit(
        f"⏰ **Voice Call Alarm Scheduled (ID: `#{rem_id}`)**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 **Task:** `{task_text}`\n"
        f"⏱️ **Rings in:** `{countdown}`\n"
        f"🎙️ **Alarm Type:** `Neural Voice Note & Audible Ring`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"> 💡 *NovaCore will ping you with a voice note when time arrives!*"
    )


@client_cmd(
    pattern=r"reminders|alarms",
    description="List all pending alarms and reminders (.reminders)",
    module="utility",
)
async def list_reminders_cmd(event: SafeMessageWrapper):
    reminders = await db.get_active_reminders()
    if not reminders:
        await event.edit(
            "⏰ **No Active Reminders.**\n\n"
            "Set one using `.remind <time> <task>`\n"
            "*(e.g. `.remind in 15m Drink water`)*"
        )
        return

    now = time.time()
    lines = [
        f"⏰ **Active Scheduled Reminders ({len(reminders)})**",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for r in reminders:
        rem_sec = max(int(r["due_timestamp"] - now), 0)
        m, s = divmod(rem_sec, 60)
        h, m = divmod(m, 60)
        countdown = f"{h}h {m}m" if h > 0 else (f"{m}m {s}s" if m > 0 else f"{s}s")
        lines.append(f"• **[#{r['id']}]** `{r['reminder_text']}` — ⏳ *in {countdown}*")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("> 💡 *Cancel a reminder using: `.delremind <id>`*")
    await event.edit("\n".join(lines))


@client_cmd(
    pattern=r"delremind|cancelalarm",
    description="Delete a scheduled reminder (.delremind <id>)",
    module="utility",
)
async def del_reminder_cmd(event: SafeMessageWrapper, query: str = ""):
    arg = query.strip()
    if not arg or not arg.isdigit():
        await event.edit("❌ **Usage:** `.delremind <id>`\nUse `.reminders` to see active IDs.")
        return

    success = await db.delete_reminder(int(arg))
    if success:
        await event.edit(f"✅ **Alarm `#{arg}` cancelled.**")
    else:
        await event.edit(f"⚠️ Reminder `#{arg}` not found.")


async def execute_voice_alarm(client, reminder: dict):
    """
    Synthesizes a realistic neural voice call reminder and sends it as an official
    Telegram Voice Note accompanied by high-priority ringing alert banners.
    """
    chat_id = reminder["chat_id"]
    task_text = reminder["reminder_text"]
    rem_id = reminder["id"]

    logger.info("Executing scheduled voice alarm #%d: '%s'", rem_id, task_text)

    # 1. Synthesize urgent voice call reminder using Edge-TTS
    voice_script = f"Attention Trade! This is your scheduled alarm reminder for: {task_text}. Please check this task right now."
    selected_voice = await db.get_setting("tts_voice", "en-US-ChristopherNeural")

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        communicate = edge_tts.Communicate(voice_script, selected_voice)
        await communicate.save(tmp_path)

        # 2. Send as Telegram Voice Note
        await client.send_file(
            chat_id,
            file=tmp_path,
            voice_note=True,
            caption=f"🎙️ **Voice Alarm:** _{task_text}_",
        )

        # 3. Send high-priority ringing visual alert
        alarm_card = (
            f"🚨 ━━━━ ⏰ **ALARM RINGING** ━━━━ 🚨\n"
            f"📢 **Scheduled Reminder:** `{task_text}`\n"
            f"⏱️ **Status:** `Triggered Right Now`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔔 **WAKE UP / ACTION REQUIRED!**\n"
            f"> 💡 *NovaCore Voice Alarm has been delivered to your audio messages.*"
        )
        msg = await client.send_message(chat_id, alarm_card)
        try:
            await msg.pin(notify=True)
        except Exception:
            pass

    except Exception as e:
        logger.exception("Failed synthesizing voice alarm: %s", e)
        # Fallback to text alert
        await client.send_message(
            chat_id,
            f"🚨 **REMINDER ALARM:** `{task_text}`\n*(Audio synthesis fallback)*",
        )
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
