"""
Tests for Safe Session Generator & .env Updater (Phase 7)
"""

import os
from unittest.mock import patch
import pytest
from session_generator import update_env_file


def test_update_env_file(tmp_path):
    test_env = tmp_path / ".env"
    test_env.write_text("API_ID=111\nAPI_HASH=oldhash\nGROQ_API_KEYS=key1,key2\n", encoding="utf-8")

    with patch("session_generator.ENV_FILE", test_env):
        update_env_file(api_id=999999, api_hash="newhash1234567890", string_session="1BJWTESTSESSIONSTRING==")

    content = test_env.read_text(encoding="utf-8")
    assert "API_ID=999999" in content
    assert "API_HASH=newhash1234567890" in content
    assert "STRING_SESSION=1BJWTESTSESSIONSTRING==" in content
    assert "GROQ_API_KEYS=key1,key2" in content
