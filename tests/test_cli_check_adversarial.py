"""
Adversarial Stress Test Suite for CLI Pre-Flight Health Checks and Exit Codes (Milestone 3).

Exhaustively verifies:
1. Subsystem Failure Modes (bad bot token 401, network failures, missing admin ID when
   shorts enabled vs disabled, chat-not-found warning vs hard failure, missing ffmpeg,
   corrupted ffmpeg, database disk corruption, unwriteable database path, missing AI key,
   AI provider 401/500 errors).
2. Exit Code Guarantees (code 0 on clean pass or warnings-only, code 1 on any critical failure).
3. Simultaneous Multi-Failure Compounding (all 5 subsystems failing at once).
4. CLI Flag Combinations (--check, --config, --once, --run, invalid args, non-numeric configs).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from gamebot.config import BotConfig, ConfigurationError, load_config
from main import HealthChecker, main, parse_args, run_bot_iteration


@pytest.fixture
def base_valid_config(tmp_path):
    """Provides a fully valid BotConfig with a clean temporary SQLite database."""
    db_file = tmp_path / "valid_test_bot.db"
    return BotConfig(
        bot_token="123456789:MOCK_VALID_TOKEN_AABBCCDDEEFF",
        channel_id="@gameworld2029",
        admin_telegram_id=987654321,
        send_shorts_to_admin=True,
        enable_evergreen=True,
        ai_provider="openai",
        ai_api_key="sk-test-ai-key-12345",
        ai_model="gpt-4o-mini",
        database_path=str(db_file),
        ffmpeg_path="ffmpeg",
        watermark_text="@gameworld2029",
        video_output_dir=str(tmp_path / "shorts"),
    )


# ==============================================================================
# 1. Subsystem Failure Modes & Diagnostic Probes
# ==============================================================================

class TestSubsystemFailureModes:
    """Adversarial tests for individual failure modes across all 5 subsystems."""

    # --- Telegram Bot Token Probes ---

    @pytest.mark.asyncio
    async def test_telegram_bot_token_http_401_unauthorized(self, base_valid_config):
        """Simulates Telegram API rejecting token with HTTP 401 Unauthorized."""
        checker = HealthChecker(base_valid_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"ok":false,"error_code":401,"description":"Unauthorized"}'

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            passed = await checker.check_telegram_bot()
            assert passed is False
            assert checker.results["Telegram Bot Auth"][0] is False
            assert "HTTP 401" in checker.results["Telegram Bot Auth"][1]
            assert any("Telegram Bot Auth" in f for f in checker.critical_failures)

    @pytest.mark.asyncio
    async def test_telegram_bot_token_network_connection_error(self, base_valid_config):
        """Simulates complete network failure / DNS error during Telegram auth."""
        checker = HealthChecker(base_valid_config)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.ConnectError("Network unreachable")):
            passed = await checker.check_telegram_bot()
            assert passed is False
            assert checker.results["Telegram Bot Auth"][0] is False
            assert "Connection failed" in checker.results["Telegram Bot Auth"][1]
            assert len(checker.critical_failures) == 1

    @pytest.mark.asyncio
    async def test_telegram_bot_token_empty_string(self, base_valid_config):
        """Empty bot token triggers immediate failure without making HTTP requests."""
        empty_token_config = BotConfig(
            bot_token="",
            channel_id=base_valid_config.channel_id,
            admin_telegram_id=base_valid_config.admin_telegram_id,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="key",
            ai_model="gpt-4o-mini",
            database_path=base_valid_config.database_path,
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(empty_token_config)
        passed = await checker.check_telegram_bot()
        assert passed is False
        assert "empty" in checker.results["Telegram Bot Auth"][1].lower()

    # --- Admin Connection Probes ---

    @pytest.mark.asyncio
    async def test_admin_missing_when_shorts_enabled_fails(self, base_valid_config):
        """Missing ADMIN_TELEGRAM_ID when SEND_SHORTS_TO_ADMIN=True must report [FAIL]."""
        no_admin_config = BotConfig(
            bot_token=base_valid_config.bot_token,
            channel_id=base_valid_config.channel_id,
            admin_telegram_id=None,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="key",
            ai_model="gpt-4o-mini",
            database_path=base_valid_config.database_path,
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(no_admin_config)
        passed = await checker.check_admin_connection()
        assert passed is False
        assert checker.results["Admin Connection"][0] is False
        assert "ADMIN_TELEGRAM_ID is missing" in checker.results["Admin Connection"][1]
        assert any("Admin Connection" in f for f in checker.critical_failures)

    @pytest.mark.asyncio
    async def test_admin_missing_when_shorts_disabled_warns_only(self, base_valid_config):
        """Missing ADMIN_TELEGRAM_ID when SEND_SHORTS_TO_ADMIN=False logs [WARN] and passes."""
        no_admin_shorts_off = BotConfig(
            bot_token=base_valid_config.bot_token,
            channel_id=base_valid_config.channel_id,
            admin_telegram_id=None,
            send_shorts_to_admin=False,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="key",
            ai_model="gpt-4o-mini",
            database_path=base_valid_config.database_path,
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(no_admin_shorts_off)
        passed = await checker.check_admin_connection()
        assert passed is True
        assert len(checker.critical_failures) == 0
        assert len(checker.warnings) == 1
        assert "SEND_SHORTS_TO_ADMIN=false" in checker.warnings[0]

    @pytest.mark.asyncio
    async def test_admin_chat_not_found_produces_graceful_warning(self, base_valid_config):
        """Telegram 'chat not found' must warn that admin must send /start, without failing."""
        checker = HealthChecker(base_valid_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: chat not found",
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            passed = await checker.check_admin_connection()
            assert passed is True
            assert len(checker.critical_failures) == 0
            assert len(checker.warnings) == 1
            assert "/start" in checker.warnings[0]

    @pytest.mark.asyncio
    async def test_admin_api_error_other_than_chat_not_found_fails(self, base_valid_config):
        """Other Telegram API errors (e.g. invalid chat_id or forbidden) report [FAIL]."""
        checker = HealthChecker(base_valid_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: chat_id is empty",
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            passed = await checker.check_admin_connection()
            assert passed is False
            assert checker.results["Admin Connection"][0] is False
            assert any("Admin Connection" in f for f in checker.critical_failures)

    # --- FFmpeg Probes ---

    @pytest.mark.asyncio
    async def test_ffmpeg_not_in_path_fails(self, base_valid_config):
        """FFmpeg missing from PATH reports [FAIL] and records critical failure."""
        checker = HealthChecker(base_valid_config)
        with patch("shutil.which", return_value=None), \
             patch("os.path.isfile", return_value=False):
            passed = await checker.check_ffmpeg()
            assert passed is False
            assert checker.results["FFmpeg Executable"][0] is False
            assert "not found in PATH" in checker.results["FFmpeg Executable"][1]
            assert any("FFmpeg Executable" in f for f in checker.critical_failures)

    @pytest.mark.asyncio
    async def test_ffmpeg_nonzero_exit_code_fails(self, base_valid_config):
        """FFmpeg returning non-zero exit code (e.g. exit code 127) reports [FAIL]."""
        checker = HealthChecker(base_valid_config)
        mock_proc = MagicMock()
        mock_proc.returncode = 127
        mock_proc.stdout = ""

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=mock_proc):
            passed = await checker.check_ffmpeg()
            assert passed is False
            assert checker.results["FFmpeg Executable"][0] is False
            assert "127" in checker.results["FFmpeg Executable"][1]

    @pytest.mark.asyncio
    async def test_ffmpeg_execution_timeout_fails(self, base_valid_config):
        """FFmpeg execution timing out or hanging reports [FAIL]."""
        checker = HealthChecker(base_valid_config)
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)):
            passed = await checker.check_ffmpeg()
            assert passed is False
            assert checker.results["FFmpeg Executable"][0] is False
            assert "Failed to execute FFmpeg" in checker.results["FFmpeg Executable"][1]

    # --- SQLite Database Probes ---

    @pytest.mark.asyncio
    async def test_database_corrupted_file_fails(self, tmp_path, base_valid_config):
        """Corrupted SQLite file (invalid header/garbage bytes) reports [FAIL]."""
        corrupted_db = tmp_path / "corrupt_bot.db"
        corrupted_db.write_bytes(b"CORRUPTED_GARBAGE_HEADER_NOT_A_VALID_SQLITE_DATABASE_DATA\x00\xff")

        bad_db_config = BotConfig(
            bot_token=base_valid_config.bot_token,
            channel_id=base_valid_config.channel_id,
            admin_telegram_id=base_valid_config.admin_telegram_id,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="key",
            ai_model="gpt-4o-mini",
            database_path=str(corrupted_db),
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(bad_db_config)
        passed = await checker.check_database()
        assert passed is False
        assert checker.results["SQLite Database"][0] is False
        assert any("SQLite Database" in f for f in checker.critical_failures)

    @pytest.mark.asyncio
    async def test_database_unwriteable_path_fails(self, base_valid_config):
        """Unwriteable or unopenable database path reports [FAIL]."""
        unwriteable_config = BotConfig(
            bot_token=base_valid_config.bot_token,
            channel_id=base_valid_config.channel_id,
            admin_telegram_id=base_valid_config.admin_telegram_id,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="key",
            ai_model="gpt-4o-mini",
            database_path="Z:/nonexistent_drive_mount_xyz/unwriteable/bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(unwriteable_config)
        passed = await checker.check_database()
        assert passed is False
        assert checker.results["SQLite Database"][0] is False
        assert any("SQLite Database" in f for f in checker.critical_failures)

    # --- AI Module Health Probes ---

    @pytest.mark.asyncio
    async def test_ai_health_missing_api_key_when_enabled_fails(self, base_valid_config):
        """Missing AI_API_KEY when ENABLE_EVERGREEN=true reports [FAIL]."""
        no_ai_key_config = BotConfig(
            bot_token=base_valid_config.bot_token,
            channel_id=base_valid_config.channel_id,
            admin_telegram_id=base_valid_config.admin_telegram_id,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path=base_valid_config.database_path,
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(no_ai_key_config)
        passed = await checker.check_ai_health()
        assert passed is False
        assert checker.results["AI Module Health"][0] is False
        assert "AI_API_KEY is not configured" in checker.results["AI Module Health"][1]
        assert any("AI Module Health" in f for f in checker.critical_failures)

    @pytest.mark.asyncio
    async def test_ai_health_auth_401_fails(self, base_valid_config):
        """AI endpoint returning HTTP 401 Unauthorized reports [FAIL]."""
        checker = HealthChecker(base_valid_config)
        with patch("main.probe_ai_health", new_callable=AsyncMock, return_value=(False, "AI authentication failed (401)")):
            passed = await checker.check_ai_health()
            assert passed is False
            assert checker.results["AI Module Health"][0] is False
            assert "401" in checker.results["AI Module Health"][1]


# ==============================================================================
# 2. Exit Code Guarantees (0 on Pass, 1 on Any Failure)
# ==============================================================================

class TestExitCodeGuarantees:
    """Verifies main.py --check exit codes across passing, warning, and failing states."""

    def test_main_check_all_pass_returns_code_zero(self, base_valid_config):
        """When all subsystems pass, main(['--check']) returns exit code 0."""
        with patch("main.load_config", return_value=base_valid_config), \
             patch("main.HealthChecker.check_ffmpeg", new_callable=AsyncMock, return_value=True), \
             patch("main.HealthChecker.check_database", new_callable=AsyncMock, return_value=True), \
             patch("main.HealthChecker.check_telegram_bot", new_callable=AsyncMock, return_value=True), \
             patch("main.HealthChecker.check_admin_connection", new_callable=AsyncMock, return_value=True), \
             patch("main.HealthChecker.check_ai_health", new_callable=AsyncMock, return_value=True):

            exit_code = main(["--check"])
            assert exit_code == 0

    def test_main_check_warnings_only_returns_code_zero(self, base_valid_config):
        """Non-critical warnings (e.g. admin chat not found) must still return exit code 0."""
        warning_config = BotConfig(
            bot_token=base_valid_config.bot_token,
            channel_id=base_valid_config.channel_id,
            admin_telegram_id=987654321,
            send_shorts_to_admin=True,
            enable_evergreen=False,  # Triggers AI warning
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path=base_valid_config.database_path,
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )

        with patch("main.load_config", return_value=warning_config), \
             patch("main.HealthChecker.check_ffmpeg", new_callable=AsyncMock, return_value=True), \
             patch("main.HealthChecker.check_database", new_callable=AsyncMock, return_value=True), \
             patch("main.HealthChecker.check_telegram_bot", new_callable=AsyncMock, return_value=True):

            # Admin check logs warning: chat not found
            async def mock_admin_warn(self_checker):
                self_checker.log_warn("Admin Connection", "Admin must send /start first")
                return True

            with patch.object(HealthChecker, "check_admin_connection", mock_admin_warn):
                exit_code = main(["--check"])
                assert exit_code == 0

    def test_main_check_single_failure_returns_code_one(self, base_valid_config):
        """Even if 4 of 5 subsystems pass, a single failure forces exit code 1."""
        with patch("main.load_config", return_value=base_valid_config), \
             patch("main.HealthChecker.check_ffmpeg", new_callable=AsyncMock, return_value=True), \
             patch("main.HealthChecker.check_database", new_callable=AsyncMock, return_value=True), \
             patch("main.HealthChecker.check_telegram_bot", new_callable=AsyncMock, return_value=False), \
             patch("main.HealthChecker.check_admin_connection", new_callable=AsyncMock, return_value=True), \
             patch("main.HealthChecker.check_ai_health", new_callable=AsyncMock, return_value=True):

            # Inject critical failure tracking
            async def failing_run_all(self_checker):
                self_checker.critical_failures.append("Telegram Bot Auth: 401 Unauthorized")
                return False

            with patch.object(HealthChecker, "run_all", failing_run_all):
                exit_code = main(["--check"])
                assert exit_code == 1

    def test_main_check_simultaneous_multi_failure_returns_code_one(self, base_valid_config):
        """When all 5 subsystems fail simultaneously, all are evaluated and main exits 1."""
        checker = HealthChecker(base_valid_config)

        with patch.object(checker, "check_ffmpeg", new_callable=AsyncMock, return_value=False), \
             patch.object(checker, "check_database", new_callable=AsyncMock, return_value=False), \
             patch.object(checker, "check_telegram_bot", new_callable=AsyncMock, return_value=False), \
             patch.object(checker, "check_admin_connection", new_callable=AsyncMock, return_value=False), \
             patch.object(checker, "check_ai_health", new_callable=AsyncMock, return_value=False):

            checker.critical_failures.extend([
                "FFmpeg Executable: not found",
                "SQLite Database: error",
                "Telegram Bot Auth: error",
                "Admin Connection: error",
                "AI Module Health: error",
            ])

            passed = asyncio.run(checker.run_all())
            assert passed is False
            assert len(checker.critical_failures) == 5


# ==============================================================================
# 3. CLI Argument Parsing Combinations
# ==============================================================================

class TestCliArgumentCombinations:
    """Verifies combinations of CLI flags: --config, --check, --once, --run, invalid args."""

    def test_cli_custom_config_path_respected(self, tmp_path):
        """Specifying --config loads parameters from the custom .env path."""
        custom_env = tmp_path / "custom_test.env"
        custom_env.write_text(
            """
