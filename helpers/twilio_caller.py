"""
NovaCore: Twilio Autonomous Voice Calling Engine
Places real GSM phone calls to the user's mobile phone via Twilio REST API (async aiohttp).
Uses Amazon Polly neural voices with inline TwiML so no external webhooks are needed.
"""

from __future__ import annotations

import base64
import html
import logging
from typing import Any, Dict, Optional, Tuple
import aiohttp

from core.config import settings
from core.database import db

logger = logging.getLogger("novacore.twilio_caller")


async def get_twilio_credentials() -> Dict[str, str]:
    """
    Retrieves Twilio credentials from SQLite database settings or fallback to environment variables.
    """
    account_sid = await db.get_setting("twilio_account_sid", "")
    auth_token = await db.get_setting("twilio_auth_token", "")
    from_number = await db.get_setting("twilio_from_number", "")
    to_number = await db.get_setting("twilio_to_number", "")
    enabled = await db.get_setting("twilio_call_enabled", "true")
    voice = await db.get_setting("twilio_voice", "Polly.Aditi")

    # Fallback to env if not set in DB
    if not account_sid and hasattr(settings, "TWILIO_ACCOUNT_SID"):
        account_sid = getattr(settings, "TWILIO_ACCOUNT_SID") or ""
    if not auth_token and hasattr(settings, "TWILIO_AUTH_TOKEN"):
        auth_token = getattr(settings, "TWILIO_AUTH_TOKEN") or ""
    if not from_number and hasattr(settings, "TWILIO_PHONE_NUMBER"):
        from_number = getattr(settings, "TWILIO_PHONE_NUMBER") or ""
    if not to_number and hasattr(settings, "USER_PHONE_NUMBER"):
        to_number = getattr(settings, "USER_PHONE_NUMBER") or ""

    return {
        "account_sid": account_sid.strip(),
        "auth_token": auth_token.strip(),
        "from_number": from_number.strip(),
        "to_number": to_number.strip(),
        "enabled": enabled.strip().lower() in ("true", "1", "yes", "on"),
        "voice": voice.strip() or "Polly.Aditi",
    }


async def is_twilio_ready() -> Tuple[bool, str]:
    """
    Checks if all required Twilio settings are configured.
    Returns (is_ready, status_message).
    """
    creds = await get_twilio_credentials()
    if not creds["account_sid"]:
        return False, "Missing Twilio Account SID"
    if not creds["auth_token"]:
        return False, "Missing Twilio Auth Token"
    if not creds["from_number"]:
        return False, "Missing Twilio Outbound Phone Number"
    if not creds["to_number"]:
        return False, "Missing Destination Phone Number (your mobile number)"
    if not creds["enabled"]:
        return False, "Twilio Voice Calling is currently disabled in settings"
    return True, "Ready"


def build_twiml(task_text: str, voice: str = "Polly.Aditi") -> str:
    """
    Generates inline TwiML XML instructions for Amazon Polly neural voice.
    Escapes text to avoid XML injection.
    """
    escaped_task = html.escape(task_text.strip())
    # Choose appropriate language code for Polly voices
    lang = "en-IN" if "Aditi" in voice or "Kajal" in voice or "Raveena" in voice else "en-US"

    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Response>\n'
        f'    <Pause length="1"/>\n'
        f'    <Say voice="{voice}" language="{lang}">Attention Trade! This is your NovaCore alarm reminder.</Say>\n'
        f'    <Pause length="1"/>\n'
        f'    <Say voice="{voice}" language="{lang}">{escaped_task}</Say>\n'
        f'    <Pause length="2"/>\n'
        f'    <Say voice="{voice}" language="{lang}">Repeating your reminder: {escaped_task}. Goodbye!</Say>\n'
        f'    <Hangup/>\n'
        f'</Response>'
    )


async def place_phone_call(task_text: str, custom_to: Optional[str] = None) -> Dict[str, Any]:
    """
    Initiates an outbound phone call via Twilio REST API.
    Returns dictionary with result status, call SID, or error message.
    """
    creds = await get_twilio_credentials()
    ready, reason = await is_twilio_ready()

    to_number = (custom_to or creds["to_number"]).strip()
    if not ready and not custom_to:
        logger.warning("Cannot place phone call: %s", reason)
        return {"success": False, "error": reason}

    if not creds["account_sid"] or not creds["auth_token"] or not creds["from_number"] or not to_number:
        return {"success": False, "error": "Incomplete Twilio credentials"}

    endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{creds['account_sid']}/Calls.json"
    twiml_body = build_twiml(task_text, creds["voice"])

    payload = {
        "To": to_number,
        "From": creds["from_number"],
        "Twiml": twiml_body,
    }

    auth_str = f"{creds['account_sid']}:{creds['auth_token']}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, data=payload, headers=headers) as resp:
                resp_json = await resp.json()
                if resp.status in (200, 201):
                    call_sid = resp_json.get("sid", "Unknown")
                    call_status = resp_json.get("status", "queued")
                    logger.info("Twilio voice call dispatched successfully! SID: %s, To: %s", call_sid, to_number)
                    return {
                        "success": True,
                        "call_sid": call_sid,
                        "status": call_status,
                        "to": to_number,
                        "from": creds["from_number"],
                    }
                else:
                    err_code = resp_json.get("code", resp.status)
                    err_msg = resp_json.get("message", "Unknown Twilio API error")
                    logger.error("Twilio call failed: [%s] %s", err_code, err_msg)
                    return {
                        "success": False,
                        "code": err_code,
                        "error": err_msg,
                        "status": resp.status,
                    }
    except Exception as e:
        logger.exception("Twilio call exception: %s", e)
        return {"success": False, "error": str(e)}
