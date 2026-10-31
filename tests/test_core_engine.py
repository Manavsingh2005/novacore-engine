"""
Tests for Core Engine: Settings, Database, Guardian Shield, and Dispatcher
"""

import asyncio
import os
import pytest
from core.config import settings
from core.database import Database
from core.ratelimiter import GuardianShield


@pytest.mark.asyncio
async def test_database_crud(tmp_path):
    test_db_path = str(tmp_path / "test.db")
    test_db = Database(db_path=test_db_path)
    await test_db.connect()

    # Settings test
    await test_db.set_setting("alive_message", "Hello World")
    val = await test_db.get_setting("alive_message")
    assert val == "Hello World"

    # Dynamic API keys test
    success, msg = await test_db.add_api_key("groq", "gsk_test1")
    assert success is True
    success2, msg2 = await test_db.add_api_key("groq", "gsk_test1")
    assert success2 is False  # Unique constraint test

    keys = await test_db.get_active_keys("groq")
    assert len(keys) == 1
    assert keys[0]["key"] == "gsk_test1"

    # Notes test
    await test_db.save_note("rules", "Be nice and respectful.")
    note = await test_db.get_note("rules")
    assert note is not None
    assert note["content"] == "Be nice and respectful."

    # AFK test
    await test_db.set_afk("Studying for exams")
    is_afk, reason, since = await test_db.get_afk_status()
    assert is_afk is True
    assert reason == "Studying for exams"

    was_afk, r, s = await test_db.clear_afk()
    assert was_afk is True
    assert r == "Studying for exams"

    is_afk2, _, _ = await test_db.get_afk_status()
    assert is_afk2 is False

    await test_db.close()


@pytest.mark.asyncio
async def test_guardian_shield_pacing():
    shield = GuardianShield()
    # First call records initial timestamp
    await shield.pace_outbound(base_delay=0.05)
    # Consecutive call should pace and sleep
    t0 = asyncio.get_event_loop().time()
    await shield.pace_outbound(base_delay=0.05)
    t1 = asyncio.get_event_loop().time()
    assert (t1 - t0) >= 0.05


def test_progress_throttle():
    shield = GuardianShield()
    msg_id = 999
    # First edit shouldn't be throttled
    assert shield.should_throttle_edit(msg_id) is False
    # Immediate second edit should be throttled
    assert shield.should_throttle_edit(msg_id) is True


@pytest.mark.asyncio
async def test_dispatcher_command_parsing():
    from core.dispatcher import client_cmd, COMMAND_REGISTRY

    received_args = []

    @client_cmd(pattern="testcmd", module="test")
    async def sample_cmd(event, args: str = ""):
        received_args.append(args)

    class DummyEvent:
        def __init__(self, text, out=True):
            self.text = text
            self.out = out
            self.reply_to_msg_id = None
            self.reply_to = None
            self.id = 123
            self.chat_id = 456
            self.sender_id = 7589422902
            self.is_private = True
            self.is_group = False
            self.is_channel = False
            self.message = self
            self.client = None

        async def edit(self, text):
            pass

    # Test exact command with arguments
    ev1 = DummyEvent(".testcmd groq gsk_my_secret_key_123")
    await sample_cmd(ev1)
    assert len(received_args) == 1
    assert received_args[0] == "groq gsk_my_secret_key_123"

    # Test exact command without arguments
    ev2 = DummyEvent(".testcmd")
    await sample_cmd(ev2)
    assert len(received_args) == 2
    assert received_args[1] == ""

    # Test command with multiline arguments
    ev3 = DummyEvent(".testcmd line1\nline2\nline3")
    await sample_cmd(ev3)
    assert len(received_args) == 3
    assert received_args[2] == "line1\nline2\nline3"

    # Test non-matching command
    ev4 = DummyEvent(".testcmdother something")
    await sample_cmd(ev4)
    assert len(received_args) == 3  # Did not trigger

