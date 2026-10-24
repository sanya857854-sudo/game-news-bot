"""
Unit tests for gamebot.config module.
Validates boolean/integer parsing, environment loading, and BotConfig validation rules.
"""

import os
import pytest
from gamebot.config import (
    BotConfig,
    ConfigurationError,
    load_config,
    _parse_bool,
    _parse_int,
    _read_env_file,
)


class TestParseBool:
    """Tests for _parse_bool helper."""

    def test_truthy_strings(self):
        for val in ("true", "True", "TRUE", "1", "yes", "YES", "on", "t", "y"):
            assert _parse_bool(val) is True

    def test_falsy_strings(self):
        for val in ("false", "False", "FALSE", "0", "no", "NO", "off", "f", "n", ""):
            assert _parse_bool(val) is False

    def test_boolean_and_numeric_inputs(self):
        assert _parse_bool(True) is True
        assert _parse_bool(False) is False
        assert _parse_bool(1) is True
        assert _parse_bool(0) is False

    def test_none_and_defaults(self):
        assert _parse_bool(None, default=True) is True
        assert _parse_bool(None, default=False) is False
        assert _parse_bool("invalid_choice", default=True) is True
        assert _parse_bool("invalid_choice", default=False) is False


class TestParseInt:
    """Tests for _parse_int helper."""

    def test_valid_integers(self):
        assert _parse_int("123456") == 123456
        assert _parse_int(" 987654321 ") == 987654321
        assert _parse_int("-42") == -42
        assert _parse_int(999) == 999
        assert _parse_int(0) == 0

    def test_none_and_empty(self):
        assert _parse_int(None) is None
        assert _parse_int(None, default=100) == 100
        assert _parse_int("") is None
        assert _parse_int("   ", default=50) == 50

    def test_invalid_string_raises(self):
        with pytest.raises(ConfigurationError, match="Expected integer"):
            _parse_int("not_a_number")

        with pytest.raises(ConfigurationError, match="Expected integer"):
            _parse_int("12.34")


class TestBotConfigValidation:
    """Tests for BotConfig.validate constraints."""

    def test_valid_config_passes(self):
        cfg = BotConfig(
            bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            channel_id="@gameworld2029",
            admin_telegram_id=987654321,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="sk-test-ai-key",
            ai_model="gpt-4o-mini",
            database_path="bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@gameworld2029",
            video_output_dir="temp/shorts",
        )
        # Should not raise
        cfg.validate()

    def test_missing_bot_token_raises(self):
        cfg = BotConfig(
            bot_token="",
            channel_id="@gameworld2029",
            admin_telegram_id=987654321,
            send_shorts_to_admin=True,
            enable_evergreen=False,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path="bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@gameworld2029",
            video_output_dir="temp/shorts",
        )
        with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN is required"):
            cfg.validate()

    def test_missing_channel_id_raises(self):
        cfg = BotConfig(
            bot_token="valid_token",
            channel_id="",
            admin_telegram_id=987654321,
            send_shorts_to_admin=True,
            enable_evergreen=False,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path="bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@gameworld2029",
            video_output_dir="temp/shorts",
        )
        with pytest.raises(ConfigurationError, match="TELEGRAM_CHANNEL_ID is required"):
            cfg.validate()

    def test_missing_admin_id_when_shorts_enabled_raises(self):
        cfg = BotConfig(
            bot_token="valid_token",
            channel_id="@gameworld2029",
            admin_telegram_id=None,
            send_shorts_to_admin=True,
            enable_evergreen=False,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path="bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@gameworld2029",
            video_output_dir="temp/shorts",
        )
        with pytest.raises(
            ConfigurationError,
            match="ADMIN_TELEGRAM_ID is required when SEND_SHORTS_TO_ADMIN=true",
        ):
            cfg.validate()

    def test_missing_admin_id_when_shorts_disabled_passes(self):
        cfg = BotConfig(
            bot_token="valid_token",
            channel_id="@gameworld2029",
            admin_telegram_id=None,
            send_shorts_to_admin=False,
            enable_evergreen=False,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path="bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@gameworld2029",
            video_output_dir="temp/shorts",
        )
        cfg.validate()

    def test_missing_ai_key_when_evergreen_enabled_raises(self):
        cfg = BotConfig(
            bot_token="valid_token",
            channel_id="@gameworld2029",
            admin_telegram_id=987654321,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path="bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@gameworld2029",
            video_output_dir="temp/shorts",
        )
        with pytest.raises(
            ConfigurationError,
            match="AI_API_KEY is required when ENABLE_EVERGREEN=true",
        ):
            cfg.validate()

    def test_missing_ai_key_when_evergreen_disabled_passes(self):
        cfg = BotConfig(
            bot_token="valid_token",
            channel_id="@gameworld2029",
            admin_telegram_id=987654321,
            send_shorts_to_admin=True,
            enable_evergreen=False,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path="bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@gameworld2029",
            video_output_dir="temp/shorts",
        )
        cfg.validate()

    def test_multiple_errors_aggregated(self):
        cfg = BotConfig(
            bot_token="",
            channel_id="",
            admin_telegram_id=None,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path="bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@gameworld2029",
            video_output_dir="temp/shorts",
        )
        with pytest.raises(ConfigurationError) as exc_info:
            cfg.validate()
        msg = str(exc_info.value)
        assert "TELEGRAM_BOT_TOKEN is required" in msg
        assert "TELEGRAM_CHANNEL_ID is required" in msg
        assert "ADMIN_TELEGRAM_ID is required" in msg
        assert "AI_API_KEY is required" in msg


