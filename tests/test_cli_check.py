"""
Unit and integration tests for CLI pre-flight diagnostic health checks (main.py --check)
and bot runtime integration.
"""

import asyncio
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from gamebot.config import BotConfig
from main import HealthChecker, main, parse_args, run_bot_iteration


@pytest.fixture
def mock_config(tmp_path):
    """Provides a valid test BotConfig instance."""
    db_path = tmp_path / "test_bot.db"
    return BotConfig(
        bot_token="123456789:MOCK_TOKEN_CLI",
        channel_id="@gameworld2029",
        admin_telegram_id=987654321,
        send_shorts_to_admin=True,
        enable_evergreen=True,
        ai_provider="openai",
        ai_api_key="mock_ai_api_key",
        ai_model="gpt-4o-mini",
        database_path=str(db_path),
        ffmpeg_path="ffmpeg",
        watermark_text="@gameworld2029",
        video_output_dir=str(tmp_path / "shorts"),
    )


class TestHealthCheckerComponents:
    """Unit tests for individual HealthChecker diagnostic probes."""

    @pytest.mark.asyncio
    async def test_check_ffmpeg_found(self, mock_config):
        checker = HealthChecker(mock_config)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ffmpeg version 6.0 Copyright (c) 2000-2023"

        with patch("shutil.which", return_value="C:/ffmpeg/ffmpeg.exe"), \
             patch("subprocess.run", return_value=mock_proc):
            passed = await checker.check_ffmpeg()
            assert passed is True
            assert "FFmpeg Executable" in checker.results
            assert checker.results["FFmpeg Executable"][0] is True

    @pytest.mark.asyncio
    async def test_check_ffmpeg_not_found(self, mock_config):
        checker = HealthChecker(mock_config)
        with patch("shutil.which", return_value=None), \
             patch("os.path.isfile", return_value=False):
            passed = await checker.check_ffmpeg()
            assert passed is False
            assert checker.results["FFmpeg Executable"][0] is False
            assert len(checker.critical_failures) == 1

    @pytest.mark.asyncio
    async def test_check_database_success(self, mock_config):
        checker = HealthChecker(mock_config)
        passed = await checker.check_database()
        assert passed is True
        assert checker.results["SQLite Database"][0] is True

    @pytest.mark.asyncio
    async def test_check_database_failure(self, mock_config):
        broken_config = BotConfig(
            bot_token=mock_config.bot_token,
            channel_id=mock_config.channel_id,
            admin_telegram_id=mock_config.admin_telegram_id,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="key",
            ai_model="gpt-4o-mini",
            database_path="/non_existent_drive_xyz/invalid/bot.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(broken_config)
        with patch("gamebot.database.Database.get_connection", side_effect=sqlite3.OperationalError("Disk error")):
            passed = await checker.check_database()
            assert passed is False
            assert checker.results["SQLite Database"][0] is False

    @pytest.mark.asyncio
    async def test_check_telegram_bot_success(self, mock_config):
        checker = HealthChecker(mock_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"username": "gameworld2029_bot", "id": 123456789},
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            passed = await checker.check_telegram_bot()
            assert passed is True
            assert checker.results["Telegram Bot Auth"][0] is True
            assert "gameworld2029_bot" in checker.results["Telegram Bot Auth"][1]

    @pytest.mark.asyncio
    async def test_check_telegram_bot_unauthorized(self, mock_config):
        checker = HealthChecker(mock_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": False,
            "error_code": 401,
            "description": "Unauthorized: invalid bot token",
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            passed = await checker.check_telegram_bot()
            assert passed is False
            assert checker.results["Telegram Bot Auth"][0] is False

    @pytest.mark.asyncio
    async def test_check_admin_connection_success(self, mock_config):
        checker = HealthChecker(mock_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"id": 987654321, "first_name": "Artem", "username": "artem_admin"},
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            passed = await checker.check_admin_connection()
            assert passed is True
            assert checker.results["Admin Connection"][0] is True
            assert "artem_admin" in checker.results["Admin Connection"][1]

    @pytest.mark.asyncio
    async def test_check_admin_connection_chat_not_found_warns_gracefully(self, mock_config):
        checker = HealthChecker(mock_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: chat not found",
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            passed = await checker.check_admin_connection()
            # Chat not found emits WARN, but does not block bot startup
            assert passed is True
            assert len(checker.warnings) == 1
            assert "/start" in checker.warnings[0]

    @pytest.mark.asyncio
    async def test_check_admin_connection_missing_when_required(self, mock_config):
        no_admin_config = BotConfig(
            bot_token=mock_config.bot_token,
            channel_id=mock_config.channel_id,
            admin_telegram_id=None,
            send_shorts_to_admin=True,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="key",
            ai_model="gpt-4o-mini",
            database_path=mock_config.database_path,
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(no_admin_config)
        passed = await checker.check_admin_connection()
        assert passed is False
        assert checker.results["Admin Connection"][0] is False

    @pytest.mark.asyncio
    async def test_check_admin_connection_missing_when_not_required_warns(self, mock_config):
        no_admin_config = BotConfig(
            bot_token=mock_config.bot_token,
            channel_id=mock_config.channel_id,
            admin_telegram_id=None,
            send_shorts_to_admin=False,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="key",
            ai_model="gpt-4o-mini",
            database_path=mock_config.database_path,
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(no_admin_config)
        passed = await checker.check_admin_connection()
        assert passed is True
        assert len(checker.warnings) == 1

    @pytest.mark.asyncio
    async def test_check_ai_health_success(self, mock_config):
        checker = HealthChecker(mock_config)
        with patch("main.probe_ai_health", new_callable=AsyncMock, return_value=(True, "OpenAI is ready")):
            passed = await checker.check_ai_health()
            assert passed is True
            assert checker.results["AI Module Health"][0] is True

    @pytest.mark.asyncio
    async def test_check_ai_health_failure(self, mock_config):
        checker = HealthChecker(mock_config)
        with patch("main.probe_ai_health", new_callable=AsyncMock, return_value=(False, "AI Auth failed")):
            passed = await checker.check_ai_health()
            assert passed is False
            assert checker.results["AI Module Health"][0] is False

    @pytest.mark.asyncio
    async def test_check_ai_health_disabled_warns(self, mock_config):
        no_ai_config = BotConfig(
            bot_token=mock_config.bot_token,
            channel_id=mock_config.channel_id,
            admin_telegram_id=mock_config.admin_telegram_id,
            send_shorts_to_admin=True,
            enable_evergreen=False,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path=mock_config.database_path,
            ffmpeg_path="ffmpeg",
            watermark_text="@test",
            video_output_dir="tmp",
        )
        checker = HealthChecker(no_ai_config)
        passed = await checker.check_ai_health()
        assert passed is True
        assert len(checker.warnings) == 1


class TestHealthCheckerRunAll:
    """Tests for run_all aggregating results and exit conditions."""

    @pytest.mark.asyncio
    async def test_run_all_all_pass(self, mock_config):
        checker = HealthChecker(mock_config)
        with patch.object(checker, "check_ffmpeg", new_callable=AsyncMock, return_value=True), \
             patch.object(checker, "check_database", new_callable=AsyncMock, return_value=True), \
             patch.object(checker, "check_telegram_bot", new_callable=AsyncMock, return_value=True), \
             patch.object(checker, "check_admin_connection", new_callable=AsyncMock, return_value=True), \
             patch.object(checker, "check_ai_health", new_callable=AsyncMock, return_value=True):
            success = await checker.run_all()
            assert success is True

    @pytest.mark.asyncio
    async def test_run_all_one_failure_returns_false(self, mock_config):
        checker = HealthChecker(mock_config)
        checker.critical_failures.append("Telegram Bot Auth: 401 Unauthorized")
        with patch.object(checker, "check_ffmpeg", new_callable=AsyncMock, return_value=True), \
             patch.object(checker, "check_database", new_callable=AsyncMock, return_value=True), \
             patch.object(checker, "check_telegram_bot", new_callable=AsyncMock, return_value=False), \
             patch.object(checker, "check_admin_connection", new_callable=AsyncMock, return_value=True), \
             patch.object(checker, "check_ai_health", new_callable=AsyncMock, return_value=True):
            success = await checker.run_all()
            assert success is False


class TestCliArgumentsAndMain:
    """Tests for parse_args and main entry point."""

    def test_parse_args_flags(self):
        args = parse_args(["--check"])
        assert args.check is True

        args = parse_args(["--run", "--once"])
        assert args.run is True
        assert args.once is True

        args = parse_args(["--gen-evergreen", "--config", "test.env"])
        assert args.gen_evergreen is True
        assert args.config == "test.env"

    def test_main_check_success_returns_zero(self, mock_config):
        with patch("main.load_config", return_value=mock_config), \
             patch("main.HealthChecker.run_all", new_callable=AsyncMock, return_value=True):
            exit_code = main(["--check"])
            assert exit_code == 0

    def test_main_check_failure_returns_one(self, mock_config):
        with patch("main.load_config", return_value=mock_config), \
             patch("main.HealthChecker.run_all", new_callable=AsyncMock, return_value=False):
            exit_code = main(["--check"])
            assert exit_code == 1

    def test_main_invalid_config_returns_one(self):
        broken_config = BotConfig(
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
            watermark_text="@test",
            video_output_dir="tmp",
        )
        with patch("main.load_config", return_value=broken_config):
            # Running bot with invalid config raises ConfigurationError and exits 1
            exit_code = main(["--run"])
            assert exit_code == 1


class TestBotRuntimeIteration:
    """Tests for run_bot_iteration coordinating subsystems."""

    @pytest.mark.asyncio
    async def test_iteration_publishes_deal_and_evergreen(self, mock_config, tmp_path):
        from gamebot.database import Database
        from gamebot.steam import GameDeal, SteamTrailer
        from gamebot.ai import EvergreenPostData

        db = Database(str(tmp_path / "runtime_test.db"))

        mock_steam = MagicMock()
        deal = GameDeal(
            appid=440,
            title="Team Fortress 2",
            short_description="Shooter game",
            header_image="https://steam.com/tf2.jpg",
            is_free=True,
            discount_percent=100,
            original_price="Free",
            final_price="Free",
            store_url="https://store.steampowered.com/app/440/",
            trailer=SteamTrailer(id=1, name="TF2", mp4_url="https://steam.com/tf2.mp4"),
        )
        mock_steam.get_featured_deals = AsyncMock(return_value=[deal])

        mock_video_gen = MagicMock()
        mock_publisher = MagicMock()
        mock_publisher.publish_deal_with_shielding = AsyncMock(return_value=True)
        mock_publisher.send_evergreen_to_channel = AsyncMock(return_value=12345)

        mock_scheduler = MagicMock()
        mock_scheduler.should_trigger_evergreen.return_value = True

        mock_ai_gen = MagicMock()
        post_data = EvergreenPostData(
            franchise="Half-Life",
            topic_key="hl_cut_levels",
            topic_title="Half-Life: Вырезанные уровни",
            content="🎮 <b>Half-Life</b>\n\nИнтересные факты...\n\n#история_игр #факты #игры",
            facts_count=3,
            question="Играли ли вы?",
            hashtags="#история_игр #факты #игры",
        )
        mock_ai_gen.generate_post = AsyncMock(return_value=post_data)

        stats = await run_bot_iteration(
            config=mock_config,
            db=db,
            steam=mock_steam,
            video_gen=mock_video_gen,
            publisher=mock_publisher,
            scheduler=mock_scheduler,
            ai_gen=mock_ai_gen,
        )

        assert stats["deals_published"] == 1
        assert stats["evergreen_published"] == 1
        assert db.is_news_posted("steam_440") is True
        assert db.count_evergreen_posts() == 1


@pytest.mark.asyncio
async def test_health_checker_all_pass(test_config):
    checker = HealthChecker(test_config)
    with patch.object(checker, "check_ffmpeg", new_callable=AsyncMock) as m_ff, \
         patch.object(checker, "check_database", new_callable=AsyncMock) as m_db, \
         patch.object(checker, "check_telegram_bot", new_callable=AsyncMock) as m_bot, \
         patch.object(checker, "check_admin_connection", new_callable=AsyncMock) as m_adm, \
         patch.object(checker, "check_ai_health", new_callable=AsyncMock) as m_ai:

        m_ff.return_value = True
        m_db.return_value = True
        m_bot.return_value = True
        m_adm.return_value = True
        m_ai.return_value = True

        result = await checker.run_all()
        assert result is True


@pytest.mark.asyncio
async def test_health_checker_fails_on_bad_admin(test_config):
    checker = HealthChecker(test_config)
    checker.critical_failures.append("Admin Connection: chat not found")
    with patch.object(checker, "check_ffmpeg", new_callable=AsyncMock, return_value=True), \
         patch.object(checker, "check_database", new_callable=AsyncMock, return_value=True), \
         patch.object(checker, "check_telegram_bot", new_callable=AsyncMock, return_value=True), \
         patch.object(checker, "check_admin_connection", new_callable=AsyncMock, return_value=False), \
         patch.object(checker, "check_ai_health", new_callable=AsyncMock, return_value=True):

        result = await checker.run_all()
        assert result is False

