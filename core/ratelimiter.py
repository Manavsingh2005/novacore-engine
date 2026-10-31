"""
Ultroid-Neo: The Guardian Anti-Ban Shield & Rate Limiting Engine
Implements official device mimicry, human-like micro-jitter, progress edit throttling,
and an emergency FloodWait circuit breaker.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable, Coroutine, Dict, Optional
from telethon.errors import FloodWaitError

from core.config import settings

logger = logging.getLogger("ultroid_neo.guardian")


class GuardianShield:
    """
    Tier-1 Anti-Ban & Account Protection Engine.
    Ensures safe pacing, randomized human micro-jitter, and circuit-breaking on FloodWaits.
    """

    def __init__(self):
        self.lock = asyncio.Lock()
        self.last_action_timestamp: float = 0.0
        self.last_edit_timestamps: Dict[int, float] = {}  # msg_id -> timestamp
        self.circuit_tripped: bool = False
        self.circuit_cooldown_until: float = 0.0

    async def pace_outbound(self, base_delay: float = 0.3) -> None:
        """
        Applies randomized human micro-jitter between outbound requests.
        Mimics human pacing to avoid mechanical burst detection by Telegram anti-spam.
        """
        if not settings.ANTI_BAN_GUARD:
            return

        async with self.lock:
            # Check circuit breaker
            now = time.time()
            if self.circuit_tripped:
                if now < self.circuit_cooldown_until:
                    wait_left = int(self.circuit_cooldown_until - now)
                    logger.warning(
                        "Guardian Shield is in protective cooldown for another %ds.",
                        wait_left,
                    )
                    raise RuntimeError(
                        f"⚠️ Guardian Circuit Breaker Active: Cooldown for {wait_left}s."
                    )
                else:
                    self.circuit_tripped = False
                    logger.info("Guardian Shield circuit breaker has reset.")

            jitter_ms = random.randint(
                settings.HUMAN_JITTER_MIN_MS,
                settings.HUMAN_JITTER_MAX_MS,
            )
            total_delay = base_delay + (jitter_ms / 1000.0)

            elapsed = now - self.last_action_timestamp
            if elapsed < total_delay:
                await asyncio.sleep(total_delay - elapsed)

            self.last_action_timestamp = time.time()

    def should_throttle_edit(self, msg_id: int) -> bool:
        """
        Checks if an edit to a given message is happening too fast (e.g. download progress).
        Returns True if the edit should be skipped to prevent message-edit spam bans.
        """
        now = time.time()
        last_edit = self.last_edit_timestamps.get(msg_id, 0.0)
        if now - last_edit < settings.THROTTLE_PROGRESS_INTERVAL_SECS:
            return True
        self.last_edit_timestamps[msg_id] = now
        return False

    def record_edit(self, msg_id: int) -> None:
        """Records an edit timestamp for a message."""
        self.last_edit_timestamps[msg_id] = time.time()

    async def execute_safe(
        self,
        coro_func: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        alert_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Executes a Telegram API coroutine with automatic FloodWait catching and safety backoff.
        Trips the emergency circuit breaker if FloodWait exceeds safe threshold.
        """
        try:
            await self.pace_outbound()
            return await coro_func(*args, **kwargs)
        except FloodWaitError as e:
            wait_seconds = int(e.seconds)
            logger.warning("Telegram issued FloodWaitError: %ds", wait_seconds)

            if wait_seconds >= settings.FLOOD_WAIT_CIRCUIT_BREAKER_SECS:
                # EMERGENCY STOP: Trip the circuit breaker to protect the account
                self.circuit_tripped = True
                self.circuit_cooldown_until = time.time() + min(wait_seconds, 300)
                alert_msg = (
                    f"⚠️ **Guardian Shield: Emergency Circuit Breaker Tripped!**\n\n"
                    f"Telegram requested a FloodWait of **{wait_seconds}s**.\n"
                    f"Command aborted immediately to protect your primary account from penalties."
                )
                logger.critical(alert_msg)
                if alert_callback:
                    try:
                        await alert_callback(alert_msg)
                    except Exception:
                        pass
                raise RuntimeError(alert_msg) from e
            else:
                # Minor wait: sleep gracefully and retry once
                logger.info("Minor FloodWait: Sleeping for %ds + 2s safety buffer...", wait_seconds)
                await asyncio.sleep(wait_seconds + 2)
                return await coro_func(*args, **kwargs)


# Global singleton instance
guardian = GuardianShield()
