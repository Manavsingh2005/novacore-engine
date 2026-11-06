"""
NovaCore: Telegram Utility Bot Command & Callback Dispatcher
Handles command routing (/command and .command), inline keyboard callback queries,
incoming message wrappers, topic preservation, and rate limiting.
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

logger = logging.getLogger("novacore.dispatcher")

# Registry for dynamic /help reflection
COMMAND_REGISTRY: Dict[str, Dict[str, Any]] = {}


class SafeMessageWrapper:
    """
    Wraps Telethon Message & Callback events to guarantee:
    1. Unified edit/reply/respond methods.
    2. Forum topic persistence.
    3. Guardian Shield rate limiting and FloodWait protection.
    """

    def __init__(self, raw_event: Any):
        self._event = raw_event
        self.client = raw_event.client
        self.chat_id = raw_event.chat_id
        self.sender_id = getattr(raw_event, "sender_id", None)
        self.is_private = getattr(raw_event, "is_private", True)
        self.is_group = getattr(raw_event, "is_group", False)
        self.is_channel = getattr(raw_event, "is_channel", False)

        # Distinguish between NewMessage and CallbackQuery
        if hasattr(raw_event, "message") and isinstance(raw_event.message, Message):
            self.message: Message = raw_event.message
        elif isinstance(raw_event, Message):
            self.message: Message = raw_event
        else:
            self.message = getattr(raw_event, "message", None)

        # Extract Forum Topic ID
        self.topic_id: Optional[int] = None
        if self.message and self.message.reply_to:
            self.topic_id = (
                getattr(self.message.reply_to, "reply_to_top_id", None)
                or self.message.reply_to.reply_to_msg_id
            )

    async def edit(self, text: str, **kwargs: Any) -> Any:
        """Edits the message if outgoing or callback, otherwise replies."""
        async def _do_edit():
            if isinstance(self._event, events.CallbackQuery.Event):
                return await self._event.edit(text, **kwargs)
            if self.message and getattr(self.message, "out", False):
                return await self.message.edit(text, **kwargs)
            # Incoming message: reply with a new message instead of failing
            if self.topic_id and "reply_to" not in kwargs:
                kwargs["reply_to"] = self.topic_id
            if hasattr(self._event, "reply"):
                return await self._event.reply(text, **kwargs)
            return await self.client.send_message(self.chat_id, text, **kwargs)

        return await guardian.execute_safe(_do_edit)

    async def reply(self, text: str, **kwargs: Any) -> Any:
        """Replies to the message within the exact same Forum topic."""
        if self.topic_id and "reply_to" not in kwargs:
            kwargs["reply_to"] = self.topic_id

        async def _do_reply():
            if hasattr(self._event, "reply"):
                return await self._event.reply(text, **kwargs)
            elif self.message:
                return await self.message.reply(text, **kwargs)
            return await self.client.send_message(self.chat_id, text, **kwargs)

        return await guardian.execute_safe(_do_reply)

    async def respond(self, text: str, **kwargs: Any) -> Any:
        """Responds in chat through Guardian Shield."""
        if self.topic_id and "reply_to" not in kwargs:
            kwargs["reply_to"] = self.topic_id

        async def _do_respond():
            if hasattr(self._event, "respond"):
                return await self._event.respond(text, **kwargs)
            return await self.client.send_message(self.chat_id, text, **kwargs)

        return await guardian.execute_safe(_do_respond)

    async def delete(self) -> Any:
        """Deletes the message through Guardian Shield."""
        async def _do_delete():
            if self.message:
                return await self.message.delete()

        return await guardian.execute_safe(_do_delete)

    async def get_reply_message(self) -> Optional[Message]:
        """Retrieves the replied message."""
        if self.message and hasattr(self.message, "get_reply_message"):
            return await self.message.get_reply_message()
        if hasattr(self._event, "get_reply_message"):
            return await self._event.get_reply_message()
        return None

    def __getattr__(self, item: str) -> Any:
        if self.message and hasattr(self.message, item):
            return getattr(self.message, item)
        return getattr(self._event, item)


def bot_cmd(
    pattern: str,
    description: str = "No description provided.",
    module: str = "general",
):
    """
    Decorator for bot commands. Listens on incoming messages.
    Supports /command, .command, and /command@botname.
    """
    clean_pat = pattern.strip("^$")
    base_cmd_pat = re.sub(r"\(\?:\s*\\s\+.*", "", clean_pat).strip()
    if not base_cmd_pat:
        base_cmd_pat = clean_pat

    # Matches /cmd or .cmd with optional @botusername and arguments
    full_pattern = rf"^[/.](?:{base_cmd_pat})(?:@\w+)?(?:$|\s+([\s\S]*))"
    regex = re.compile(full_pattern, flags=re.DOTALL | re.IGNORECASE)
    trigger_regex = re.compile(rf"^[/.](?:{base_cmd_pat})(?:@\w+)?(?:\s+|$)", flags=re.DOTALL | re.IGNORECASE)

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
            # Process incoming messages sent to the bot
            if getattr(event, "out", False):
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
                logger.exception("Error executing bot command %s: %s", cmd_name, e)
                try:
                    await wrapped_event.reply(
                        f"❌ **Error executing `/{cmd_name}`:**\n`{str(e)}`"
                    )
                except Exception:
                    pass

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = description
        wrapper._is_novacore_cmd = True
        wrapper._event_builder = events.NewMessage(incoming=True, pattern=regex)
        return wrapper

    return decorator


# Backward-compatibility alias so existing plugins continue to load
client_cmd = bot_cmd


def bot_callback(pattern: Optional[str | re.Pattern] = None):
    """
    Decorator for inline keyboard callback query handlers.
    """
    if isinstance(pattern, str):
        regex = re.compile(pattern)
    elif pattern is None:
        regex = None
    else:
        regex = pattern

    def decorator(func: Callable):
        sig = inspect.signature(func)
        param_count = len(sig.parameters)

        async def wrapper(event: events.CallbackQuery.Event):
            wrapped_event = SafeMessageWrapper(event)
            try:
                if param_count >= 1:
                    await func(wrapped_event)
                else:
                    await func()
            except Exception as e:
                logger.exception("Error executing callback handler %s: %s", func.__name__, e)
                try:
                    await event.answer(f"Error: {e}", alert=True)
                except Exception:
                    pass

        wrapper.__name__ = func.__name__
        wrapper._is_novacore_cmd = True
        wrapper._event_builder = events.CallbackQuery(pattern=regex)
        return wrapper

    return decorator


def client_raw_handler(event_builder):
    """Decorator for custom raw Telethon event builders."""
    def decorator(func):
        sig = inspect.signature(func)
        param_count = len(sig.parameters)

        async def wrapper(event):
            wrapped = SafeMessageWrapper(event)
            try:
                if param_count >= 1:
                    await func(wrapped)
                else:
                    await func()
            except Exception as e:
                logger.exception("Error executing raw handler %s: %s", func.__name__, e)

        wrapper.__name__ = func.__name__
        wrapper._is_novacore_cmd = True
        wrapper._event_builder = event_builder
        return wrapper

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

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if callable(attr) and getattr(attr, "_is_novacore_cmd", False):
                    event_builder = getattr(attr, "_event_builder", None)
                    if event_builder and client:
                        client.add_event_handler(attr, event_builder)

            loaded_count += 1
            logger.info("Loaded plugin module: %s", module_name)
        except Exception as e:
            logger.error("Failed to load plugin module %s: %s", module_name, e, exc_info=True)

    return loaded_count
