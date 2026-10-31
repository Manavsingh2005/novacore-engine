"""
Ultroid-Neo: Dynamic Elastic N-Key Groq AI Pool (Enterprise Grade)
Implements:
- Round-robin load-balancing across arbitrary N keys.
- Instant 429 rate-limit failover without dropping queries.
- Live model discovery directly from Groq API (models.list).
- Dynamic model switching (.setmodel).
- Key usage statistics tracking (requests_count, latency_ms).
- Whisper voice transcription & foreign audio translation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from groq import AsyncGroq, RateLimitError, APIError

from core.config import settings
from core.database import db

logger = logging.getLogger("ultroid_neo.ai_pool")


class KeyState:
    def __init__(
        self,
        key_id: int,
        key: str,
        provider: str = "groq",
        requests_count: int = 0,
        latency_ms: float = 0.0,
    ):
        self.key_id = key_id
        self.key = key
        self.provider = provider
        self.client = AsyncGroq(api_key=key)
        self.cooldown_until: float = 0.0
        self.latency_ms: float = latency_ms
        self.requests_count: int = requests_count
        self.is_healthy: bool = True

    @property
    def is_available(self) -> bool:
        return self.is_healthy and (time.time() >= self.cooldown_until)

    def mark_rate_limited(self, cooldown_seconds: float = 30.0):
        self.cooldown_until = time.time() + cooldown_seconds
        logger.warning(
            "Groq Key #%d rate-limited. Cooling down for %.1fs.",
            self.key_id,
            cooldown_seconds,
        )


class AIPoolManager:
    """
    Elastic multi-key pool supporting arbitrary N keys.
    Automatically rotates between keys and fails over instantly on 429 errors.
    """

    def __init__(self):
        self._keys: List[KeyState] = []
        self._current_index: int = 0
        self._lock = asyncio.Lock()

    async def reload_keys(self) -> int:
        """Loads keys from both config and database, building active KeyState objects."""
        async with self._lock:
            db_keys = await db.get_active_keys("groq")
            if not db_keys:
                all_keys = await db.get_all_keys()
                db_keys = [k for k in all_keys if k["provider"] == "groq"]

            seen_keys = set()
            new_pool: List[KeyState] = []

            # 1. Load keys from database
            for row in db_keys:
                raw_key = row["key"].strip()
                if raw_key and raw_key not in seen_keys:
                    seen_keys.add(raw_key)
                    state = KeyState(
                        key_id=row["id"],
                        key=raw_key,
                        provider="groq",
                        requests_count=row.get("requests_count", 0),
                        latency_ms=row.get("latency_ms", 0.0),
                    )
                    new_pool.append(state)

            # 2. Add any keys from .env that weren't in DB yet
            for env_key in settings.groq_keys:
                if env_key not in seen_keys:
                    seen_keys.add(env_key)
                    success, _ = await db.add_api_key("groq", env_key)
                    if success:
                        latest = await db.get_active_keys("groq")
                        matching = [k for k in latest if k["key"] == env_key]
                        kid = matching[0]["id"] if matching else len(new_pool) + 1
                    else:
                        kid = len(new_pool) + 1

                    new_pool.append(KeyState(key_id=kid, key=env_key, provider="groq"))

            self._keys = new_pool
            self._current_index = 0
            logger.info("AIPool reloaded: %d active keys in rotation.", len(self._keys))
            return len(self._keys)

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def _get_next_key(self) -> Optional[KeyState]:
        """Round-robin selection of the next available, healthy key."""
        if not self._keys:
            return None

        total = len(self._keys)
        for _ in range(total):
            state = self._keys[self._current_index]
            self._current_index = (self._current_index + 1) % total
            if state.is_available:
                return state

        # If all are in cooldown, pick the one with earliest cooldown expiry
        return min(self._keys, key=lambda k: k.cooldown_until)

    async def get_current_model(self) -> str:
        """Returns currently active text model from settings/DB."""
        if db._conn:
            try:
                stored = await db.get_setting("current_ai_model")
                if stored and stored not in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
                    return stored
            except Exception:
                pass
        return settings.DEFAULT_AI_MODEL

    async def set_current_model(self, model_name: str) -> None:
        """Switches default active model in database."""
        await db.set_setting("current_ai_model", model_name.strip())

    async def get_whisper_model(self) -> str:
        """Returns currently active Whisper model from settings/DB."""
        if db._conn:
            try:
                stored = await db.get_setting("current_whisper_model")
                if stored:
                    return stored
            except Exception:
                pass
        return settings.DEFAULT_WHISPER_MODEL

    async def set_whisper_model(self, model_name: str) -> None:
        """Switches default active Whisper model in database."""
        await db.set_setting("current_whisper_model", model_name.strip())

    async def list_available_models(self) -> List[str]:
        """Queries Groq's live API to retrieve currently supported models."""
        if not self._keys:
            await self.reload_keys()

        key_state = self._get_next_key()
        if not key_state:
            return ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "allam-2-7b"]

        try:
            models_page = await key_state.client.models.list()
            model_ids = [m.id for m in models_page.data if hasattr(m, "id")]
            return sorted(model_ids)
        except Exception as e:
            logger.warning("Could not fetch models dynamically: %s", e)
            return ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "allam-2-7b"]

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1500,
    ) -> str:
        """
        Executes chat completion with automatic 429 failover across all keys in the pool.
        """
        if not self._keys:
            await self.reload_keys()

        if not self._keys:
            raise RuntimeError(
                "No Groq API keys configured. Use `.addkey groq <YOUR_KEY>` to add one."
            )

        active_model = model or await self.get_current_model()
        attempts = 0
        max_attempts = max(len(self._keys) * 2, 3)

        last_error = None
        while attempts < max_attempts:
            key_state = self._get_next_key()
            if not key_state:
                raise RuntimeError("All AI API keys are currently exhausted or unavailable.")

            try:
                t0 = time.time()
                response = await key_state.client.chat.completions.create(
                    model=active_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                latency = (time.time() - t0) * 1000.0
                key_state.latency_ms = round(latency, 1)
                key_state.requests_count += 1

                # Track usage in DB
                asyncio.create_task(db.increment_key_usage(key_state.key_id))

                choice = response.choices[0]
                content = choice.message.content or ""
                if not content.strip() and getattr(choice.message, "reasoning", None):
                    content = choice.message.reasoning or ""
                return content.strip()
            except RateLimitError as e:
                attempts += 1
                last_error = e
                key_state.mark_rate_limited(cooldown_seconds=45.0)
                logger.warning(
                    "Rate limit on Key #%d. Retrying immediately with next key...",
                    key_state.key_id,
                )
                await asyncio.sleep(0.2)
            except APIError as e:
                attempts += 1
                last_error = e
                logger.error("Groq API error on Key #%d: %s", key_state.key_id, e)
                await asyncio.sleep(0.5)

        raise RuntimeError(f"AI query failed after {attempts} attempts: {last_error}")

    async def transcribe_audio(
        self,
        audio_file_path: str,
        model: Optional[str] = None,
    ) -> str:
        """
        Transcribes audio file using Groq Whisper-large-v3.
        Brings Telegram Premium voice transcription to free users for $0.
        """
        if not self._keys:
            await self.reload_keys()

        if not self._keys:
            raise RuntimeError("No Groq API keys available for audio transcription.")

        model = model or await self.get_whisper_model()
        key_state = self._get_next_key()
        if not key_state:
            raise RuntimeError("No available AI key in pool.")

        with open(audio_file_path, "rb") as audio_file:
            transcription = await key_state.client.audio.transcriptions.create(
                file=audio_file,
                model=model,
                response_format="text",
            )
            key_state.requests_count += 1
            asyncio.create_task(db.increment_key_usage(key_state.key_id))
            return str(transcription).strip()

    async def translate_audio(
        self,
        audio_file_path: str,
        model: Optional[str] = None,
    ) -> str:
        """
        Transcribes foreign audio and translates it to English using Groq Whisper.
        """
        if not self._keys:
            await self.reload_keys()

        if not self._keys:
            raise RuntimeError("No Groq API keys available for audio translation.")

        model = model or await self.get_whisper_model()
        key_state = self._get_next_key()
        if not key_state:
            raise RuntimeError("No available AI key in pool.")

        with open(audio_file_path, "rb") as audio_file:
            translation = await key_state.client.audio.translations.create(
                file=audio_file,
                model=model,
                response_format="text",
            )
            key_state.requests_count += 1
            asyncio.create_task(db.increment_key_usage(key_state.key_id))
            return str(translation).strip()

    async def benchmark_all_keys(self) -> List[Dict[str, Any]]:
        """Pings each key individually, measures round-trip latency, and updates database."""
        results = []
        model = await self.get_current_model()
        for state in self._keys:
            t0 = time.time()
            try:
                await state.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=10,
                )
                latency = round((time.time() - t0) * 1000.0, 1)
                state.latency_ms = latency
                state.is_healthy = True
                await db.update_key_latency(state.key_id, latency, is_active=True)
                results.append({
                    "id": state.key_id,
                    "provider": state.provider,
                    "status": "Online",
                    "latency_ms": latency,
                    "requests": state.requests_count,
                    "preview": f"...{state.key[-6:]}",
                })
            except Exception as e:
                state.is_healthy = False
                await db.update_key_latency(state.key_id, 0.0, is_active=True)
                results.append({
                    "id": state.key_id,
                    "provider": state.provider,
                    "status": f"Error: {type(e).__name__}",
                    "latency_ms": 0.0,
                    "requests": state.requests_count,
                    "preview": f"...{state.key[-6:]}",
                })
        return results


# Global AI Pool instance
ai_pool = AIPoolManager()
