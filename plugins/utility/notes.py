"""
Ultroid-Neo Plugin: Cloud Notes & Snippet Manager (.save / .get / .notes / .clear)
Stores notes and text snippets permanently in the local SQLite database.
"""

from __future__ import annotations

from core.dispatcher import client_cmd, SafeMessageWrapper
from core.database import db


@client_cmd(
    pattern="save",
    description="Save a note or snippet (.save <name> <content or reply>)",
    module="utility",
)
async def save_note_cmd(event: SafeMessageWrapper, args: str = ""):
    parts = args.split(maxsplit=1) if args else []
    if not parts:
        await event.edit("❌ **Usage:** `.save <note_name> <text>` or reply to a message.")
        return

    name = parts[0].lower()
    content = parts[1].strip() if len(parts) > 1 else ""

    if not content:
        reply = await event.get_reply_message()
        if reply and reply.text:
            content = reply.text.strip()

    if not content:
        await event.edit("❌ Note content cannot be empty.")
        return

    await db.save_note(name, content)
    await event.edit(f"💾 **Note Saved:** `{name}`")


@client_cmd(
    pattern="get",
    description="Retrieve a saved note (.get <name>)",
    module="utility",
)
async def get_note_cmd(event: SafeMessageWrapper, name_arg: str = ""):
    name = name_arg.lower().strip() if name_arg else ""
    if not name:
        await event.edit("❌ **Usage:** `.get <note_name>`")
        return

    note = await db.get_note(name)
    if not note:
        await event.edit(f"❌ Note `{name}` not found. Type `.notes` to see all.")
        return

    await event.edit(note["content"])


@client_cmd(
    pattern="notes",
    description="List all saved notes",
    module="utility",
)
async def list_notes_cmd(event: SafeMessageWrapper):
    note_names = await db.list_notes()
    if not note_names:
        await event.edit("📂 **No saved notes found.** Use `.save <name> <text>` to create one.")
        return

    names_formatted = "\n".join(f"• `{n}`" for n in note_names)
    text = (
        f"📝 **Saved Notes ({len(note_names)}):**\n"
        f"──────────────────────────────\n"
        f"{names_formatted}\n"
        f"──────────────────────────────\n"
        f"💡 *Retrieve any note with `.get <name>`*"
    )
    await event.edit(text)


@client_cmd(
    pattern="clear",
    description="Delete a saved note (.clear <name>)",
    module="utility",
)
async def clear_note_cmd(event: SafeMessageWrapper, name_arg: str = ""):
    name = name_arg.lower().strip() if name_arg else ""
    if not name:
        await event.edit("❌ **Usage:** `.clear <note_name>`")
        return

    deleted = await db.delete_note(name)
    if deleted:
        await event.edit(f"🗑️ Note `{name}` deleted.")
    else:
        await event.edit(f"❌ Note `{name}` not found.")
