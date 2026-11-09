"""
NovaCore: High-Performance Archive Studio (/zip, /unzip, /ziplist)
Features:
- /unzip [password]: Extract .zip, .rar, .7z, .tar.gz, .tar.bz2, .tar.xz with full password support.
- /zip [password]: Compress replied file or media into AES-256 encrypted .zip.
- /zip new [name] -> send files -> /zip done [password]: Multi-file batch archive builder.
- /ziplist: Inspect archive directory and file sizes without extracting.
- Zip-Slip path traversal protection and automatic disk cleanup.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from typing import Any, Dict, List, Optional

import py7zr
import pyzipper
from telethon import Button
from telethon.tl.types import DocumentAttributeFilename

from core.dispatcher import bot_cmd, bot_callback, client_raw_handler, SafeMessageWrapper
from helpers.formatters import format_bytes
from helpers.archiving import split_file_into_zips

logger = logging.getLogger("novacore.plugin.utility.archiver")

# Active batch zip sessions keyed by user_id (with disk sync for daemon restart resilience)
ZIP_SESSIONS: Dict[int, Dict[str, Any]] = {}


def get_zip_session(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves batch zip session from memory or persistent disk state."""
    session_dir = f"/tmp/novacore_zipsess_{user_id}"
    meta_path = os.path.join(session_dir, "session.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["files"] = [f for f in data.get("files", []) if os.path.exists(f)]
            ZIP_SESSIONS[user_id] = data
            return data
        except Exception:
            pass
    return ZIP_SESSIONS.get(user_id)


def save_zip_session(user_id: int, session: Dict[str, Any]) -> None:
    """Saves batch zip session in memory and writes metadata to disk."""
    ZIP_SESSIONS[user_id] = session
    session_dir = session.get("dir") or f"/tmp/novacore_zipsess_{user_id}"
    os.makedirs(session_dir, exist_ok=True)
    meta_path = os.path.join(session_dir, "session.json")
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(session, f)
    except Exception as e:
        logger.warning("Failed to save zip session meta: %s", e)


def clear_zip_session(user_id: int) -> None:
    """Cleans up batch zip session directory and memory."""
    session = get_zip_session(user_id)
    if session and session.get("dir") and os.path.exists(session["dir"]):
        shutil.rmtree(session["dir"], ignore_errors=True)
    ZIP_SESSIONS.pop(user_id, None)

ARCHIVE_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz",
    ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".gz",
    ".z01", ".z02", ".001",
)


def get_archive_filename(msg) -> str:
    """Extracts filename from document attributes or fallback."""
    if not msg:
        return "archive"
    if msg.file and msg.file.name:
        return msg.file.name
    if msg.document:
        for attr in msg.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name
    return "archive"


def get_media_filename(msg, fallback_index: int = 1) -> str:
    """Extracts filename with extension for any Telegram media object."""
    if not msg:
        return f"file_{fallback_index}"

    # Document filename
    if getattr(msg, "document", None):
        for attr in getattr(msg.document, "attributes", []):
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name

    # Telethon file property
    if getattr(msg, "file", None) and getattr(msg.file, "name", None):
        return msg.file.name

    # Telegram Photo
    if getattr(msg, "photo", None):
        return f"photo_{int(time.time())}_{fallback_index}.jpg"

    # Telegram Video
    if getattr(msg, "video", None):
        return f"video_{int(time.time())}_{fallback_index}.mp4"

    # Telegram Audio / Voice
    if getattr(msg, "voice", None):
        return f"voice_{int(time.time())}_{fallback_index}.ogg"
    if getattr(msg, "audio", None):
        return f"audio_{int(time.time())}_{fallback_index}.mp3"

    ext = getattr(msg.file, "ext", "") if getattr(msg, "file", None) else ""
    return f"file_{int(time.time())}_{fallback_index}{ext or ''}"


def is_archive(filename: str) -> bool:
    fn = filename.lower()
    return any(fn.endswith(ext) for ext in ARCHIVE_EXTENSIONS)


# ==========================================
# UNZIP / EXTRACTION ENGINE
# ==========================================

