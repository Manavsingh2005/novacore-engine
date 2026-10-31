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

    from helpers.twilio_caller import is_twilio_ready
    twilio_ready, _ = await is_twilio_ready()
    alarm_type_str = "Neural Voice Note & Real Phone Call (Twilio) 📞" if twilio_ready else "Neural Voice Note & Audible Ring"

    await event.edit(
        f"⏰ **Voice Call Alarm Scheduled (ID: `#{rem_id}`)**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 **Task:** `{task_text}`\n"
        f"⏱️ **Rings at:** `{formatted_target}` *(in {countdown})*\n"
        f"🎙️ **Alarm Type:** `{alarm_type_str}`\n"
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
    voice_script = f"Attention Manav! This is your scheduled alarm reminder for: {task_text}. Please check this task right now."
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

    # 4. Outbound Twilio Real GSM Voice Call
    try:
        from helpers.twilio_caller import is_twilio_ready, place_phone_call
        is_ready, _ = await is_twilio_ready()
        if is_ready:
            call_res = await place_phone_call(task_text)
            if call_res.get("success"):
                to_masked = call_res.get("to", "")
                if len(to_masked) > 7:
                    to_masked = to_masked[:3] + "****" + to_masked[-4:]
                await client.send_message(
                    chat_id,
                    f"📞 **Real Phone Call Dispatched:** Calling `{to_masked}`!\n"
                    f"• **Call SID:** `{call_res.get('call_sid')}`\n"
                    f"• **Status:** `{call_res.get('status')}`\n"
                    f"> 🔔 *Answer your mobile phone to hear your reminder!*"
                )
            else:
                await client.send_message(
                    chat_id,
                    f"⚠️ **Twilio Phone Call Notice:** {call_res.get('error')}"
                )
    except Exception as ex:
        logger.error("Error executing Twilio phone call: %s", ex)


@client_cmd(
    pattern=r"twilio",
    description="Manage Twilio Voice Calling for Phone Alarms (.twilio [status|on|off|config])",
    module="utility",
)
async def twilio_cmd(event: SafeMessageWrapper, query: str = ""):
    from helpers.twilio_caller import get_twilio_credentials, is_twilio_ready

    parts = query.strip().split()
    subcmd = parts[0].lower() if parts else "status"

    if subcmd in ("status", "info"):
        creds = await get_twilio_credentials()
        ready, status_msg = await is_twilio_ready()

        def mask(s: str) -> str:
            if not s:
                return "❌ *Not Configured*"
            if len(s) <= 8:
                return "••••••••"
            return f"`{s[:4]}••••{s[-4:]}`"

        status_icon = "🟢 **Ready to Call**" if ready else f"🟡 **Needs Setup** ({status_msg})"
        enabled_str = "✅ **Enabled**" if creds["enabled"] else "⛔ **Disabled**"

        await event.edit(
            f"📞 **NovaCore Twilio Voice Calling System**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Engine Status:** {status_icon}\n"
            f"• **Calling Active:** {enabled_str}\n"
            f"• **Account SID:** {mask(creds['account_sid'])}\n"
            f"• **Auth Token:** {mask(creds['auth_token'])}\n"
            f"• **Twilio Number:** `{creds['from_number'] or 'Not Set'}`\n"
            f"• **Your Verified Mobile:** `{creds['to_number'] or 'Not Set'}`\n"
            f"• **Polly AI Voice:** `{creds['voice']}`\n• **TwiML Bin:** `{creds.get('bin_url') or 'Default Voice Demo'}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📖 **Commands:**\n"
            f"• `.testcall` — Place an instant test call to your phone!\n"
            f"• `.setphone <+country_code_number>` — Set your phone number\n"
            f"• `.twilio on` / `.twilio off` — Toggle calling on alarms\n"
            f"• `.twilio config <SID> <TOKEN> <TWILIO_NUM> <MY_NUM>` — Save keys\n"
            f"> 💡 *Or configure securely at http://54.206.81.79:7860/*"
        )
        return

    elif subcmd == "on":
        await db.set_setting("twilio_call_enabled", "true")
        await event.edit("✅ **Twilio phone calls are now ENABLED for all scheduled reminders!** 📞")
        return

    elif subcmd == "off":
        await db.set_setting("twilio_call_enabled", "false")
        await event.edit("⛔ **Twilio phone calls DISABLED.** (Reminders will use Telegram audio notes only).")
        return

    elif subcmd in ("bin", "twiml"):
        if len(parts) < 2:
            curr_bin = await db.get_setting("twilio_bin_url", "")
            await event.edit(
                f"🔗 **TwiML Bin URL:** `{curr_bin or 'Not Set (using Twilio Demo Voice)'}`\n\n"
                f"To set your TwiML Bin URL:\n"
                f"`.twilio bin https://handler.twilio.com/twiml/EH...`\n\n"
                f"> 💡 *Create one in Twilio Console under Developer Tools -> TwiML Bins!*"
            )
            return
        raw_url = parts[1].strip()
        m = re.search(r"https://[^\s)\]\"'>]+", raw_url)
        bin_url = m.group(0).rstrip(".,;") if m else raw_url
        await db.set_setting("twilio_bin_url", bin_url)
        await event.edit(f"✅ **TwiML Bin URL Saved!**\n`{bin_url}`\nYour custom reminder text will now be spoken on calls! 📞")
        return

    elif subcmd == "config":
        # Format: .twilio config <sid> <token> <twilio_from> <user_to>
        if len(parts) < 5:
            await event.edit(
                "❌ **Usage:** `.twilio config <ACCOUNT_SID> <AUTH_TOKEN> <TWILIO_NUMBER> <YOUR_PHONE>`\n\n"
                "*(Example: `.twilio config ACxxxxxx 9f8xxxxxx +1205xxxxxxx +9198xxxxxxxx`)*\n"
                "🔒 *The message will be automatically deleted immediately for your security.*"
            )
            return

        sid = parts[1].strip()
        token = parts[2].strip()
        from_num = parts[3].strip()
        to_num = parts[4].strip()

        await db.set_setting("twilio_account_sid", sid)
        await db.set_setting("twilio_auth_token", token)
        await db.set_setting("twilio_from_number", from_num)
        await db.set_setting("twilio_to_number", to_num)
        await db.set_setting("twilio_call_enabled", "true")

        # Delete raw command message for security
        try:
            await event.delete()
        except Exception:
            pass

        await event.respond(
            "🔒 ✅ **Twilio Voice Calling Successfully Configured!**\n\n"
            f"• Outbound Caller: `{from_num}`\n"
            f"• Destination Phone: `{to_num}`\n"
            f"• Raw credentials stored securely in SQLite database.\n\n"
            f"👉 Send `.testcall` to place an immediate test call to your mobile!"
        )
        return

    else:
        await event.edit("❌ Unknown subcommand. Use `.twilio status`, `.twilio config`, `.twilio on`, or `.twilio off`.")


@client_cmd(
    pattern=r"setphone",
    description="Set your verified destination phone number for Twilio alarms (.setphone <number>)",
    module="utility",
)
async def setphone_cmd(event: SafeMessageWrapper, query: str = ""):
    num = query.strip()
    if not num:
        curr = await db.get_setting("twilio_to_number", "Not Set")
        await event.edit(f"📞 **Current Destination Phone:** `{curr}`\n\nTo update: `.setphone +919876543210`")
        return

    if not num.startswith("+"):
        await event.edit("❌ Phone number must be in E.164 international format starting with `+` (e.g. `+919876543210`).")
        return

    await db.set_setting("twilio_to_number", num)
    await event.edit(f"✅ **Destination Phone Number Updated to `{num}`!**\nUse `.testcall` to verify.")


@client_cmd(
    pattern=r"testcall",
    description="Trigger an instant test phone call to verify Twilio calling (.testcall [message])",
    module="utility",
)
async def testcall_cmd(event: SafeMessageWrapper, query: str = ""):
    from helpers.twilio_caller import is_twilio_ready, place_phone_call

    ready, reason = await is_twilio_ready()
    if not ready:
        await event.edit(
            f"❌ **Twilio Calling Not Ready:** {reason}\n\n"
            f"Configure your Twilio details using:\n"
            f"`.twilio config <ACCOUNT_SID> <AUTH_TOKEN> <TWILIO_NUMBER> <YOUR_PHONE>`\n"
            f"or via the web dashboard at http://54.206.81.79:7860/"
        )
        return

    test_msg = query.strip() or "This is a live test of your NovaCore voice calling alarm system. Everything is functioning normally!"

    await event.edit("📞 **Initiating real GSM phone call via Twilio...**")
    res = await place_phone_call(test_msg)

    if res.get("success"):
        await event.edit(
            f"🎉 📞 **Phone Call Dispatched Successfully!**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Calling:** `{res.get('to')}`\n"
            f"• **Call SID:** `{res.get('call_sid')}`\n"
            f"• **Status:** `{res.get('status')}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔔 **Your phone should be ringing right now!**\n"
            f"> *Pick up to hear Amazon Polly speak your test message.*"
        )
    else:
        err = res.get("error", "Unknown error")
        code = res.get("code", "")
        hint = ""
        if code == 21608:
            hint = "\n💡 *Trial Account Note: You must verify your destination phone number in the Twilio Console (Phone Numbers -> Manage -> Verified Caller IDs).*"
        elif code == 21211:
            hint = "\n💡 *Invalid phone number format. Ensure number is in international E.164 format (e.g. +919876543210).*"

        await event.edit(
            f"❌ **Failed to Place Phone Call**\n"
            f"• **Error:** `{err}` (Code: `{code}`)\n"
            f"{hint}"
        )