class TestLoadConfig:
    """Tests for load_config from environment and custom .env files."""

    def test_load_from_environment(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mock_tok_123")
        monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@test_chan")
        monkeypatch.setenv("ADMIN_TELEGRAM_ID", "11223344")
        monkeypatch.setenv("SEND_SHORTS_TO_ADMIN", "true")
        monkeypatch.setenv("ENABLE_EVERGREEN", "false")
        monkeypatch.setenv("AI_API_KEY", "custom_key")
        monkeypatch.setenv("DATABASE_PATH", "custom_bot.db")
        monkeypatch.setenv("FFMPEG_PATH", "custom_ffmpeg")
        monkeypatch.setenv("WATERMARK_TEXT", "@my_mark")

        cfg = load_config()
        assert cfg.bot_token == "mock_tok_123"
        assert cfg.channel_id == "@test_chan"
        assert cfg.admin_telegram_id == 11223344
        assert cfg.send_shorts_to_admin is True
        assert cfg.enable_evergreen is False
        assert cfg.ai_api_key == "custom_key"
        assert cfg.database_path == "custom_bot.db"
        assert cfg.ffmpeg_path == "custom_ffmpeg"
        assert cfg.watermark_text == "@my_mark"

    def test_load_from_custom_env_file(self, tmp_path):
        env_file = tmp_path / "custom.env"
        env_file.write_text(
            """
# Custom test environment
TELEGRAM_BOT_TOKEN=file_bot_token
TELEGRAM_CHANNEL_ID=@file_channel
ADMIN_TELEGRAM_ID=77889900
SEND_SHORTS_TO_ADMIN=false
ENABLE_EVERGREEN=true
AI_API_KEY=file_ai_key
AI_PROVIDER=gemini
AI_MODEL=gemini-1.5-flash
DATABASE_PATH=test_dir/custom.db
WATERMARK_TEXT=@custom_watermark
""",
            encoding="utf-8",
        )

        cfg = load_config(env_path=str(env_file))
        assert cfg.bot_token == "file_bot_token"
        assert cfg.channel_id == "@file_channel"
        assert cfg.admin_telegram_id == 77889900
        assert cfg.send_shorts_to_admin is False
        assert cfg.enable_evergreen is True
        assert cfg.ai_api_key == "file_ai_key"
        assert cfg.ai_provider == "gemini"
        assert cfg.ai_model == "gemini-1.5-flash"
        assert cfg.database_path == "test_dir/custom.db"
        assert cfg.watermark_text == "@custom_watermark"

    def test_read_env_file_helper(self, tmp_path, monkeypatch):
        test_file = tmp_path / "test_read.env"
        test_file.write_text(
            """
KEY_PLAIN=simple_value
KEY_DOUBLE="quoted_val"
KEY_SINGLE='single_quoted'
# Commented line
KEY_EMPTY=
""",
            encoding="utf-8",
        )
        monkeypatch.delenv("KEY_PLAIN", raising=False)
        monkeypatch.delenv("KEY_DOUBLE", raising=False)
        monkeypatch.delenv("KEY_SINGLE", raising=False)

        _read_env_file(str(test_file), override=True)
        assert os.environ.get("KEY_PLAIN") == "simple_value"
        assert os.environ.get("KEY_DOUBLE") == "quoted_val"
        assert os.environ.get("KEY_SINGLE") == "single_quoted"


def test_parse_bool_standalone():
    assert _parse_bool("true") is True
    assert _parse_bool("True") is True
    assert _parse_bool("1") is True
    assert _parse_bool("false") is False
    assert _parse_bool("0") is False
    assert _parse_bool(None, default=True) is True


def test_parse_int_standalone():
    assert _parse_int("12345") == 12345
    assert _parse_int(None) is None
    with pytest.raises(ConfigurationError):
        _parse_int("not_a_number")


def test_config_validation_success(mock_env):
    cfg = load_config()
    cfg.validate()
    assert cfg.admin_telegram_id == 987654321
    assert cfg.send_shorts_to_admin is True
    assert cfg.enable_evergreen is True


def test_config_validation_missing_admin(mock_env, monkeypatch):
    monkeypatch.delenv("ADMIN_TELEGRAM_ID", raising=False)
    monkeypatch.setenv("SEND_SHORTS_TO_ADMIN", "true")
    cfg = load_config()
    with pytest.raises(ConfigurationError, match="ADMIN_TELEGRAM_ID is required"):
        cfg.validate()


def test_config_validation_missing_ai_key(mock_env, monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("ENABLE_EVERGREEN", "true")
    cfg = load_config()
    with pytest.raises(ConfigurationError, match="AI_API_KEY is required"):
        cfg.validate()

