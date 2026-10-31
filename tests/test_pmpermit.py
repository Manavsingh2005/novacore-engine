"""
Tests for Smart Conversational PM-Permit & Anti-Report Shield (Phase 4)
"""

import time
import pytest
from core.database import Database


@pytest.mark.asyncio
async def test_pmpermit_database_lifecycle(tmp_path):
    test_db = Database(db_path=str(tmp_path / "test_pm.db"))
    await test_db.connect()

    # 1. Test Toggle
    await test_db.set_setting("pmpermit_enabled", "true")
    assert await test_db.get_setting("pmpermit_enabled") == "true"

    await test_db.set_setting("pmpermit_enabled", "false")
    assert await test_db.get_setting("pmpermit_enabled") == "false"

    # 2. Test Approval Lifecycle
    test_user_id = 987654321
    assert await test_db.is_pm_approved(test_user_id) is False

    await test_db.approve_pm(test_user_id)
    assert await test_db.is_pm_approved(test_user_id) is True

    # 3. Test Disapproval
    await test_db.disapprove_pm(test_user_id)
    assert await test_db.is_pm_approved(test_user_id) is False

    # 4. Test 24-Hour Rate Limiting
    stranger_id = 11223344
    count1 = await test_db.record_pm_interaction(stranger_id)
    assert count1 == 1

    last_time = await test_db.get_pm_last_interaction(stranger_id)
    assert last_time > 0
    now = time.time()
    assert (now - last_time) < 5  # Interacted just now

    # Within 24 hours (86400s), subsequent messages must be silenced
    elapsed = now - last_time
    assert elapsed < 86400

    # 5. Test Approved Users List & Stats
    await test_db.approve_pm(1001)
    await test_db.approve_pm(1002)

    approved_list = await test_db.get_approved_users()
    assert len(approved_list) == 2
    uids = [u["user_id"] for u in approved_list]
    assert 1001 in uids and 1002 in uids

    stats = await test_db.get_pm_stats()
    assert stats["approved"] == 2
    assert stats["pending"] >= 1  # stranger_id was pending

    # 6. Test Custom PM Message & AI Mode Settings
    await test_db.set_setting("custom_pm_message", "Hello from vacation!")
    assert await test_db.get_setting("custom_pm_message") == "Hello from vacation!"

    await test_db.set_setting("pmpermit_ai", "true")
    assert await test_db.get_setting("pmpermit_ai") == "true"

    await test_db.close()
