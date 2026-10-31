import pytest
import time
from core.database import Database
from plugins.utility.reminder import parse_time_offset


@pytest.mark.asyncio
async def test_memory_lifecycle(tmp_path):
    db_file = tmp_path / "test_memory.db"
    test_db = Database(str(db_file))
    await test_db.connect()

    # Add memory
    await test_db.add_or_update_memory("dog_name", "Dogo", "personal")
    await test_db.add_or_update_memory("timezone", "IST", "preference")

    # Get memory
    val = await test_db.get_memory("dog_name")
    assert val == "Dogo"

    # Update memory
    await test_db.add_or_update_memory("dog_name", "Dogo The Great", "personal")
    val_updated = await test_db.get_memory("dog_name")
    assert val_updated == "Dogo The Great"

    # List all
    all_mem = await test_db.get_all_memories()
    assert len(all_mem) == 2

    # Delete memory
    del_ok = await test_db.delete_memory("timezone")
    assert del_ok is True
    assert await test_db.get_memory("timezone") is None

    await test_db.close()


@pytest.mark.asyncio
async def test_reminder_scheduling(tmp_path):
    db_file = tmp_path / "test_reminder.db"
    test_db = Database(str(db_file))
    await test_db.connect()

    now = time.time()
    due_future = now + 600  # in 10m
    due_past = now - 5     # already due

    id_future = await test_db.add_reminder(12345, "Future Task", due_future, voice_enabled=True)
    id_past = await test_db.add_reminder(12345, "Past Task", due_past, voice_enabled=True)

    # Check due reminders
    due_list = await test_db.get_due_reminders()
    assert len(due_list) == 1
    assert due_list[0]["id"] == id_past
    assert due_list[0]["reminder_text"] == "Past Task"

    # Mark triggered
    await test_db.mark_reminder_triggered(id_past)
    due_list_after = await test_db.get_due_reminders()
    assert len(due_list_after) == 0

    # Active reminders
    active = await test_db.get_active_reminders(chat_id=12345)
    assert len(active) == 1
    assert active[0]["id"] == id_future

    await test_db.close()


def test_time_parsing():
    now = time.time()
    t_10m = parse_time_offset("in 10m")
    assert t_10m is not None
    assert 595 <= (t_10m - now) <= 605

    t_2h = parse_time_offset("2h")
    assert t_2h is not None
    assert 7195 <= (t_2h - now) <= 7205

    t_30s = parse_time_offset("30s")
    assert t_30s is not None
    assert 25 <= (t_30s - now) <= 35
