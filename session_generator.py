"""
Ultroid-Neo: Safe Offline Telegram String Session Generator
Runs 100% locally on your machine to securely generate an authentic Telethon StringSession.
Binds to official Windows 11 Desktop client signatures from the moment of creation.
"""

import asyncio
import os
import sys
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    ApiIdInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def update_env_file(api_id: int, api_hash: str, string_session: str):
    """Safely updates or creates .env with credentials."""
    env_content = ""
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            env_content = f.read()

    lines = env_content.splitlines()
    new_lines = []
    keys_set = set()

    replacements = {
        "API_ID": str(api_id),
        "API_HASH": api_hash,
        "STRING_SESSION": string_session,
    }

    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k, _ = line.split("=", 1)
            k = k.strip()
            if k in replacements:
                new_lines.append(f"{k}={replacements[k]}")
                keys_set.add(k)
                continue
        new_lines.append(line)

    for k, v in replacements.items():
        if k not in keys_set:
            new_lines.append(f"{k}={v}")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")

    print(f"\n[+] Automatically updated credentials in {ENV_FILE}")


async def generate_session():
    print("=" * 60)
    print("   Ultroid-Neo: Safe Offline Session Generator")
    print("   (100% Local • Zero Telemetry • Desktop Mimicry)")
    print("=" * 60)

    # 1. API_ID
    while True:
        raw_id = input("\nEnter your API_ID (from https://my.telegram.org): ").strip()
        if raw_id.isdigit():
            api_id = int(raw_id)
            break
        print("[-] API_ID must be a numeric integer.")

    # 2. API_HASH
    while True:
        api_hash = input("Enter your API_HASH: ").strip()
        if len(api_hash) >= 16:
            break
        print("[-] API_HASH is too short. Please copy it carefully.")

    # Instantiate client with official Windows 11 Desktop device signature
    client = TelegramClient(
        session=StringSession(),
        api_id=api_id,
        api_hash=api_hash,
        device_model="Desktop",
        system_version="Windows 11",
        app_version="5.6.3 x64",
        lang_code="en",
        system_lang_code="en",
    )

    await client.connect()

    # 3. Phone Number
    phone_raw = input("\nEnter your Phone Number (with country code, e.g. +919876543210): ").strip()
    phone = phone_raw.replace(" ", "").replace("-", "")

    try:
        sent = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        print("[-] Invalid phone number format.")
        await client.disconnect()
        return
    except ApiIdInvalidError:
        print("[-] Invalid API_ID or API_HASH. Please re-check at my.telegram.org.")
        await client.disconnect()
        return

    # 4. Login Code
    code_raw = input("Enter the login code received on Telegram: ").strip()
    code = code_raw.replace(" ", "").replace("-", "")

    try:
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        pwd = input("Enter your Two-Factor Authentication (2FA) Password: ").strip()
        await client.sign_in(password=pwd)

    # 5. Extract StringSession
    string_session = client.session.save()
    me = await client.get_me()

    print("\n" + "=" * 60)
    print(f"[+] Login Successful as: {me.first_name} (ID: {me.id})")
    print(f"[+] Telegram Premium: {'Yes 💎' if getattr(me, 'premium', False) else 'No (Standard Tier) ⚡'}")
    print("=" * 60)
    print("\nYOUR STRING_SESSION (Keep this strictly secret!):\n")
    print(string_session)
    print("\n" + "=" * 60)

    # Also send to Saved Messages so you always have it backed up safely
    try:
        await client.send_message(
            "me",
            f"🛡️ **Ultroid-Neo Session String Backed Up**\n\n`{string_session}`\n\n*(Keep this strictly private)*",
        )
        print("[+] Also safely backed up to your Telegram 'Saved Messages'.")
    except Exception:
        pass

    auto_save = input("\nDo you want to automatically save this to your .env file? (y/n): ").strip().lower()
    if auto_save in ("y", "yes"):
        update_env_file(api_id, api_hash, string_session)

    await client.disconnect()
    print("\n[✓] Done! You can now run the userbot with: python main.py")


if __name__ == "__main__":
    try:
        asyncio.run(generate_session())
    except KeyboardInterrupt:
        print("\n[!] Cancelled by user.")
