"""
Tests for High-Speed Media Suite & Modern Utilities (Phase 5)
"""

import io
import time
import pytest
from PIL import Image
from plugins.media.kang import resize_image_for_sticker
from core.database import Database


def test_sticker_resize_proportions():
    # 1. Test Wide Image (1000x500) -> should become 512x256
    img_wide = Image.new("RGBA", (1000, 500), color="blue")
    buf = io.BytesIO()
    img_wide.save(buf, format="PNG")
    res_buf = resize_image_for_sticker(buf.getvalue())

    res_img = Image.open(res_buf)
    assert res_img.size == (512, 256)
    assert res_img.format == "PNG"

    # 2. Test Tall Image (400x800) -> should become 256x512
    img_tall = Image.new("RGBA", (400, 800), color="red")
    buf2 = io.BytesIO()
    img_tall.save(buf2, format="PNG")
    res_buf2 = resize_image_for_sticker(buf2.getvalue())

    res_img2 = Image.open(res_buf2)
    assert res_img2.size == (256, 512)
    assert res_img2.format == "PNG"


@pytest.mark.asyncio
async def test_afk_and_notes_lifecycle(tmp_path):
    test_db = Database(db_path=str(tmp_path / "test_utils.db"))
    await test_db.connect()

    # AFK lifecycle with mentions tracking
    await test_db.set_afk("Writing code")
    is_afk, reason, afk_since = await test_db.get_afk_status()
    assert is_afk is True
    assert reason == "Writing code"

    await test_db.set_setting("afk_mentions_count", "5")
    assert await test_db.get_setting("afk_mentions_count") == "5"

    was_afk, r, s = await test_db.clear_afk()
    assert was_afk is True
    assert r == "Writing code"

    # Notes lifecycle
    await test_db.save_note("meeting_link", "https://meet.google.com/abc-defg-hij")
    note = await test_db.get_note("meeting_link")
    assert note is not None
    assert "meet.google.com" in note["content"]

    notes_list = await test_db.list_notes()
    assert "meeting_link" in notes_list

    deleted = await test_db.delete_note("meeting_link")
    assert deleted is True
    assert await test_db.get_note("meeting_link") is None

    await test_db.close()


def test_purge_chunking():
    # Verify 100-batch safety chunking
    items = list(range(250))
    chunk_size = 100
    chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

    assert len(chunks) == 3
    assert len(chunks[0]) == 100
    assert len(chunks[1]) == 100
    assert len(chunks[2]) == 50


def test_tts_voices_mapping():
    from plugins.media.tts import VOICE_MAP, DEFAULT_VOICE
    assert "narrator" in VOICE_MAP
    assert "guy" in VOICE_MAP
    assert "jenny" in VOICE_MAP
    assert DEFAULT_VOICE == "en-US-ChristopherNeural"


@pytest.mark.asyncio
async def test_ephemeral_database_lifecycle(tmp_path):
    test_db = Database(db_path=str(tmp_path / "test_ephemeral.db"))
    await test_db.connect()

    # Schedule deletion in past/immediate
    await test_db.schedule_ephemeral_deletion(chat_id=123, message_id=456, delete_after_seconds=-5)
    due = await test_db.get_due_ephemeral_deletions()
    assert len(due) == 1
    rec_id, cid, mid = due[0]
    assert cid == 123
    assert mid == 456

    # Remove record
    await test_db.remove_ephemeral_records([rec_id])
    due_after = await test_db.get_due_ephemeral_deletions()
    assert len(due_after) == 0

    await test_db.close()

