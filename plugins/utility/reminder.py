"""
NovaCore Plugin: Scheduled Reminders & Neural Voice Alarms (.remind, .alarms, .delremind, .tz)
Features:
- .remind <time> <task>: Schedules reminders with neural voice alarms.
  - Supports clock times: '12:38 am', '12:38am', '8:30 pm', '9pm', '14:30', 'at 8:00 pm'.
  - Supports relative times: 'in 10m', '10m', '45s', '2h', '1d'.
  - Strips am/pm/at cleanly from task description.
  - Evaluates clock times in user's local timezone (defaults to Asia/Kolkata / IST).
- .tz [timezone]: Check or set your local timezone (e.g. .tz Asia/Kolkata, .tz UTC).
- .reminders / .alarms: List all pending scheduled alarms with local countdown.
- .delremind <id | all>: Cancel a reminder or clear all pending alarms.
- High-priority ringing visual alert banner pinned with notify=True.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import edge_tts
from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db

logger = logging.getLogger("novacore.plugin.utility.reminder")

DEFAULT_TIMEZONE = "Asia/Kolkata"


async def get_user_timezone() -> ZoneInfo:
    """Retrieves user's configured timezone from DB or defaults to Asia/Kolkata."""
    tz_str = await db.get_setting("timezone", DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(tz_str)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def parse_reminder_input(raw: str, user_tz: ZoneInfo) -> Tuple[Optional[float], str, str]:
    """
    Parses natural user reminder query into:
    (due_timestamp, clean_task_text, formatted_target_time_string)
    """
    text = raw.strip()
    if not text:
        return None, "", ""

    now_utc = datetime.now(timezone.utc)
    now_user = now_utc.astimezone(user_tz)
    now_epoch = now_utc.timestamp()

    # 1. Check relative format: "in 10m", "10m", "in 45s", "2h", "1d"
    rel_match = re.match(
        r"^(?:in\s+)?(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\b\s*(.*)$",
        text,
        re.IGNORECASE,
    )
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        task = rel_match.group(3).strip()

        multiplier = 60
        if unit.startswith("s"):
            multiplier = 1
        elif unit.startswith("m"):
            multiplier = 60
        elif unit.startswith("h"):
            multiplier = 3600
        elif unit.startswith("d"):
            multiplier = 86400

        due_time = now_epoch + (val * multiplier)
        target_dt = datetime.fromtimestamp(due_time, tz=user_tz)
        formatted_target = target_dt.strftime("%I:%M:%S %p (%d %b)")
        return due_time, task, formatted_target

    # 2. Check clock time format: "12:38 am", "12:38am", "8:30 pm", "at 9pm", "14:30"
    clock_match = re.match(
        r"^(?:(?:at|on|for)\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b\s*(.*)$",
        text,
        re.IGNORECASE,
    )
    if clock_match:
        hour_str = clock_match.group(1)
        min_str = clock_match.group(2)
        meridiem = (clock_match.group(3) or "").lower()
        task = clock_match.group(4).strip()

        # If there's neither a colon nor am/pm (e.g. "10 apples"), it's not a clock time
        if min_str is None and not meridiem:
            return None, text, ""

        hour = int(hour_str)
        minute = int(min_str) if min_str else 0

        if 0 <= hour <= 23 and 0 <= minute <= 59:
            # 12-hour clock standard conversion
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0

            target_dt = now_user.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target_dt <= now_user:
                # Time already passed today, schedule for tomorrow
                target_dt += timedelta(days=1)

            due_time = target_dt.timestamp()
            formatted_target = target_dt.strftime("%I:%M %p (%d %b)")
            return due_time, task, formatted_target

    return None, text, ""


@client_cmd(
    pattern=r"remind|alarm",
    description="Set a scheduled reminder with neural voice alarm (.remind <time> <task>)",
    module="utility",
)
async def remind_cmd(event: SafeMessageWrapper, query: str = ""):
    raw_query = query.strip()
    if not raw_query:
        await event.edit(
            "❌ **Usage:** `.remind <time> <task>`\n\n"
            "• **Clock:** `.remind 12:45 am Test`\n"
            "• **Clock:** `.remind 08:30 PM Dinner time`\n"
            "• **Relative:** `.remind in 10m Take your medicine`\n"
            "• **Relative:** `.remind 45s Check oven`\n"
            "• **Relative:** `.remind 2h Call client`\n\n"
            "💡 *When time arrives, NovaCore rings your Telegram with a neural voice alarm!*"
        )
        return

    user_tz = await get_user_timezone()
    due_time, task_text, formatted_target = parse_reminder_input(raw_query, user_tz)

    if not due_time or due_time <= time.time():
        await event.edit(
            "❌ **Invalid Time Format.**\n\n"
            "• Clock time: `12:45 am`, `8:30 pm`, `14:30`\n"
            "• Relative time: `in 10m`, `45s`, `2h`, `1d`\n\n"
            "*(Example: `.remind 12:45 am Check server`)*"
        )
        return

    if not task_text:
        task_text = "Scheduled Reminder"

    rem_id = await db.add_reminder(
        chat_id=event.chat_id,
        reminder_text=task_text,
        due_timestamp=due_time,
        voice_enabled=True,
    )

    remaining_sec = max(int(due_time - time.time()), 0)
    m, s = divmod(remaining_sec, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)

    countdown_parts = []
    if d > 0:
        countdown_parts.append(f"{d}d")
    if h > 0:
        countdown_parts.append(f"{h}h")
    if m > 0:
        countdown_parts.append(f"{m}m")
    countdown_parts.append(f"{s}s")
    countdown = " ".join(countdown_parts)

    await event.edit(
        f"⏰ **Voice Call Alarm Scheduled (ID: `#{rem_id}`)**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 **Task:** `{task_text}`\n"
        f"⏱️ **Rings at:** `{formatted_target}` *(in {countdown})*\n"
        f"🎙️ **Alarm Type:** `Neural Voice Note & Audible Ring`\n"
        f"🌐 **Timezone:** `{user_tz.key}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"> 💡 *NovaCore will ring and deliver your voice reminder when time arrives!*"
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
            "*(e.g. `.remind in 15m Drink water` or `.remind 12:45 am Test`)*"
        )
        return

    user_tz = await get_user_timezone()
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
        dt_target = datetime.fromtimestamp(r["due_timestamp"], tz=user_tz)
        target_str = dt_target.strftime("%I:%M %p")
        lines.append(f"• **[#{r['id']}]** `{r['reminder_text']}` — 🕒 `{target_str}` *(in {countdown})*")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("> 💡 *Cancel one: `.delremind <id>` | Cancel all: `.delremind all`*")
    await event.edit("\n".join(lines))


@client_cmd(
    pattern=r"delremind|cancelalarm",
    description="Delete a scheduled reminder (.delremind <id | all>)",
    module="utility",
)
async def del_reminder_cmd(event: SafeMessageWrapper, query: str = ""):
    arg = query.strip().lower()
    if not arg:
        await event.edit("❌ **Usage:** `.delremind <id>` or `.delremind all`\nUse `.reminders` to see active IDs.")
        return

    if arg in ["all", "clear"]:
        cleared_count = await db.clear_all_reminders()
        await event.edit(f"✅ **Cleared all active reminders ({cleared_count} removed).**")
        return

    if not arg.isdigit():
        await event.edit("❌ **Invalid ID.** Use numeric ID (e.g. `.delremind 1`) or `.delremind all`.")
        return

    success = await db.delete_reminder(int(arg))
    if success:
        await event.edit(f"✅ **Alarm `#{arg}` cancelled.**")
    else:
        await event.edit(f"⚠️ Reminder `#{arg}` not found.")


@client_cmd(
    pattern=r"tz|timezone",
    description="View or set your local timezone for reminders (.tz [timezone_name])",
    module="utility",
)
async def timezone_cmd(event: SafeMessageWrapper, query: str = ""):
    arg = query.strip()
    if not arg:
        user_tz = await get_user_timezone()
        now_local = datetime.now(timezone.utc).astimezone(user_tz)
        await event.edit(
            f"🌐 **Current Timezone:** `{user_tz.key}`\n"
            f"🕒 **Current Local Time:** `{now_local.strftime('%I:%M:%S %p (%d %b %Y)')}`\n\n"
            f"💡 *To change timezone, use `.tz <name>`*\n"
            f"*(Examples: `.tz Asia/Kolkata`, `.tz UTC`, `.tz America/New_York`)*"
        )
        return

    try:
        new_tz = ZoneInfo(arg)
        await db.set_setting("timezone", new_tz.key)
        now_local = datetime.now(timezone.utc).astimezone(new_tz)
        await event.edit(
            f"✅ **Timezone Updated to `{new_tz.key}`!**\n\n"
            f"🕒 **Current Local Time:** `{now_local.strftime('%I:%M:%S %p (%d %b %Y)')}`\n"
            f"All future clock reminders will follow this timezone."
        )
    except Exception as e:
        await event.edit(
            f"❌ **Invalid Timezone:** `{arg}`\n"
            f"Use a standard IANA timezone name like `Asia/Kolkata`, `UTC`, `America/New_York`, etc."
        )


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
