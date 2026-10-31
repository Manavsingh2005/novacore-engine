"""
NovaCore: Configuration Engine & Environment Validator
Loads settings from .env file and environment variables with strict type validation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram Credentials ---
    API_ID: int = Field(default=0, description="Telegram API ID from my.telegram.org")
    API_HASH: str = Field(default="", description="Telegram API Hash from my.telegram.org")
    STRING_SESSION: str = Field(default="", description="Telethon StringSession for user account")
    BOT_TOKEN: Optional[str] = Field(default=None, description="Bot API token for Assistant Bot")

    # --- AI & Multi-Key Pool ---
    GROQ_API_KEYS_RAW: str = Field(
        default="",
        alias="GROQ_API_KEYS",
        description="Comma-separated Groq API keys",
    )
    DEFAULT_AI_MODEL: str = Field(
        default="qwen/qwen3.8-27b",
        description="Default text model for .ask, .sum, .rewrite",
    )
    DEFAULT_WHISPER_MODEL: str = Field(
        default="whisper-large-v3-turbo",
        description="Default Whisper model for .v2t voice-to-text",
    )

    # --- Command & Bot Customization ---
    COMMAND_PREFIX: str = Field(default=".", description="Command trigger prefix")
    DATABASE_PATH: str = Field(
        default="data/novacore.db",
        description="Local SQLite database file path",
    )
    LOG_CHAT_ID: str = Field(
        default="me",
        description="Target chat for logs ('me' for Saved Messages or supergroup ID)",
    )

    # --- 24/7 Forever-Free PaaS Keep-Alive ---
    PORT: Optional[int] = Field(
        default=None,
        description="Port for aiohttp micro health server (e.g. 8080 on Render/Koyeb)",
    )

    # --- The Guardian Anti-Ban Shield Settings ---
    GUARDIAN_SHIELD: bool = Field(default=True, description="Enable anti-ban defenses")
    FLOOD_WAIT_CIRCUIT_BREAKER_SECS: int = Field(
        default=60,
        description="FloodWait seconds threshold to trigger emergency circuit breaker",
    )
    THROTTLE_PROGRESS_INTERVAL_SECS: float = Field(
        default=3.5,
        description="Minimum seconds between progress bar message edits",
    )
    HUMAN_JITTER_MIN_MS: int = Field(
        default=150,
        description="Minimum randomized human-like delay between requests (ms)",
    )
    HUMAN_JITTER_MAX_MS: int = Field(
        default=450,
        description="Maximum randomized human-like delay between requests (ms)",
    )

    # --- Official Client Fingerprint (Anti-Ban Stealth) ---
    DEVICE_MODEL: str = Field(default="Desktop", description="Telegram client device model")
    SYSTEM_VERSION: str = Field(default="Windows 11", description="Client OS string")
    APP_VERSION: str = Field(default="5.6.3 x64", description="Official Telegram Desktop version")
    LANG_CODE: str = Field(default="en", description="Client language code")
    SYSTEM_LANG_CODE: str = Field(default="en", description="System language code")

    @property
    def groq_keys(self) -> List[str]:
        """Returns list of stripped, non-empty Groq API keys."""
        if not self.GROQ_API_KEYS_RAW:
            return []
        keys = [k.strip() for k in self.GROQ_API_KEYS_RAW.split(",") if k.strip()]
        return keys

    @property
    def db_file(self) -> Path:
        """Returns absolute path to database file and ensures parent folder exists."""
        target = BASE_DIR / self.DATABASE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


# Singleton instance
settings = Settings()
