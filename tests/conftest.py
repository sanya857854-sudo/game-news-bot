"""
Pytest configuration for Game News Bot.
Adds project root directory to sys.path and provides shared fixtures across test suites.
"""

import os
import sys
import pytest

# Insert project root into sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from gamebot.config import BotConfig, load_config


@pytest.fixture
def mock_env(monkeypatch, tmp_path):
    """Set up controlled environment variables for unit tests."""
    db_file = tmp_path / "test_conftest_bot.db"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:MOCK_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@test_channel")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "987654321")
    monkeypatch.setenv("SEND_SHORTS_TO_ADMIN", "true")
    monkeypatch.setenv("ENABLE_EVERGREEN", "true")
    monkeypatch.setenv("AI_API_KEY", "test-ai-key")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    monkeypatch.setenv("FFMPEG_PATH", "ffmpeg")
    monkeypatch.setenv("WATERMARK_TEXT", "@gameworld2029")
    monkeypatch.setenv("VIDEO_OUTPUT_DIR", str(tmp_path / "shorts"))
    return {
        "db_path": str(db_file),
        "admin_id": 987654321,
    }


@pytest.fixture
def test_config(mock_env):
    """Returns initialized BotConfig instance from mock_env."""
    return load_config()
