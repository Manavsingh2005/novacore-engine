"""
Tests for System Suite & Tier-Adaptive Formatters (Phase 3)
"""

import time
import pytest
from helpers.formatters import (
    format_uptime,
    format_bytes,
    format_progress_bar,
    get_dc_location,
    make_card,
)
from core.database import Database


def test_formatters():
    # Uptime test
    t0 = time.time() - 3665  # 1h 1m 5s
    up = format_uptime(t0)
    assert "1h" in up and "1m" in up

    # Bytes test
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1024 * 1024 * 2) == "2.00 MB"
    assert format_bytes(1024 * 1024 * 1024 * 3) == "3.00 GB"

    # Progress bar test
    bar = format_progress_bar(50.0, length=10)
    assert "█████░░░░░" in bar
    assert "50%" in bar

    # DC Location test
    assert "Singapore" in get_dc_location(5)
    assert "Amsterdam" in get_dc_location(2)
    assert "Miami" in get_dc_location(1)

    # Card test
    card = make_card(
        title="Test Card",
        fields=[("Field 1", "Value 1"), ("Field 2", "Value 2")],
        footer="Test Footer",
    )
    assert "Test Card" in card
    assert "━━━━━━━━━━━━━━━━━━━━" in card
    assert "Field 1" in card
    assert "Test Footer" in card


@pytest.mark.asyncio
async def test_alive_custom_footer(tmp_path):
    test_db = Database(db_path=str(tmp_path / "test_alive.db"))
    await test_db.connect()

    # Default None
    footer = await test_db.get_setting("custom_alive_footer")
    assert footer is None

    # Custom footer
    await test_db.set_setting("custom_alive_footer", "Hacking the Matrix...")
    updated = await test_db.get_setting("custom_alive_footer")
    assert updated == "Hacking the Matrix..."

    await test_db.close()
