"""
Ultroid-Neo: Dynamic Command Dispatcher & Forum Topic Router
Handles command registration, prefix matching, outgoing message verification,
Forum topic-thread routing, and Guardian Shield execution.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from telethon import events
from telethon.tl.custom import Message

from core.config import settings
from core.ratelimiter import guardian

logger = logging.getLogger("ultroid_neo.dispatcher")

# Registry for dynamic .help reflection
COMMAND_REGISTRY: Dict[str, Dict[str, Any]] = {}


class SafeMessageWrapper:
    """
    Wraps Telethon Message events to guarantee:
    1. Forum topic persistence (reply_to_msg_id and top_msg_id routing).
    2. Outbound pacing and FloodWait circuit-breaking via Guardian Shield.
    3. Throttled edits for progress bars.
    """

    def __init__(self, raw_event: events.NewMessage.Event):
        self._event = raw_event
        self.message: Message = raw_event.message
        self.client = raw_event.client
        self.chat_id = raw_event.chat_id
        self.sender_id = raw_event.sender_id
        self.is_private = raw_event.is_private
        self.is_group = raw_event.is_group
        self.is_channel = raw_event.is_channel

        # Extract Forum Topic ID (Telegram Modernity)
        self.topic_id: Optional[int] = None
        if self.message.reply_to:
            self.topic_id = (
                getattr(self.message.reply_to, "reply_to_top_id", None)
                or self.message.reply_to.reply_to_msg_id
            )

    async def edit(self, text: str, **kwargs: Any) -> Any:
        """Edits the command message safely through Guardian Shield."""
        async def _do_edit():
            return await self.message.edit(text, **kwargs)

        return await guardian.execute_safe(_do_edit)

    async def reply(self, text: str, **kwargs: Any) -> Any:
        """Replies within the exact same Forum topic and paces through Guardian Shield."""
        if self.topic_id and "reply_to" not in kwargs:
            kwargs["reply_to"] = self.topic_id

        async def _do_reply():
            return await self._event.reply(text, **kwargs)

        return await guardian.execute_safe(_do_reply)

    async def delete(self) -> Any:
        """Deletes the message through Guardian Shield."""
        async def _do_delete():
            return await self.message.delete()

        return await guardian.execute_safe(_do_delete)

    async def respond(self, text: str, **kwargs: Any) -> Any:
        """Responds in chat through Guardian Shield."""
        if self.topic_id and "reply_to" not in kwargs:
            kwargs["reply_to"] = self.topic_id

        async def _do_respond():
            return await self._event.respond(text, **kwargs)

        return await guardian.execute_safe(_do_respond)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._event, item)


def client_cmd(
    pattern: str,
    description: str = "No description provided.",
    module: str = "general",
    prefix: Optional[str] = None,
):
    """
    Decorator for userbot commands.
    Ensures outgoing execution only, regex command matching, topic binding,
    and automatic error containment.
    """
    pfx = re.escape(prefix or settings.COMMAND_PREFIX)
    clean_pat = pattern.strip("^$")
    # If the author included trailing argument regex like (?:\s+...) or \s+, strip that out for base command matching
    base_cmd_pat = re.sub(r"\(\?:\s*\\s\+.*", "", clean_pat).strip()
    if not base_cmd_pat:
        base_cmd_pat = clean_pat

    full_pattern = rf"^{pfx}(?:{base_cmd_pat})(?:$|\s+([\s\S]*))"
    regex = re.compile(full_pattern, flags=re.DOTALL)
    trigger_regex = re.compile(rf"^{pfx}(?:{base_cmd_pat})(?:\s+|$)", flags=re.DOTALL)

    # Extract clean display command names from pattern
    clean_candidates = re.findall(r"\b[a-zA-Z0-9_-]+\b", base_cmd_pat)
    cmd_name = clean_candidates[0] if clean_candidates else base_cmd_pat.replace("$", "").strip()

    COMMAND_REGISTRY[cmd_name] = {
        "pattern": pattern,
        "description": description,
        "module": module,
    }

    def decorator(func: Callable):
        sig = inspect.signature(func)
        param_count = len(sig.parameters)

        async def wrapper(event: events.NewMessage.Event):
            # Only process outgoing messages sent by the account owner
            if not event.out:
                return

            text = event.text or ""
            m = trigger_regex.match(text)
            if not m:
                return

            arg_str = text[m.end():].strip()

            wrapped_event = SafeMessageWrapper(event)
            try:
                if param_count >= 2:
                    await func(wrapped_event, arg_str)
                else:
                    await func(wrapped_event)
            except Exception as e:
                logger.exception("Error executing command %s: %s", cmd_name, e)
                try:
                    await wrapped_event.edit(
                        f"❌ **Error executing `{cmd_name}`:**\n`{str(e)}`"
                    )
                except Exception:
                    pass

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = description
        wrapper._is_ultroid_cmd = True
        wrapper._event_builder = events.NewMessage(outgoing=True, pattern=regex)
        return wrapper

    return decorator

    return decorator


def load_all_plugins(client: Any) -> int:
    """
    Discovers and imports all plugins under plugins/ directory and registers event handlers.
    Returns the number of loaded plugin modules.
    """
    plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
    loaded_count = 0

    for py_file in plugins_dir.rglob("*.py"):
        if py_file.name.startswith("__"):
            continue

        relative_path = py_file.relative_to(plugins_dir.parent)
        module_name = ".".join(relative_path.with_suffix("").parts)

        try:
            if module_name in sys.modules:
                mod = importlib.reload(sys.modules[module_name])
            else:
                mod = importlib.import_module(module_name)

            # Register any decorated commands with Telethon client
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if callable(attr) and getattr(attr, "_is_ultroid_cmd", False):
                    event_builder = getattr(attr, "_event_builder", None)
                    if event_builder and client:
                        client.add_event_handler(attr, event_builder)

            loaded_count += 1
            logger.info("Loaded plugin module: %s", module_name)
        except Exception as e:
            logger.error("Failed to load plugin module %s: %s", module_name, e, exc_info=True)

    return loaded_count
