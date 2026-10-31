"""
NovaCore: Asynchronous SQLite Database Engine
Serverless, zero-configuration local database using aiosqlite.
Manages settings, dynamic API keys, inbox guard state, notes, memories, and alarms.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import aiosqlite

from core.config import settings

logger = logging.getLogger("novacore.database")


class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(settings.db_file)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Connects to SQLite and runs migrations."""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._create_tables()
        logger.info("Connected to local database at %s", self.db_path)

    async def close(self) -> None:
        """Closes the database connection safely."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed.")

    async def _create_tables(self) -> None:
        """Initializes schema if not present."""
        assert self._conn is not None
        async with self._conn.cursor() as cursor:
            # Settings table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at REAL
                )
            """)

            # Dynamic API keys table (Groq, etc.)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    key TEXT NOT NULL UNIQUE,
                    is_active INTEGER DEFAULT 1,
                    latency_ms REAL DEFAULT 0.0,
                    requests_count INTEGER DEFAULT 0,
                    last_used REAL DEFAULT 0.0,
                    added_at REAL
                )
            """)
            try:
                await cursor.execute("ALTER TABLE api_keys ADD COLUMN requests_count INTEGER DEFAULT 0")
            except Exception:
                pass
            try:
                await cursor.execute("ALTER TABLE api_keys ADD COLUMN last_used REAL DEFAULT 0.0")
            except Exception:
                pass

            # Inbox Guard table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS pm_permit (
                    user_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_interaction REAL,
                    warning_count INTEGER DEFAULT 0
                )
            """)

            # Notes table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    name TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    media_id TEXT,
                    created_at REAL
                )
            """)

            # AFK State table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS afk_state (
                    id INTEGER PRIMARY KEY,
                    is_afk INTEGER DEFAULT 0,
                    reason TEXT,
                    afk_since REAL
                )
            """)

            # Ephemeral Messages table (Ghost Mode auto-deletion)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS ephemeral_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    delete_at REAL NOT NULL
                )
            """)

            # Long-Term User Memories table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_fact TEXT UNIQUE NOT NULL,
                    value_text TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

            # Scheduled Reminders & Voice Alarms table
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    reminder_text TEXT NOT NULL,
                    due_timestamp REAL NOT NULL,
                    voice_enabled INTEGER DEFAULT 1,
                    is_triggered INTEGER DEFAULT 0,
                    created_at REAL NOT NULL
                )
            """)
            await self._conn.commit()

    # --- Settings Operations ---
    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        assert self._conn is not None
        now = time.time()
        await self._conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
        await self._conn.commit()

    # --- API Keys Operations ---
    async def add_api_key(self, provider: str, key: str) -> Tuple[bool, str]:
        assert self._conn is not None
        provider = provider.lower().strip()
        key = key.strip()
        now = time.time()
        try:
            await self._conn.execute(
                "INSERT INTO api_keys (provider, key, is_active, latency_ms, added_at) VALUES (?, ?, 1, 0.0, ?)",
                (provider, key, now),
            )
            await self._conn.commit()
            return True, "Key added successfully."
        except aiosqlite.IntegrityError:
            return False, "Key already exists in the pool."

    async def get_active_keys(self, provider: str) -> List[Dict[str, Any]]:
        assert self._conn is not None
        provider = provider.lower().strip()
        async with self._conn.execute(
            "SELECT id, provider, key, is_active, latency_ms, requests_count, last_used, added_at FROM api_keys WHERE provider = ? AND is_active = 1 ORDER BY id ASC",
            (provider,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_all_keys(self) -> List[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT id, provider, key, is_active, latency_ms, requests_count, last_used, added_at FROM api_keys ORDER BY provider, id ASC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def increment_key_usage(self, key_id: int) -> None:
        assert self._conn is not None
        now = time.time()
        await self._conn.execute(
            "UPDATE api_keys SET requests_count = requests_count + 1, last_used = ? WHERE id = ?",
            (now, key_id),
        )
        await self._conn.commit()

    async def delete_api_key(self, provider: str, key_id: int) -> bool:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM api_keys WHERE provider = ? AND id = ?",
            (provider.lower().strip(), key_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def update_key_latency(self, key_id: int, latency_ms: float, is_active: bool = True) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE api_keys SET latency_ms = ?, is_active = ? WHERE id = ?",
            (latency_ms, 1 if is_active else 0, key_id),
        )
        await self._conn.commit()

    # --- Inbox Guard (PM Permit) Operations ---
    async def is_pm_approved(self, user_id: int) -> bool:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT status FROM pm_permit WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row["status"] == "approved")

    async def approve_pm(self, user_id: int) -> None:
        assert self._conn is not None
        now = time.time()
        await self._conn.execute(
            "INSERT OR REPLACE INTO pm_permit (user_id, status, last_interaction, warning_count) VALUES (?, 'approved', ?, 0)",
            (user_id, now),
        )
        await self._conn.commit()

    async def disapprove_pm(self, user_id: int) -> None:
        assert self._conn is not None
        now = time.time()
        await self._conn.execute(
            "INSERT OR REPLACE INTO pm_permit (user_id, status, last_interaction, warning_count) VALUES (?, 'disapproved', ?, 0)",
            (user_id, now),
        )
        await self._conn.commit()

    async def record_pm_interaction(self, user_id: int) -> int:
        assert self._conn is not None
        now = time.time()
        async with self._conn.execute(
            "SELECT warning_count, last_interaction FROM pm_permit WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            count = (row["warning_count"] + 1) if row else 1

        await self._conn.execute(
            "INSERT OR REPLACE INTO pm_permit (user_id, status, last_interaction, warning_count) VALUES (?, 'pending', ?, ?)",
            (user_id, now, count),
        )
        await self._conn.commit()
        return count

    async def get_pm_last_interaction(self, user_id: int) -> float:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT last_interaction FROM pm_permit WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return float(row["last_interaction"]) if row and row["last_interaction"] else 0.0

    async def get_approved_users(self) -> List[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT user_id, last_interaction FROM pm_permit WHERE status = 'approved' ORDER BY last_interaction DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_pm_stats(self) -> Dict[str, int]:
        assert self._conn is not None
        async with self._conn.execute("SELECT status, COUNT(*) as cnt FROM pm_permit GROUP BY status") as cursor:
            rows = await cursor.fetchall()
            stats = {"approved": 0, "pending": 0, "disapproved": 0, "total": 0}
            total = 0
            for row in rows:
                st = row["status"]
                cnt = row["cnt"]
                stats[st] = cnt
                total += cnt
            stats["total"] = total
            return stats

    # --- AFK Operations ---
    async def set_afk(self, reason: str = "") -> None:
        assert self._conn is not None
        now = time.time()
        await self._conn.execute(
            "INSERT OR REPLACE INTO afk_state (id, is_afk, reason, afk_since) VALUES (1, 1, ?, ?)",
            (reason, now),
        )
        await self._conn.commit()

    async def clear_afk(self) -> Tuple[bool, str, float]:
        assert self._conn is not None
        async with self._conn.execute("SELECT is_afk, reason, afk_since FROM afk_state WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            if not row or not row["is_afk"]:
                return False, "", 0.0
            was_afk = bool(row["is_afk"])
            reason = row["reason"] or ""
            since = float(row["afk_since"] or 0.0)

        await self._conn.execute("UPDATE afk_state SET is_afk = 0 WHERE id = 1")
        await self._conn.commit()
        return was_afk, reason, since

    async def get_afk_status(self) -> Tuple[bool, str, float]:
        assert self._conn is not None
        async with self._conn.execute("SELECT is_afk, reason, afk_since FROM afk_state WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            if row and row["is_afk"]:
                return True, row["reason"] or "", float(row["afk_since"] or 0.0)
            return False, "", 0.0

    # --- Notes Operations ---
    async def save_note(self, name: str, content: str, media_id: Optional[str] = None) -> None:
        assert self._conn is not None
        now = time.time()
        await self._conn.execute(
            "INSERT OR REPLACE INTO notes (name, content, media_id, created_at) VALUES (?, ?, ?, ?)",
            (name.lower().strip(), content, media_id, now),
        )
        await self._conn.commit()

    async def get_note(self, name: str) -> Optional[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT name, content, media_id, created_at FROM notes WHERE name = ?",
            (name.lower().strip(),),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def delete_note(self, name: str) -> bool:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM notes WHERE name = ?", (name.lower().strip(),)
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def list_notes(self) -> List[str]:
        assert self._conn is not None
        async with self._conn.execute("SELECT name FROM notes ORDER BY name ASC") as cursor:
            rows = await cursor.fetchall()
            return [row["name"] for row in rows]

    # --- Ephemeral Message Operations (Auto-deletion) ---
    async def schedule_ephemeral_deletion(self, chat_id: int, message_id: int, delete_after_seconds: float) -> None:
        assert self._conn is not None
        delete_at = time.time() + delete_after_seconds
        await self._conn.execute(
            "INSERT INTO ephemeral_messages (chat_id, message_id, delete_at) VALUES (?, ?, ?)",
            (chat_id, message_id, delete_at),
        )
        await self._conn.commit()

    async def get_due_ephemeral_deletions(self) -> List[Tuple[int, int, int]]:
        assert self._conn is not None
        now = time.time()
        async with self._conn.execute(
            "SELECT id, chat_id, message_id FROM ephemeral_messages WHERE delete_at <= ?",
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [(r["id"], r["chat_id"], r["message_id"]) for r in rows]

    async def remove_ephemeral_records(self, record_ids: List[int]) -> None:
        assert self._conn is not None
        if not record_ids:
            return
        placeholders = ",".join("?" for _ in record_ids)
        await self._conn.execute(
            f"DELETE FROM ephemeral_messages WHERE id IN ({placeholders})",
            record_ids,
        )
        await self._conn.commit()

    # --- Long-Term Memory Operations ---
    async def add_or_update_memory(self, key_fact: str, value_text: str, category: str = "general") -> None:
        assert self._conn is not None
        now = time.time()
        await self._conn.execute(
            """
            INSERT INTO user_memories (key_fact, value_text, category, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key_fact) DO UPDATE SET
                value_text=excluded.value_text,
                category=excluded.category,
                updated_at=excluded.updated_at
            """,
            (key_fact.strip().lower(), value_text.strip(), category.strip(), now, now),
        )
        await self._conn.commit()

    async def get_memory(self, key_fact: str) -> Optional[str]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT value_text FROM user_memories WHERE key_fact = ?", (key_fact.strip().lower(),)
        ) as cursor:
            row = await cursor.fetchone()
            return row["value_text"] if row else None

    async def get_all_memories(self) -> List[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT id, key_fact, value_text, category, created_at, updated_at FROM user_memories ORDER BY id ASC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def delete_memory(self, key_or_id: str) -> bool:
        assert self._conn is not None
        key_or_id = key_or_id.strip()
        if key_or_id.isdigit():
            cursor = await self._conn.execute("DELETE FROM user_memories WHERE id = ?", (int(key_or_id),))
        else:
            cursor = await self._conn.execute("DELETE FROM user_memories WHERE key_fact = ?", (key_or_id.lower(),))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def clear_all_memories(self) -> None:
        assert self._conn is not None
        await self._conn.execute("DELETE FROM user_memories")
        await self._conn.commit()

    # --- Scheduled Reminders & Voice Alarms ---
    async def add_reminder(self, chat_id: int, reminder_text: str, due_timestamp: float, voice_enabled: bool = True) -> int:
        assert self._conn is not None
        now = time.time()
        cursor = await self._conn.execute(
            "INSERT INTO scheduled_reminders (chat_id, reminder_text, due_timestamp, voice_enabled, is_triggered, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (chat_id, reminder_text.strip(), due_timestamp, 1 if voice_enabled else 0, now),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_due_reminders(self) -> List[Dict[str, Any]]:
        assert self._conn is not None
        now = time.time()
        async with self._conn.execute(
            "SELECT id, chat_id, reminder_text, due_timestamp, voice_enabled FROM scheduled_reminders WHERE due_timestamp <= ? AND is_triggered = 0",
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def mark_reminder_triggered(self, reminder_id: int) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE scheduled_reminders SET is_triggered = 1 WHERE id = ?", (reminder_id,)
        )
        await self._conn.commit()

    async def get_active_reminders(self, chat_id: Optional[int] = None) -> List[Dict[str, Any]]:
        assert self._conn is not None
        now = time.time()
        query = "SELECT id, chat_id, reminder_text, due_timestamp, voice_enabled FROM scheduled_reminders WHERE is_triggered = 0 AND due_timestamp > ?"
        params = [now]
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        query += " ORDER BY due_timestamp ASC"
        async with self._conn.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def delete_reminder(self, reminder_id: int) -> bool:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM scheduled_reminders WHERE id = ?", (reminder_id,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0


# Global database instance
db = Database()
