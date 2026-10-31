"""
Tests for AI Pool: Round-Robin Selection, 429 Failover, Model Switching & Audio Translation
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from groq import RateLimitError
from core.ai_pool import AIPoolManager, KeyState
from core.database import Database


@pytest.mark.asyncio
async def test_ai_pool_key_rotation():
    pool = AIPoolManager()
    k1 = KeyState(1, "gsk_key1")
    k2 = KeyState(2, "gsk_key2")
    pool._keys = [k1, k2]

    # Test round robin
    next1 = pool._get_next_key()
    next2 = pool._get_next_key()
    next3 = pool._get_next_key()

    assert next1.key_id == 1
    assert next2.key_id == 2
    assert next3.key_id == 1


@pytest.mark.asyncio
async def test_ai_pool_429_failover(tmp_path):
    # Setup test DB to handle increment_key_usage
    test_db = Database(db_path=str(tmp_path / "test_ai.db"))
    await test_db.connect()

    with patch("core.ai_pool.db", test_db):
        pool = AIPoolManager()
        k1 = KeyState(1, "gsk_key1")
        k2 = KeyState(2, "gsk_key2")
        pool._keys = [k1, k2]

        # Mock Key 1 throwing RateLimitError, Key 2 succeeding
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Success from Key 2"
        mock_resp.choices = [mock_choice]

        dummy_resp = MagicMock()
        dummy_resp.status_code = 429
        dummy_resp.headers = {}

        k1.client.chat.completions.create = AsyncMock(
            side_effect=RateLimitError("Rate limit reached", response=dummy_resp, body={})
        )
        k2.client.chat.completions.create = AsyncMock(return_value=mock_resp)

        # Perform chat completion
        res = await pool.chat_completion([{"role": "user", "content": "hello"}])
        assert res == "Success from Key 2"
        assert k1.cooldown_until > 0

    await test_db.close()


@pytest.mark.asyncio
async def test_dynamic_model_switching(tmp_path):
    test_db = Database(db_path=str(tmp_path / "test_model.db"))
    await test_db.connect()

    with patch("core.ai_pool.db", test_db):
        pool = AIPoolManager()
        # Default model
        current = await pool.get_current_model()
        assert "qwen" in current or "llama" in current

        # Switch model
        await pool.set_current_model("openai/gpt-oss-120b")
        updated = await pool.get_current_model()
        assert updated == "openai/gpt-oss-120b"

    await test_db.close()


@pytest.mark.asyncio
async def test_list_models():
    pool = AIPoolManager()
    k1 = KeyState(1, "gsk_key1")
    pool._keys = [k1]

    mock_model_1 = MagicMock()
    mock_model_1.id = "llama-3.3-70b-versatile"
    mock_model_2 = MagicMock()
    mock_model_2.id = "whisper-large-v3"

    mock_models_page = MagicMock()
    mock_models_page.data = [mock_model_1, mock_model_2]

    k1.client.models.list = AsyncMock(return_value=mock_models_page)
    models = await pool.list_available_models()
    assert "llama-3.3-70b-versatile" in models
    assert "whisper-large-v3" in models