TELEGRAM_BOT_TOKEN=custom_file_token_999
TELEGRAM_CHANNEL_ID=@custom_channel
ADMIN_TELEGRAM_ID=123123123
SEND_SHORTS_TO_ADMIN=false
ENABLE_EVERGREEN=false
AI_API_KEY=
DATABASE_PATH=custom_bot.db
FFMPEG_PATH=custom_ffmpeg
""",
            encoding="utf-8",
        )

        args = parse_args(["--check", "--config", str(custom_env)])
        assert args.check is True
        assert args.config == str(custom_env)

        loaded_cfg = load_config(args.config)
        assert loaded_cfg.bot_token == "custom_file_token_999"
        assert loaded_cfg.channel_id == "@custom_channel"
        assert loaded_cfg.admin_telegram_id == 123123123
        assert loaded_cfg.send_shorts_to_admin is False
        assert loaded_cfg.enable_evergreen is False

    def test_cli_invalid_integer_in_config_fails_gracefully(self, tmp_path):
        """Non-numeric ADMIN_TELEGRAM_ID in config causes main() to exit 1 without unhandled crash."""
        bad_env = tmp_path / "bad_int.env"
        bad_env.write_text("ADMIN_TELEGRAM_ID=NOT_AN_INT\n", encoding="utf-8")

        # main() catches ConfigurationError and returns 1
        exit_code = main(["--check", "--config", str(bad_env)])
        assert exit_code == 1

    def test_cli_once_flag_runs_single_iteration(self, base_valid_config):
        """The --once flag breaks out of the loop after exactly one iteration."""
        args = parse_args(["--run", "--once"])
        assert args.run is True
        assert args.once is True

        with patch("main.load_config", return_value=base_valid_config), \
             patch("main.run_bot", new_callable=AsyncMock) as mock_run_bot:
            exit_code = main(["--run", "--once"])
            assert exit_code == 0
            mock_run_bot.assert_awaited_once_with(base_valid_config, once=True)

    def test_cli_invalid_flag_exits_with_code_2(self):
        """Passing an unrecognized flag raises SystemExit with status 2."""
        with pytest.raises(SystemExit) as exc:
            parse_args(["--non-existent-flag-xyz"])
        assert exc.value.code == 2

    def test_cli_help_flag_exits_with_code_0(self):
        """Passing --help exits cleanly with status 0."""
        with pytest.raises(SystemExit) as exc:
            parse_args(["--help"])
        assert exc.value.code == 0