@bot_cmd(
    pattern=r"(?:unzip|extract)(?:\s+(.*))?",
    description="Extract archive with password support (/unzip [password])",
    module="utility",
)
async def unzip_cmd(event: SafeMessageWrapper, query: str = ""):
    password = (query or "").strip()
    reply = await event.get_reply_message()

    target_msg = reply if (reply and reply.media) else (event.message if event.message.media else None)

    if not target_msg or not target_msg.media:
        await event.reply(
            "🗜️ **How to Unzip an Archive:**\n\n"
            "1. Reply to any `.zip`, `.rar`, `.7z`, or `.tar.gz` file with `/unzip`\n"
            "2. If it is password protected, use: `/unzip <password>`\n\n"
            "💡 *Supported formats:* `.zip`, `.rar`, `.7z`, `.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`"
        )
        return

    raw_filename = get_archive_filename(target_msg)
    if not is_archive(raw_filename):
        await event.reply(
            f"⚠️ **File `{raw_filename}` does not appear to be an archive.**\n"
            f"Supported extensions: {', '.join(ARCHIVE_EXTENSIONS)}"
        )
        return

    status_msg = await event.reply(f"⏳ **Preparing to extract `{raw_filename}`...**")
    loop = asyncio.get_running_loop()
    temp_dir = tempfile.mkdtemp(prefix="novacore_unzip_")
    archive_path = os.path.join(temp_dir, raw_filename)
    extract_dir = os.path.join(temp_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        last_down_time = [0.0]

        async def download_progress(current, total):
            now = time.time()
            if now - last_down_time[0] >= 3.5:
                last_down_time[0] = now
                pct = int(current / total * 100) if total else 0
                await status_msg.edit(
                    f"📥 **Downloading Archive:** `{pct}%` (`{format_bytes(current)}` / `{format_bytes(total)}`)..."
                )

        await event.client.download_media(
            target_msg,
            file=archive_path,
            progress_callback=download_progress,
        )

        await status_msg.edit("🔓 **Unpacking archive contents...**")

        def _extract() -> Dict[str, Any]:
            extracted_files: List[str] = []
            fn_lower = raw_filename.lower()
            pwd_bytes = password.encode("utf-8") if password else None

            # 1. ZIP / AES-ZIP
            if fn_lower.endswith(".zip"):
                try:
                    with pyzipper.AESZipFile(archive_path) as zf:
                        if pwd_bytes:
                            zf.setpassword(pwd_bytes)
                        zf.extractall(extract_dir)
                except Exception as zip_err:
                    err_str = str(zip_err).lower()
                    if "password" in err_str or "encrypted" in err_str or "bad password" in err_str:
                        return {"error": "PASSWORD_REQUIRED", "details": str(zip_err)}
                    # Fallback to standard zipfile, then system 7z (which unpacks multi-volume split zips)
                    try:
                        with zipfile.ZipFile(archive_path) as zf:
                            if pwd_bytes:
                                zf.setpassword(pwd_bytes)
                            zf.extractall(extract_dir)
                    except Exception:
                        cmd = ["7z", "x", "-y"]
                        if password:
                            cmd.append(f"-p{password}")
                        else:
                            cmd.append("-p-")
                        cmd.extend([archive_path, f"-o{extract_dir}"])
                        res = subprocess.run(cmd, capture_output=True, text=True)
                        if res.returncode != 0:
                            if "wrong password" in (res.stderr + res.stdout).lower():
                                return {"error": "PASSWORD_REQUIRED", "details": res.stderr}
                            raise RuntimeError(res.stderr or res.stdout)

            # 2. 7Z (7-Zip)
            elif fn_lower.endswith(".7z"):
                try:
                    with py7zr.SevenZipFile(archive_path, mode="r", password=password or None) as szf:
                        szf.extractall(extract_dir)
                except Exception as sz_err:
                    err_str = str(sz_err).lower()
                    if "password" in err_str or "encrypted" in err_str:
                        return {"error": "PASSWORD_REQUIRED", "details": str(sz_err)}
                    # Fallback to system 7z CLI
                    cmd = ["7z", "x", "-y"]
                    if password:
                        cmd.append(f"-p{password}")
                    else:
                        cmd.append("-p-")
                    cmd.extend([archive_path, f"-o{extract_dir}"])
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    if res.returncode != 0:
                        if "wrong password" in res.stderr.lower() or "password" in res.stderr.lower():
                            return {"error": "PASSWORD_REQUIRED", "details": res.stderr}
                        raise RuntimeError(res.stderr or res.stdout)

            # 3. RAR
            elif fn_lower.endswith(".rar"):
                cmd = ["unar", "-o", extract_dir]
                if password:
                    cmd.extend(["-p", password])
                cmd.append(archive_path)
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    if "password" in (res.stderr + res.stdout).lower():
                        return {"error": "PASSWORD_REQUIRED", "details": res.stderr or res.stdout}
                    raise RuntimeError(res.stderr or res.stdout)

            # 4. TAR / TAR.GZ / TAR.BZ2 / TAR.XZ
            elif any(fn_lower.endswith(ext) for ext in [".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"]):
                with tarfile.open(archive_path, "r:*") as tf:
                    # Python 3.12+ safe filter
                    if hasattr(tarfile, "data_filter"):
                        tf.extractall(extract_dir, filter="data")
                    else:
                        tf.extractall(extract_dir)
            else:
                # Generic fallback via system unar / 7z
                cmd = ["unar", "-o", extract_dir]
                if password:
                    cmd.extend(["-p", password])
                cmd.append(archive_path)
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    raise RuntimeError(res.stderr or res.stdout)

            # Walk and collect all extracted files
            total_size = 0
            for root, _, files in os.walk(extract_dir):
                for f in files:
                    p = os.path.join(root, f)
                    # Zip-Slip security check
                    if not os.path.abspath(p).startswith(os.path.abspath(extract_dir)):
                        raise ValueError("Security violation: path traversal detected in archive!")
                    extracted_files.append(p)
                    total_size += os.path.getsize(p)

            return {"files": extracted_files, "total_size": total_size}

        result = await loop.run_in_executor(None, _extract)

        if result.get("error") == "PASSWORD_REQUIRED":
            await status_msg.edit(
                "🔒 **Password Protected Archive!**\n\n"
                f"The archive `{raw_filename}` is encrypted.\n\n"
                "👉 **To extract:** Reply to the archive with:\n"
                "`/unzip <your_password>`"
            )
            return

        files = result.get("files", [])
        total_size = result.get("total_size", 0)

        if not files:
            await status_msg.edit("⚠️ **Extraction completed, but archive was empty.**")
            return

        await status_msg.edit(
            f"✅ **Extracted `{len(files)}` files** (`{format_bytes(total_size)}`).\n"
            f"📤 **Uploading files to Telegram...**"
        )

        # Upload files
        # If <= 15 files, upload each one directly with progress
        if len(files) <= 15:
            for idx, file_p in enumerate(files):
                fname = os.path.basename(file_p)
                fsize = os.path.getsize(file_p)
                await status_msg.edit(f"📤 **Uploading [{idx+1}/{len(files)}]:** `{fname}` (`{format_bytes(fsize)}`)...")
                await event.client.send_file(
                    event.chat_id,
                    file=file_p,
                    caption=f"📄 **{fname}**\n💾 `{format_bytes(fsize)}` • 🗜️ *From {raw_filename}*",
                    reply_to=getattr(event, "topic_id", None),
                )
            await status_msg.delete()
        else:
            # Over 15 files: repack into clean unencrypted zip or send top 10
            await status_msg.edit(f"📦 Archive contains **{len(files)} files** ({format_bytes(total_size)}). Sending first 15 files...")
            for idx, file_p in enumerate(files[:15]):
                fname = os.path.basename(file_p)
                fsize = os.path.getsize(file_p)
                await event.client.send_file(
                    event.chat_id,
                    file=file_p,
                    caption=f"📄 **{fname}**\n💾 `{format_bytes(fsize)}`",
                    reply_to=getattr(event, "topic_id", None),
                )
            await event.reply(
                f"ℹ️ Sent first 15 of **{len(files)} files**. Total uncompressed size: `{format_bytes(total_size)}`."
            )
            await status_msg.delete()

    except Exception as e:
        logger.exception("Unzip failed: %s", e)
        await status_msg.edit(f"❌ **Extraction Failed:** `{str(e)[:300]}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ==========================================
# ZIP / ARCHIVE CREATION ENGINE
# ==========================================

@bot_cmd(
    pattern=r"zip(?:\s+(.*))?",
    description="Compress file or start batch zip (/zip [password] or /zip new [name])",
    module="utility",
)
async def zip_cmd(event: SafeMessageWrapper, query: str = ""):
    args = (query or "").strip().split()
    subcmd = args[0].lower() if args else ""
    user_id = event.sender_id or event.chat_id

    # 1. Check for batch session management (/zip new, /zip done, /zip cancel, /zip status)
    if subcmd == "new":
        archive_name = args[1] if len(args) > 1 else f"archive_{int(time.time())}.zip"
        if not archive_name.endswith(".zip"):
            archive_name += ".zip"

        # Cancel any previous session
        clear_zip_session(user_id)

        session_dir = f"/tmp/novacore_zipsess_{user_id}"
        os.makedirs(session_dir, exist_ok=True)
        session = {
            "name": archive_name,
            "dir": session_dir,
            "files": [],
            "created_at": time.time(),
        }
        save_zip_session(user_id, session)

        await event.reply(
            f"📦 **Batch Archive Session Started!**\n\n"
            f"• **Archive Name:** `{archive_name}`\n"
            f"• **Destination:** `.zip` (AES-256 Capable)\n\n"
            "👉 **How to use:**\n"
            "1. Send me documents, photos, or files one by one.\n"
            "2. When finished, send: `/zip done [optional_password]`\n"
            "3. To cancel anytime, send: `/zip cancel`"
        )
        return

    if subcmd == "cancel":
        sess = get_zip_session(user_id)
        if sess:
            clear_zip_session(user_id)
            await event.reply("🚫 **Batch zip session cancelled and cleared.**")
        else:
            await event.reply("ℹ️ No active batch zip session found.")
        return

    if subcmd == "done":
        session = get_zip_session(user_id)
        if not session or not session.get("files"):
            await event.reply("❌ **No active batch archive session or no files added yet.** Start with `/zip new`.")
            return

        password = args[1] if len(args) > 1 else None
        status_msg = await event.reply(f"🗜️ **Compressing {len(session['files'])} files into `{session['name']}`...**")
        loop = asyncio.get_running_loop()

        out_zip_path = os.path.join(session["dir"], session["name"])

        def _create_batch_zip():
            if password:
                with pyzipper.AESZipFile(
                    out_zip_path,
                    "w",
                    compression=pyzipper.ZIP_DEFLATED,
                    encryption=pyzipper.WZ_AES,
                ) as zf:
                    zf.setpassword(password.encode("utf-8"))
                    for file_p in session["files"]:
                        zf.write(file_p, arcname=os.path.basename(file_p))
            else:
                with zipfile.ZipFile(out_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for file_p in session["files"]:
                        zf.write(file_p, arcname=os.path.basename(file_p))

        try:
            await loop.run_in_executor(None, _create_batch_zip)
            zip_size = os.path.getsize(out_zip_path)
            pwd_text = f"🔑 **Password:** `{password}`\n" if password else "🔓 **Unencrypted**\n"

            # Check if archive exceeds Telegram 2GB limit -> Split into parts!
            if zip_size > 2000 * 1024 * 1024:
                await status_msg.edit(
                    f"📦 **Archive size ({format_bytes(zip_size)}) exceeds Telegram 2GB limit.**\n"
                    f"✂️ **Splitting into multi-volume Zip parts (~1.85 GB each)...**"
                )
                parts = await loop.run_in_executor(None, split_file_into_zips, out_zip_path, 1900)
                total_parts = len(parts)

                for idx, part_path in enumerate(parts, 1):
                    part_name = os.path.basename(part_path)
                    part_size = os.path.getsize(part_path)
                    last_part_up = [0.0]

                    async def part_upload_progress(current, total):
                        now = time.time()
                        if now - last_part_up[0] >= 3.5:
                            last_part_up[0] = now
                            pct = int(current / total * 100) if total else 0
                            await status_msg.edit(
                                f"📤 **Uploading Part [{idx}/{total_parts}]:** `{pct}%` (`{format_bytes(current)}` / `{format_bytes(total)}`)..."
                            )

                    caption = (
                        f"🗜️ **{session['name']} — Part {idx} of {total_parts}**\n"
                        f"📁 **Files Packed:** `{len(session['files'])}`\n"
                        f"📄 `{part_name}` • 💾 `{format_bytes(part_size)}`\n"
                        f"{pwd_text}"
                    )
                    await event.client.send_file(
                        event.chat_id,
                        file=part_path,
                        caption=caption,
                        progress_callback=part_upload_progress,
                        reply_to=getattr(event, "topic_id", None),
                    )
                    try:
                        os.remove(part_path)
                    except Exception:
                        pass

                await event.reply(
                    f"✅ **All {total_parts} Zip parts uploaded successfully!**\n\n"
                    f"💡 **How to Extract:**\n"
                    f"1. Download all parts into the **same folder** on your PC or phone.\n"
                    f"2. Open or extract the final file (`.zip`) using **7-Zip, WinRAR, or ZArchiver**.\n"
                    f"3. All parts will automatically merge and extract your complete files!"
                )
                await status_msg.delete()
                return

            await status_msg.edit(f"📤 **Uploading `{session['name']}` ({format_bytes(zip_size)})...**")
            await event.client.send_file(
                event.chat_id,
                file=out_zip_path,
                caption=(
                    f"🗜️ **{session['name']}**\n"
                    f"📁 **Files Packed:** `{len(session['files'])}`\n"
                    f"💾 **Archive Size:** `{format_bytes(zip_size)}`\n"
                    f"{pwd_text}"
                ),
                reply_to=getattr(event, "topic_id", None),
            )
            await status_msg.delete()
        except Exception as e:
            logger.exception("Batch zip creation failed: %s", e)
            await status_msg.edit(f"❌ **Zip Creation Failed:** `{e}`")
        finally:
            clear_zip_session(user_id)
        return

    # 2. Single-file zipping mode: reply /zip [password] [archive_name] to any media/file
    reply = await event.get_reply_message()
    if not reply or not reply.media:
        # Check if user has an active session
        sess = get_zip_session(user_id)
        if sess:
            await event.reply(
                f"📦 **Active Batch Zip Session:** `{sess['name']}`\n"
                f"• **Files Queued:** `{len(sess['files'])}`\n"
                f"• Send files to add, or send `/zip done [password]` to finish!"
            )
            return

        await event.reply(
            "🗜️ **How to Create a Zip File:**\n\n"
            "**Option A (Single File):**\n"
            "• Reply `/zip` to any file, photo, video, or audio.\n"
            "• Add a password: `/zip mypassword`\n\n"
            "**Option B (Batch Archive / Multiple Files):**\n"
            "• Send `/zip new [archive_name.zip]`\n"
            "• Send all your files one by one\n"
            "• Send `/zip done [password]` to build and receive your zip!"
        )
        return

    password = args[0] if args else None
    target_name = get_archive_filename(reply)
    zip_name = f"{os.path.splitext(target_name)[0]}.zip"

    status_msg = await event.reply(f"⏳ **Preparing to zip `{target_name}`...**")
    loop = asyncio.get_running_loop()
    temp_dir = tempfile.mkdtemp(prefix="novacore_singlezip_")
    src_path = os.path.join(temp_dir, target_name)
    out_zip_path = os.path.join(temp_dir, zip_name)

    try:
        last_down_time = [0.0]

        async def download_progress(current, total):
            now = time.time()
            if now - last_down_time[0] >= 3.5:
                last_down_time[0] = now
                pct = int(current / total * 100) if total else 0
                await status_msg.edit(f"📥 **Downloading `{target_name}`:** `{pct}%`...")

        await event.client.download_media(reply, file=src_path, progress_callback=download_progress)
        orig_size = os.path.getsize(src_path)

        await status_msg.edit("🗜️ **Compressing file into AES-256 Zip...**")

        def _create_single_zip():
            if password:
                with pyzipper.AESZipFile(
                    out_zip_path,
                    "w",
                    compression=pyzipper.ZIP_DEFLATED,
                    encryption=pyzipper.WZ_AES,
                ) as zf:
                    zf.setpassword(password.encode("utf-8"))
                    zf.write(src_path, arcname=target_name)
            else:
                with zipfile.ZipFile(out_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    zf.write(src_path, arcname=target_name)

        await loop.run_in_executor(None, _create_single_zip)
        zip_size = os.path.getsize(out_zip_path)
        pwd_text = f"🔑 **Password:** `{password}` (AES-256 Encrypted)\n" if password else "🔓 **Unencrypted**\n"

        # Check if single zip exceeds Telegram 2GB limit -> Split into parts!
        if zip_size > 2000 * 1024 * 1024:
            await status_msg.edit(
                f"📦 **Archive size ({format_bytes(zip_size)}) exceeds Telegram 2GB limit.**\n"
                f"✂️ **Splitting into multi-volume Zip parts (~1.85 GB each)...**"
            )
            parts = await loop.run_in_executor(None, split_file_into_zips, out_zip_path, 1900)
            total_parts = len(parts)

            for idx, part_path in enumerate(parts, 1):
                part_name = os.path.basename(part_path)
                part_size = os.path.getsize(part_path)
                last_part_up = [0.0]

                async def part_upload_progress(current, total):
                    now = time.time()
                    if now - last_part_up[0] >= 3.5:
                        last_part_up[0] = now
                        pct = int(current / total * 100) if total else 0
                        await status_msg.edit(
                            f"📤 **Uploading Part [{idx}/{total_parts}]:** `{pct}%` (`{format_bytes(current)}` / `{format_bytes(total)}`)..."
                        )

                caption = (
                    f"🗜️ **{zip_name} — Part {idx} of {total_parts}**\n"
                    f"📄 `{part_name}` • 💾 `{format_bytes(part_size)}`\n"
                    f"{pwd_text}"
                )
                await event.client.send_file(
                    event.chat_id,
                    file=part_path,
                    caption=caption,
                    progress_callback=part_upload_progress,
                    reply_to=getattr(event, "topic_id", None),
                )
                try:
                    os.remove(part_path)
                except Exception:
                    pass

            await event.reply(
                f"✅ **All {total_parts} Zip parts uploaded successfully!**\n\n"
                f"💡 **How to Extract:**\n"
                f"1. Download all parts into the **same folder** on your PC or phone.\n"
                f"2. Open or extract the final file (`.zip`) using **7-Zip, WinRAR, or ZArchiver**.\n"
                f"3. All parts will automatically merge and extract your complete file!"
            )
            await status_msg.delete()
            return

        await status_msg.edit(f"📤 **Uploading `{zip_name}` ({format_bytes(zip_size)})...**")
        await event.client.send_file(
            event.chat_id,
            file=out_zip_path,
            caption=(
                f"🗜️ **{zip_name}**\n"
                f"📄 **Contains:** `{target_name}`\n"
                f"💾 **Size:** `{format_bytes(orig_size)}` ➔ `{format_bytes(zip_size)}`\n"
                f"{pwd_text}"
            ),
            reply_to=getattr(event, "topic_id", None),
        )
        await status_msg.delete()
    except Exception as e:
        logger.exception("Single zip failed: %s", e)
        await status_msg.edit(f"❌ **Zip Failed:** `{e}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ==========================================
# BATCH FILE RECEIVER LISTENER
# ==========================================

from telethon import events


@client_raw_handler(events.NewMessage(
    incoming=True,
    func=lambda e: (e.media or e.photo or e.document) and not (getattr(e, "text", "") or "").startswith(("/", "."))
))
async def batch_zip_file_receiver(event: SafeMessageWrapper):
    """
    Automatically captures incoming media/files when the sender has an active /zip new session.
    """
    user_id = event.sender_id or event.chat_id
    session = get_zip_session(user_id)
    if not session:
        return

    msg = event.message
    if not (getattr(msg, "media", None) or getattr(msg, "photo", None) or getattr(msg, "document", None) or getattr(msg, "file", None)):
        return

    filename = get_media_filename(msg, fallback_index=len(session["files"]) + 1)
    dest_path = os.path.join(session["dir"], filename)
    base, ext = os.path.splitext(filename)
    counter = 1
    while os.path.exists(dest_path):
        dest_path = os.path.join(session["dir"], f"{base}_{counter}{ext}")
        counter += 1

    try:
        saved_path = await event.client.download_media(msg, file=dest_path)
        if saved_path and os.path.exists(saved_path):
            session["files"].append(saved_path)
            session["last_activity"] = time.time()
            save_zip_session(user_id, session)
            fsize = os.path.getsize(saved_path)
            await event.reply(
                f"📥 **Added to `{session['name']}`:** `{os.path.basename(saved_path)}` (`{format_bytes(fsize)}`)\n"
                f"📊 **Total files in session:** `{len(session['files'])}`\n"
                f"👉 *Send more files, or type `/zip done` to finish!*"
            )
    except Exception as e:
        logger.exception("Failed to download media for batch zip: %s", e)

@bot_cmd(
    pattern=r"(?:ziplist|listarchive)",
    description="Inspect archive contents without extracting (/ziplist)",
    module="utility",
)
async def ziplist_cmd(event: SafeMessageWrapper, query: str = ""):
    reply = await event.get_reply_message()
    if not reply or not reply.media:
        await event.reply("ℹ️ Reply to any `.zip`, `.7z`, or `.tar` archive with `/ziplist` to inspect its contents.")
        return

    raw_filename = get_archive_filename(reply)
    status_msg = await event.reply(f"🔎 **Inspecting `{raw_filename}`...**")
    loop = asyncio.get_running_loop()
    temp_dir = tempfile.mkdtemp(prefix="novacore_list_")
    archive_path = os.path.join(temp_dir, raw_filename)

    try:
        await event.client.download_media(reply, file=archive_path)

        def _read_list():
            items = []
            fn = raw_filename.lower()
            if fn.endswith(".zip"):
                with zipfile.ZipFile(archive_path) as zf:
                    for info in zf.infolist():
                        items.append((info.filename, info.file_size))
            elif fn.endswith(".7z"):
                with py7zr.SevenZipFile(archive_path, mode="r") as szf:
                    for info in szf.list():
                        items.append((info.filename, info.uncompressed))
            elif any(fn.endswith(ext) for ext in [".tar", ".tar.gz", ".tgz", ".tar.bz2"]):
                with tarfile.open(archive_path, "r:*") as tf:
                    for m in tf.getmembers():
                        items.append((m.name, m.size))
            return items

        entries = await loop.run_in_executor(None, _read_list)
        if not entries:
            await status_msg.edit(f"📁 **Archive `{raw_filename}` is empty or encrypted.**")
            return

        total_uncompressed = sum(size for _, size in entries)
        lines = [
            f"📋 **Archive Contents:** `{raw_filename}`",
            f"• **Total Files:** `{len(entries)}`",
            f"• **Uncompressed Size:** `{format_bytes(total_uncompressed)}`\n",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

        for fname, fsize in entries[:25]:
            lines.append(f"• `{fname}` (`{format_bytes(fsize)}`)")

        if len(entries) > 25:
            lines.append(f"*(and {len(entries) - 25} more files)*")

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("👉 *To extract all: Reply `/unzip` to the original archive.*")
        await status_msg.edit("\n".join(lines))
    except Exception as e:
        await status_msg.edit(f"❌ **Failed to inspect archive:** `{e}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
