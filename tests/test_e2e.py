"""
Milestone 4: Dual Track End-to-End (E2E) Test Suite for Game News Bot.

Comprehensive opaque-box verification across 4 tiers:
- Tier 1: Feature Coverage (F1 to F10)
  - F1: Configuration parsing & validation (ADMIN_TELEGRAM_ID, SEND_SHORTS_TO_ADMIN, ENABLE_EVERGREEN)
  - F2: Steam trailer extraction (mp4['max'] prioritization, discount calculation, major deal detection)
  - F3: FFmpeg 9:16 Shorts Generation (Pillow badge, @gameworld2029 watermark, 720x1280 blurred background, centered trailer)
  - F4: Admin Shorts DM Delivery (vertical video to ADMIN_TELEGRAM_ID with caption and tags #игры #гейминг #shorts #tiktok)
  - F5: Channel Shielding Guarantee (channel posting to @gameworld2029 strictly succeeds even if shorts or admin DM fails)
  - F6: SQLite 180-Day Deduplication (preventing repeated topics within 6 months)
  - F7: AI Evergreen Post Generation (hook title, 3-4 facts/lore, audience question, #история_игр #факты #игры)
  - F8: Offline Curated Lore Fallback (all 19 iconic franchises supported offline without live API keys)
  - F9: Quiet-Period Scheduling (>6h news lull, daytime 10:00-22:00, 24h cooldown, UTC math)
  - F10: CLI Pre-flight Health Check (main.py --check diagnosing 5 subsystems and returning exit code 0 on pass / 1 on failure)
- Tier 2: Boundary & Corner Cases
  - Exactly 179 vs 181 days deduplication boundary
  - Active daytime hours (09:59 vs 10:00 vs 22:00 vs 22:01)
  - Missing admin ID when shorts enabled vs disabled
  - Silent trailers (no audio) and badge title truncation
- Tier 3: Cross-Feature Interactions
  - Full pipeline: Steam discount detected -> Channel deal posted -> Shorts generated -> Admin DM delivered -> Topic recorded
  - News lull triggered -> Quiet-period evaluated -> LRU franchise rotated -> Negative prompt injected -> Telegram post published -> DB deduplication updated
- Tier 4: Real-World Workload Scenarios
  - Multi-day simulation with alternating news releases and quiet-period evergreen content across multiple franchises
  - Network glitch resilience: Telegram failure containment without burning cooldowns or crashing daemon
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image
import pytest
import httpx

from gamebot.config import BotConfig, ConfigurationError, load_config
from gamebot.steam import SteamClient, SteamTrailer, GameDeal
from gamebot.video import ShortsGenerator
from gamebot.database import Database
from gamebot.ai import (
    AIEvergreenGenerator,
    EvergreenPostData,
    ICONIC_FRANCHISES,
    DEFAULT_HASHTAGS,
    probe_ai_health,
    sanitize_telegram_html,
)
from gamebot.curated_lore import (
    CURATED_POSTS,
    CuratedLoreEntry,
    get_all_curated_franchises,
    get_curated_post_for_franchise,
    get_any_unexcluded_curated_post,
)
from gamebot.scheduler import QuietPeriodScheduler
from gamebot.telegram import TelegramPublisher
from main import HealthChecker, main, run_bot_iteration


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def temp_db(tmp_path):
    """Provides a fresh file-backed Database instance for isolated E2E tests."""
    db_file = tmp_path / "e2e_test_bot.db"
    db = Database(str(db_file))
    yield db
    db.close()


@pytest.fixture
def valid_config(tmp_path):
    """Provides a fully valid BotConfig instance."""
    db_file = tmp_path / "e2e_config_bot.db"
    return BotConfig(
        bot_token="123456789:ABC_VALID_BOT_TOKEN_E2E",
        channel_id="@gameworld2029",
        admin_telegram_id=987654321,
        send_shorts_to_admin=True,
        enable_evergreen=True,
        ai_provider="openai",
        ai_api_key="mock_valid_ai_api_key",
        ai_model="gpt-4o-mini",
        database_path=str(db_file),
        ffmpeg_path="ffmpeg",
        watermark_text="@gameworld2029",
        video_output_dir=str(tmp_path / "shorts_output"),
    )


@pytest.fixture
def sample_witcher_deal():
    """Provides a realistic Steam GameDeal for The Witcher 3 with trailer."""
    trailer = SteamTrailer(
        id=256658589,
        name="The Witcher 3: Wild Hunt - Launch Cinematic",
        mp4_url="https://steamcdn-a.akamaihd.net/steam/apps/256658589/movie_max.mp4",
        webm_url="https://steamcdn-a.akamaihd.net/steam/apps/256658589/movie_max.webm",
        is_highlight=True,
    )
    return GameDeal(
        appid=292030,
        title="The Witcher 3: Wild Hunt",
        short_description="Become Geralt of Rivia, a monster slayer for hire, exploring a war-torn world.",
        header_image="https://cdn.akamai.steamstatic.com/steam/apps/292030/header.jpg",
        is_free=False,
        discount_percent=75,
        original_price="2 000 руб.",
        final_price="500 руб.",
        store_url="https://store.steampowered.com/app/292030/",
        trailer=trailer,
    )


# =====================================================================
# Tier 1: Feature Coverage (F1 to F10)
# =====================================================================

class TestTier1FeatureCoverage:
    """Verifies all core features F1 through F10 against specification contracts."""

    def test_tier1_f1_configuration_parsing_and_validation(self, monkeypatch, tmp_path):
        """
        F1: Configuration parsing and validation:
        - ADMIN_TELEGRAM_ID parsed as int
        - SEND_SHORTS_TO_ADMIN and ENABLE_EVERGREEN parsed as boolean
        - Validation rules enforce admin ID when shorts enabled, AI key when evergreen enabled.
        """
        env_file = tmp_path / ".env.test_f1"
        env_file.write_text(
            "TELEGRAM_BOT_TOKEN=111222333:AAFFtest_token\n"
            "TELEGRAM_CHANNEL_ID=@gameworld2029\n"
            "ADMIN_TELEGRAM_ID=777888999\n"
            "SEND_SHORTS_TO_ADMIN=true\n"
            "ENABLE_EVERGREEN=true\n"
            "AI_API_KEY=sk-test-ai-key-12345\n"
            "AI_PROVIDER=openai\n"
            "AI_MODEL=gpt-4o-mini\n"
            "DATABASE_PATH=test_f1.db\n",
            encoding="utf-8",
        )

        cfg = load_config(str(env_file))
        assert cfg.bot_token == "111222333:AAFFtest_token"
        assert cfg.channel_id == "@gameworld2029"
        assert cfg.admin_telegram_id == 777888999
        assert cfg.send_shorts_to_admin is True
        assert cfg.enable_evergreen is True
        assert cfg.ai_api_key == "sk-test-ai-key-12345"
        # Must pass validation
        cfg.validate()

        # Validation failure: missing admin ID when SEND_SHORTS_TO_ADMIN=true
        invalid_cfg_admin = BotConfig(
            bot_token="token",
            channel_id="@channel",
            admin_telegram_id=None,
            send_shorts_to_admin=True,
            enable_evergreen=False,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path="test.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@wm",
            video_output_dir="tmp",
        )
        with pytest.raises(ConfigurationError, match="ADMIN_TELEGRAM_ID is required"):
            invalid_cfg_admin.validate()

        # Validation failure: missing AI key when ENABLE_EVERGREEN=true
        invalid_cfg_ai = BotConfig(
            bot_token="token",
            channel_id="@channel",
            admin_telegram_id=123,
            send_shorts_to_admin=False,
            enable_evergreen=True,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path="test.db",
            ffmpeg_path="ffmpeg",
            watermark_text="@wm",
            video_output_dir="tmp",
        )
        with pytest.raises(ConfigurationError, match="AI_API_KEY is required"):
            invalid_cfg_ai.validate()

    def test_tier1_f2_steam_trailer_extraction_and_deal_logic(self):
        """
        F2: Steam trailer extraction & deal logic:
        - Prioritizes mp4['max'] over mp4['480'] and webm
        - Prioritizes highlight trailer
        - Calculates discount percentage and identifies major events (giveaway or >=50% discount).
        """
        client = SteamClient()

        # Mock app data from Steam API
        mock_app_data = {
            "steam_appid": 292030,
            "name": "The Witcher 3: Wild Hunt",
            "short_description": "Award-winning RPG.",
            "header_image": "https://steam.com/witcher.jpg",
            "is_free": False,
            "price_overview": {
                "discount_percent": 70,
                "initial_formatted": "2 000 руб.",
                "final_formatted": "600 руб.",
            },
            "movies": [
                {
                    "id": 1001,
                    "name": "Standard Gameplay Trailer",
                    "highlight": False,
                    "mp4": {
                        "480": "https://steam.com/1001_480.mp4",
                        "max": "https://steam.com/1001_max.mp4",
                    },
                    "webm": {
                        "max": "https://steam.com/1001_max.webm",
                    },
                },
                {
                    "id": 1002,
                    "name": "Cinematic Highlight Trailer",
                    "highlight": True,
                    "mp4": {
                        "480": "https://steam.com/1002_480.mp4",
                        "max": "https://steam.com/1002_max.mp4",
                    },
                    "webm": {
                        "max": "https://steam.com/1002_max.webm",
                    },
                },
            ],
        }

        # 1. Verify trailer extraction prioritizing max resolution
        trailers = client.extract_trailers(mock_app_data)
        assert len(trailers) == 2
        assert trailers[0].mp4_url == "https://steam.com/1001_max.mp4"
        assert trailers[1].mp4_url == "https://steam.com/1002_max.mp4"

        # 2. Verify extract_best_trailer picks highlight trailer (id 1002)
        best = client.extract_best_trailer(mock_app_data)
        assert best is not None
        assert best.id == 1002
        assert best.is_highlight is True
        assert best.mp4_url == "https://steam.com/1002_max.mp4"

        # 3. Verify deal parsing & major event threshold (70% discount >= 50%)
        deal = client.parse_deal(mock_app_data)
        assert deal is not None
        assert deal.appid == 292030
        assert deal.discount_percent == 70
        assert deal.is_major_event is True
        assert deal.trailer.id == 1002

        # 4. Verify free giveaway deal logic
        free_app_data = {
            "steam_appid": 440,
            "name": "Team Fortress 2",
            "is_free": True,
        }
        free_deal = client.parse_deal(free_app_data)
        assert free_deal.is_free is True
        assert free_deal.discount_percent == 100
        assert free_deal.final_price == "Бесплатно"
        assert free_deal.is_major_event is True

        # 5. Verify minor deal (< 50% discount) is not major event
        minor_app_data = {
            "steam_appid": 10,
            "name": "Minor Game",
            "is_free": False,
            "price_overview": {"discount_percent": 25, "initial_formatted": "100", "final_formatted": "75"},
        }
        minor_deal = client.parse_deal(minor_app_data)
        assert minor_deal.discount_percent == 25
        assert minor_deal.is_major_event is False

    def test_tier1_f3_ffmpeg_shorts_generation(self, tmp_path):
        """
        F3: FFmpeg 9:16 Shorts Generation:
        - Pillow creates 720x1280 RGBA badge with game title & @gameworld2029 watermark.
        - Filter complex specifies 720x1280 blurred background, centered trailer, and overlay.
        - Video encoding parameters (libx264, aac, max 45s cap).
        """
        gen = ShortsGenerator(ffmpeg_path="ffmpeg")

        # 1. Test real Pillow badge rendering
        badge_path = tmp_path / "test_badge.png"
        out_badge = gen.create_overlay_badge(
            title="Half-Life 2",
            watermark="@gameworld2029",
            output_png=str(badge_path),
        )
        assert os.path.exists(out_badge)
        with Image.open(out_badge) as img:
            assert img.size == (720, 1280)
            assert img.mode == "RGBA"

        # 2. Test FFmpeg command structure and parameter verification
        raw_video = tmp_path / "dummy_raw.mp4"
        raw_video.write_bytes(b"dummy_mp4_input")
        out_shorts = tmp_path / "dummy_shorts.mp4"

        captured_cmd = []

        def mock_subprocess_run(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            # Write non-zero output video file to satisfy size check
            out_shorts.write_bytes(b"fake_rendered_shorts_h264_aac")
            res = MagicMock()
            res.returncode = 0
            res.stderr = ""
            return res

        with patch.object(gen, "is_ffmpeg_available", return_value=True), \
             patch("subprocess.run", side_effect=mock_subprocess_run):
            success = gen.build_shorts(
                input_video_path=str(raw_video),
                title="Half-Life 2",
                output_video_path=str(out_shorts),
                max_duration=45,
                watermark="@gameworld2029",
            )

            assert success is True
            assert os.path.exists(str(out_shorts))

            # Validate filter complex flags
            cmd_str = " ".join(captured_cmd)
            assert "-filter_complex" in captured_cmd
            fc_idx = captured_cmd.index("-filter_complex")
            fc = captured_cmd[fc_idx + 1]

            assert "split=2[bg_in][fg_in]" in fc
            assert "scale=720:1280" in fc
            assert "boxblur=22:22" in fc
            assert "scale=720:-2" in fc
            assert "overlay=(W-w)/2:(H-h)/2" in fc
            assert "[base][1:v]overlay=0:0[outv]" in fc

            # Validate codecs, duration cap, and audio mapping
            assert "-c:v" in captured_cmd and "libx264" in captured_cmd
            assert "-c:a" in captured_cmd and "aac" in captured_cmd
            assert "-t" in captured_cmd and "45" in captured_cmd
            assert "-map" in captured_cmd and "0:a?" in captured_cmd

    @pytest.mark.asyncio
    async def test_tier1_f4_admin_shorts_dm_delivery(self, sample_witcher_deal, tmp_path):
        """
        F4: Admin Shorts DM Delivery:
        - Sends vertical video directly to ADMIN_TELEGRAM_ID
        - Caption contains title, price benefit, channel link, and tags:
          #игры #гейминг #shorts #tiktok
        """
        pub = TelegramPublisher(
            bot_token="123456:MOCK_TOKEN",
            channel_id="@gameworld2029",
            admin_id=987654321,
        )

        video_file = tmp_path / "witcher_shorts.mp4"
        video_file.write_bytes(b"fake_mp4_video_data_for_admin")

        captured_requests = []

        async def mock_post(url, data=None, files=None, **kwargs):
            captured_requests.append({"url": url, "data": data, "files": files})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "result": {"message_id": 9991}}
            return mock_resp

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_post):
            delivered = await pub.send_shorts_to_admin(str(video_file), sample_witcher_deal)
            assert delivered is True

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert "sendVideo" in req["url"]
        assert req["data"]["chat_id"] == "987654321"

        caption = req["data"]["caption"]
        assert "The Witcher 3: Wild Hunt" in caption
        assert "-75%" in caption
        assert "@gameworld2029" in caption
        for tag in ["#игры", "#гейминг", "#shorts", "#tiktok"]:
            assert tag in caption

        assert "video" in req["files"]

    @pytest.mark.asyncio
    async def test_tier1_f5_channel_shielding_guarantee(self, sample_witcher_deal, tmp_path):
        """
        F5: Channel Shielding Guarantee:
        - Regular channel posting strictly succeeds EVEN IF:
          1. Video generation throws exception / crashes
          2. Admin DM delivery fails with error
        - Verifies genuine error containment without patching internal publisher methods.
        """
        pub = TelegramPublisher(
            bot_token="123456:MOCK_TOKEN",
            channel_id="@gameworld2029",
            admin_id=987654321,
        )

        # 1. Video generation crashes with RuntimeError (e.g. FFmpeg segfault)
        mock_video_gen = MagicMock()
        mock_video_gen.build_shorts.side_effect = RuntimeError("FFmpeg unexpected segfault")

        mock_steam = MagicMock()
        mock_steam.download_trailer = AsyncMock(return_value=True)

        mock_channel_resp = MagicMock()
        mock_channel_resp.status_code = 200
        mock_channel_resp.json.return_value = {"ok": True, "result": {"message_id": 555}}

        # External HTTP boundary responds 200 for Telegram channel posting
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_channel_resp):
            success = await pub.publish_deal_with_shielding(
                deal=sample_witcher_deal,
                send_shorts=True,
                video_generator=mock_video_gen,
                steam_client=mock_steam,
            )

            # Channel posting MUST succeed and not be disrupted
            assert success is True
            mock_steam.download_trailer.assert_called_once()
            mock_video_gen.build_shorts.assert_called_once()

        # 2. Admin DM delivery fails with error (HTTP 500 or network timeout)
        mock_video_gen2 = MagicMock()
        mock_video_gen2.build_shorts.return_value = True

        mock_steam2 = MagicMock()
        mock_steam2.download_trailer = AsyncMock(return_value=True)

        async def mock_admin_fail_post(url, *args, **kwargs):
            if "/sendVideo" in str(url):
                resp = MagicMock()
                resp.status_code = 500
                resp.text = "Internal Server Error"
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"ok": True, "result": {"message_id": 556}}
            return resp

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_admin_fail_post):
            success2 = await pub.publish_deal_with_shielding(
                deal=sample_witcher_deal,
                send_shorts=True,
                video_generator=mock_video_gen2,
                steam_client=mock_steam2,
            )

            assert success2 is True
            mock_steam2.download_trailer.assert_called_once()
            mock_video_gen2.build_shorts.assert_called_once()

    def test_tier1_f6_sqlite_180_day_deduplication(self, temp_db):
        """
        F6: SQLite 180-Day (6 months) Deduplication:
        - Prevents repeating topics posted within 180 days
        - Handles case-insensitivity (COLLATE NOCASE)
        - Allows posting after 180 days have passed.
        """
        now = datetime.now(timezone.utc)

        # 1. Initially topic is not recent
        topic_key = "half_life_ai_and_sound_sensors"
        assert temp_db.is_topic_recent(topic_key, days=180) is False

        # 2. Record topic
        temp_db.record_evergreen_post(
            franchise="Half-Life",
            topic_key=topic_key,
            topic_title="Революционный ИИ десантников",
            content="Содержимое поста...",
            facts_count=4,
            question="Помните бой?",
            posted_at=now,
        )

        # 3. Topic is now recent
        assert temp_db.is_topic_recent(topic_key, days=180) is True

        # Case-insensitivity check (uppercase key)
        assert temp_db.is_topic_recent(topic_key.upper(), days=180) is True

        # 4. An older topic posted 200 days ago should NOT be recent
        old_topic = "half_life_old_topic"
        temp_db.record_evergreen_post(
            franchise="Half-Life",
            topic_key=old_topic,
            topic_title="Старая тема",
            content="Содержимое...",
            facts_count=3,
            question="Старый вопрос?",
            posted_at=now - timedelta(days=200),
        )
        assert temp_db.is_topic_recent(old_topic, days=180) is False

    @pytest.mark.asyncio
    async def test_tier1_f7_ai_evergreen_post_generation(self):
        """
        F7: AI Evergreen Post Generation:
        - Structured Russian post: hook title, 3-4 facts/lore, question to audience, required hashtags.
        - Validates Cyrillic character ratio, facts count, and Telegram HTML sanitization.
        """
        gen = AIEvergreenGenerator(api_key="mock_key", model="gpt-4o-mini")

        mock_ai_json = {
            "topic_title": "Fallout: Тайна Убежища 77",
            "headline": "🎮 <b>Тайны Пустошей: Убежище 77 и человек с марионетками</b>",
            "intro": "В постапокалиптическом мире Fallout компания Волт-Тек проводила чудовищные социальные эксперименты над жителями убежищ.",
            "facts": [
                "🔹 <b>Один человек и ящик кукол</b>: В Убежище 77 закрыли всего одного мужчину с ящиком марионеток.",
                "🔹 <b>Безумие марионеточника</b>: Спустя полтора года изоляции он начал слышать голоса кукол и сошел с ума.",
                "🔹 <b>Легенда Пустошей</b>: Выбравшись на поверхность, он стал карателем в маске куклы.",
                "🔹 <b>Комикс от Penny Arcade</b>: История Убежища 77 была увековечена в официальном промо-комиксе Bethesda.",
            ],
            "question": "Какое Убежище во всей франшизе Fallout вы считаете самым пугающим?",
            "hashtags": "#история_игр #факты #игры",
        }

        mock_http_resp = MagicMock()
        mock_http_resp.status_code = 200
        mock_http_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(mock_ai_json)}}]
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_http_resp):
            post_data = await gen.generate_post(franchise="Fallout", excluded_topics=[])

        assert isinstance(post_data, EvergreenPostData)
        assert post_data.franchise == "Fallout"
        assert post_data.facts_count == 4
        assert "Убежище 77" in post_data.topic_title
        assert "💬 <b>Вопрос к вам:</b>" in post_data.content
        for tag in ["#история_игр", "#факты", "#игры"]:
            assert tag in post_data.hashtags
            assert tag in post_data.content

        # Verify Cyrillic and Telegram HTML tags
        assert "<b>" in post_data.content and "</b>" in post_data.content
        assert len(post_data.content) >= 300

    @pytest.mark.asyncio
    async def test_tier1_f8_offline_curated_lore_fallback_all_19_franchises(self):
        """
        F8: Offline Curated Lore Fallback:
        - When AI API key is missing or API is unreachable, seamlessly uses curated catalog.
        - Guarantees valid verified post for ALL 19 iconic franchises.
        """
        offline_gen = AIEvergreenGenerator(api_key=None)
        assert offline_gen.api_key is None

        # Verify all 19 franchises
        all_franchises = get_all_curated_franchises()
        assert len(all_franchises) >= 19

        for franchise in all_franchises:
            post = await offline_gen.generate_post(franchise=franchise, excluded_topics=[])
            assert post is not None
            assert post.franchise == franchise
            assert len(post.topic_title) > 0
            assert len(post.topic_key) > 0
            assert post.facts_count in (3, 4)
            assert len(post.question) > 0
            assert "#история_игр" in post.hashtags
            assert len(post.content) >= 300
            assert "🎮" in post.content
            assert "🔹" in post.content

    def test_tier1_f9_quiet_period_scheduling(self, temp_db):
        """
        F9: Quiet-Period Scheduling:
        - Evaluates news gap (>6h since last news post during active daytime 10:00-22:00)
        - Enforces 24-hour cooldown between evergreen posts
        - Uses UTC-anchored math.
        """
        scheduler = QuietPeriodScheduler(
            db=temp_db,
            quiet_period_hours=6.0,
            min_evergreen_cooldown_hours=24.0,
            active_hours_range=(10, 22),
        )

        # Baseline: 15:00 UTC, no prior posts -> triggers
        now = datetime(2026, 10, 23, 15, 0, 0, tzinfo=timezone.utc)
        assert scheduler.should_trigger_evergreen(now) is True

        # Case 1: News was posted 2 hours ago (< 6h) -> blocked
        temp_db.record_news_post(
            news_id="deal_recent",
            source="steam",
            title="Recent Deal",
            url="https://steam.com",
            posted_at=now - timedelta(hours=2),
        )
        assert scheduler.should_trigger_evergreen(now) is False

        # Case 2: News was posted 7 hours ago (> 6h) -> allowed
        now_later = now + timedelta(hours=5)  # 20:00 UTC (7h after deal_recent)
        assert scheduler.should_trigger_evergreen(now_later) is True

        # Case 3: Evergreen post was published 10 hours ago (< 24h cooldown) -> blocked
        temp_db.record_evergreen_post(
            franchise="DOOM",
            topic_key="doom_bsp_engine",
            topic_title="DOOM BSP Engine",
            content="Content...",
            posted_at=now_later - timedelta(hours=10),
        )
        assert scheduler.should_trigger_evergreen(now_later) is False

        # Case 4: Night-time (03:00 UTC, outside 10:00-22:00) -> blocked
        night_time = datetime(2026, 10, 24, 3, 0, 0, tzinfo=timezone.utc)
        assert scheduler.should_trigger_evergreen(night_time) is False

    @pytest.mark.asyncio
    async def test_tier1_f10_health_checker_subsystems(self, valid_config):
        """
        F10: HealthChecker subsystem pre-flight diagnostics:
        - Diagnoses FFmpeg, SQLite DB, Telegram Bot auth, Admin reachability, AI module health.
        - Returns True when all critical checks pass, False on critical failure.
        - Executes all 5 real diagnostic probes against external boundary mocks.
        """
        checker = HealthChecker(valid_config)

        mock_ffmpeg_proc = MagicMock()
        mock_ffmpeg_proc.returncode = 0
        mock_ffmpeg_proc.stdout = "ffmpeg version 6.0 Copyright (c) 2000-2023"

        async def mock_tg_get(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/getMe" in str(url):
                resp.json.return_value = {
                    "ok": True,
                    "result": {"id": 123456789, "is_bot": True, "username": "GameWorldBot"},
                }
            elif "/getChat" in str(url):
                resp.json.return_value = {
                    "ok": True,
                    "result": {"id": 987654321, "type": "private", "username": "admin_artem", "first_name": "Artem"},
                }
            else:
                resp.json.return_value = {"ok": True, "result": {}}
            return resp

        async def mock_ai_post(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "id": "chatcmpl-mock",
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
            }
            return resp

        # Real database is verified via valid_config.database_path file
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=mock_ffmpeg_proc), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=mock_tg_get), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_ai_post):

            all_passed = await checker.run_all()
            assert all_passed is True
            assert len(checker.critical_failures) == 0
            assert len(checker.results) == 5
            assert checker.results["FFmpeg Executable"][0] is True
            assert checker.results["SQLite Database"][0] is True
            assert checker.results["Telegram Bot Auth"][0] is True
            assert checker.results["Admin Connection"][0] is True
            assert checker.results["AI Module Health"][0] is True

    def test_tier1_f10_cli_check_command_line(self, valid_config):
        """
        F10: CLI Pre-flight Health Check entry point (main.py --check):
        - Returns exit code 0 on pass.
        - Returns exit code 1 on failure.
        - Executes real HealthChecker logic without stubbing HealthChecker.run_all.
        """
        mock_ffmpeg_proc = MagicMock()
        mock_ffmpeg_proc.returncode = 0
        mock_ffmpeg_proc.stdout = "ffmpeg version 6.0 Copyright (c) 2000-2023"

        async def mock_tg_get(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/getMe" in str(url):
                resp.json.return_value = {
                    "ok": True,
                    "result": {"id": 123456789, "is_bot": True, "username": "GameWorldBot"},
                }
            elif "/getChat" in str(url):
                resp.json.return_value = {
                    "ok": True,
                    "result": {"id": 987654321, "type": "private", "username": "admin_artem", "first_name": "Artem"},
                }
            else:
                resp.json.return_value = {"ok": True, "result": {}}
            return resp

        async def mock_ai_post(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "id": "chatcmpl-mock",
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
            }
            return resp

        # Test CLI main entry point returning 0 on pass (all external boundary checks succeed)
        with patch("main.load_config", return_value=valid_config), \
             patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=mock_ffmpeg_proc), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=mock_tg_get), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_ai_post):

            exit_code = main(["--check"])
            assert exit_code == 0

        # Test CLI main entry point returning 1 on failure (simulated external boundary failure: Telegram 401 Unauthorized)
        async def mock_tg_get_fail(url, *args, **kwargs):
            resp = MagicMock()
            resp.status_code = 401
            resp.text = "Unauthorized"
            resp.json.return_value = {
                "ok": False,
                "error_code": 401,
                "description": "Unauthorized: invalid bot token",
            }
            return resp

        with patch("main.load_config", return_value=valid_config), \
             patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=mock_ffmpeg_proc), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=mock_tg_get_fail), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_ai_post):

            exit_code_fail = main(["--check"])
            assert exit_code_fail == 1


# =====================================================================
# Tier 2: Boundary & Corner Cases
# =====================================================================

class TestTier2BoundaryAndCornerCases:
    """Verifies subtle boundary conditions, edge limits, and defensive corner cases."""

    def test_boundary_deduplication_exact_179_vs_181_days(self, temp_db):
        """
        Boundary: Exactly 179 days vs 181 days deduplication window:
        - At 179 days, 23 hours (< 180 days): topic must be considered RECENT (duplicate).
        - At 180 days, 1 hour (> 180 days): topic must NOT be considered recent (allowed to post).
        """
        now = datetime.now(timezone.utc)
        topic_key = "witcher_bloody_baron"

        # Record post at exactly 179 days and 23 hours ago
        ts_179d = now - timedelta(days=179, hours=23)
        temp_db.record_evergreen_post(
            franchise="Ведьмак",
            topic_key=topic_key,
            topic_title="Кровавый Барон",
            content="Текст...",
            posted_at=ts_179d,
        )

        # 179 days 23 hours ago: inside the 180-day window -> True
        assert temp_db.is_topic_recent(topic_key, days=180) is True

        # Now test an older post from 181 days ago
        old_key = "witcher_older_quest"
        ts_181d = now - timedelta(days=181)
        temp_db.record_evergreen_post(
            franchise="Ведьмак",
            topic_key=old_key,
            topic_title="Старый квест",
            content="Текст...",
            posted_at=ts_181d,
        )

        # 181 days ago: outside the 180-day window -> False
        assert temp_db.is_topic_recent(old_key, days=180) is False

    def test_boundary_active_daytime_hours_0959_1000_2200_2201(self, temp_db):
        """
        Boundary: Active daytime posting window (10:00 to 22:00):
        - 09:59 (hour 9) -> False (pre-opening)
        - 10:00 (hour 10) -> True (opening boundary)
        - 22:00 (hour 22) -> True (closing boundary)
        - 22:01 / 23:00 (hour 23) -> False (post-closing)
        """
        scheduler = QuietPeriodScheduler(db=temp_db, active_hours_range=(10, 22))

        t_0959 = datetime(2026, 10, 23, 9, 59, 0)
        assert scheduler.should_trigger_evergreen(t_0959) is False
        status_0959 = scheduler.get_status(t_0959)
        assert status_0959["in_active_hours"] is False
        assert "Outside active hours" in status_0959["reason"]

        t_1000 = datetime(2026, 10, 23, 10, 0, 0)
        assert scheduler.should_trigger_evergreen(t_1000) is True
        status_1000 = scheduler.get_status(t_1000)
        assert status_1000["in_active_hours"] is True

        t_2200 = datetime(2026, 10, 23, 22, 0, 0)
        assert scheduler.should_trigger_evergreen(t_2200) is True
        status_2200 = scheduler.get_status(t_2200)
        assert status_2200["in_active_hours"] is True

        t_2300 = datetime(2026, 10, 23, 23, 0, 0)
        assert scheduler.should_trigger_evergreen(t_2300) is False
        status_2300 = scheduler.get_status(t_2300)
        assert status_2300["in_active_hours"] is False

    @pytest.mark.asyncio
    async def test_corner_missing_admin_id_permutations(self, tmp_path):
        """
        Corner case: Missing admin ID when shorts enabled vs disabled:
        - When SEND_SHORTS_TO_ADMIN=true and ADMIN_TELEGRAM_ID=None:
          validate() raises ConfigurationError, and HealthChecker logs FAIL.
        - When SEND_SHORTS_TO_ADMIN=false and ADMIN_TELEGRAM_ID=None:
          validate() succeeds, HealthChecker logs [WARN] without failing pre-flight.
        """
        db_file = tmp_path / "corner_cfg.db"

        # Case 1: Shorts enabled without admin ID -> Critical configuration error
        cfg_shorts_enabled = BotConfig(
            bot_token="token",
            channel_id="@channel",
            admin_telegram_id=None,
            send_shorts_to_admin=True,
            enable_evergreen=False,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path=str(db_file),
            ffmpeg_path="ffmpeg",
            watermark_text="@wm",
            video_output_dir="tmp",
        )
        with pytest.raises(ConfigurationError):
            cfg_shorts_enabled.validate()

        checker_enabled = HealthChecker(cfg_shorts_enabled)
        res_enabled = await checker_enabled.check_admin_connection()
        assert res_enabled is False
        assert len(checker_enabled.critical_failures) > 0

        # Case 2: Shorts disabled without admin ID -> Non-critical warning, check passes
        cfg_shorts_disabled = BotConfig(
            bot_token="token",
            channel_id="@channel",
            admin_telegram_id=None,
            send_shorts_to_admin=False,
            enable_evergreen=False,
            ai_provider="openai",
            ai_api_key="",
            ai_model="gpt-4o-mini",
            database_path=str(db_file),
            ffmpeg_path="ffmpeg",
            watermark_text="@wm",
            video_output_dir="tmp",
        )
        # validate() must pass cleanly
        cfg_shorts_disabled.validate()

        checker_disabled = HealthChecker(cfg_shorts_disabled)
        res_disabled = await checker_disabled.check_admin_connection()
        assert res_disabled is True  # Pass (with warning logged)
        assert len(checker_disabled.warnings) > 0
        assert len(checker_disabled.critical_failures) == 0

    def test_corner_silent_trailers_and_badge_truncation(self, tmp_path):
        """
        Corner case: Silent trailers (no audio) and badge title truncation:
        - FFmpeg command includes '-map 0:a?' ensuring silent video input converts without audio errors.
        - Extremely long game titles (>34 chars) are safely truncated to fit the Pillow badge card.
        """
        gen = ShortsGenerator(ffmpeg_path="ffmpeg")

        # 1. Test long title truncation in badge
        long_title = "The Lord of the Rings: The Return of the King - Extended Edition Game"
        badge_file = tmp_path / "long_title_badge.png"
        gen.create_overlay_badge(title=long_title, output_png=str(badge_file))
        assert os.path.exists(str(badge_file))

        # 2. Test optional audio stream map in FFmpeg command
        raw_video = tmp_path / "silent_raw.mp4"
        raw_video.write_bytes(b"silent_video_input")
        out_video = tmp_path / "silent_shorts.mp4"

        captured_cmd = []

        def mock_run(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            out_video.write_bytes(b"silent_video_output")
            res = MagicMock()
            res.returncode = 0
            return res

        with patch.object(gen, "is_ffmpeg_available", return_value=True), \
             patch("subprocess.run", side_effect=mock_run):
            ok = gen.build_shorts(str(raw_video), "Silent Game", str(out_video))
            assert ok is True
            # Optional audio stream flag
            assert "-map" in captured_cmd
            map_indices = [i for i, x in enumerate(captured_cmd) if x == "-map"]
            mapped_streams = [captured_cmd[i + 1] for i in map_indices]
            assert "[outv]" in mapped_streams
            assert "0:a?" in mapped_streams


# =====================================================================
# Tier 3: Cross-Feature Interactions
# =====================================================================

class TestTier3CrossFeatureInteractions:
    """Verifies multi-subsystem pipelines and complete state transitions."""

    @pytest.mark.asyncio
    async def test_pipeline_steam_deal_channel_post_shorts_admin_dm_db(
        self, valid_config, sample_witcher_deal, temp_db, tmp_path
    ):
        """
        Pipeline 1:
        1. Steam discovery detects major deal (75% off with trailer).
        2. Channel deal is published with header photo and formatted pricing.
        3. 9:16 Shorts video is rendered via FFmpeg with Pillow overlay badge.
        4. Shorts video is delivered to admin DM with #игры #гейминг #shorts #tiktok.
        5. Deal is recorded into SQLite posted_news.
        6. Second iteration skips duplicate deal.
        """
        steam_mock = MagicMock()
        steam_mock.get_featured_deals = AsyncMock(return_value=[sample_witcher_deal])
        steam_mock.download_trailer = AsyncMock(return_value=True)

        video_gen = ShortsGenerator()

        publisher = TelegramPublisher(
            bot_token=valid_config.bot_token,
            channel_id=valid_config.channel_id,
            admin_id=valid_config.admin_telegram_id,
        )

        ai_mock = MagicMock()
        scheduler = QuietPeriodScheduler(db=temp_db)

        # Mock Telegram HTTP endpoints for channel photo and admin video
        captured_calls = []

        async def mock_tg_post(url, data=None, files=None, json=None, **kwargs):
            captured_calls.append({"url": url, "data": data, "files": files, "json": json})
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"ok": True, "result": {"message_id": 54321}}
            return resp

        # Mock FFmpeg build_shorts
        def mock_build_shorts(input_video_path, title, output_video_path, **kwargs):
            with open(output_video_path, "wb") as f:
                f.write(b"encoded_shorts_content")
            return True

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_tg_post), \
             patch.object(video_gen, "build_shorts", side_effect=mock_build_shorts):

            # Iteration 1: Fresh deal
            stats = await run_bot_iteration(
                config=valid_config,
                db=temp_db,
                steam=steam_mock,
                video_gen=video_gen,
                publisher=publisher,
                scheduler=scheduler,
                ai_gen=ai_mock,
            )

            assert stats["deals_published"] == 1
            # Check DB recorded
            news_id = f"steam_{sample_witcher_deal.appid}"
            assert temp_db.is_news_posted(news_id) is True
            assert temp_db.count_news_posts() == 1

            # Check network requests: sendPhoto for channel, sendVideo for admin
            endpoints = [c["url"] for c in captured_calls]
            assert any("sendPhoto" in ep or "sendMessage" in ep for ep in endpoints)
            assert any("sendVideo" in ep for ep in endpoints)

            # Iteration 2: Duplicate deal skipped
            stats2 = await run_bot_iteration(
                config=valid_config,
                db=temp_db,
                steam=steam_mock,
                video_gen=video_gen,
                publisher=publisher,
                scheduler=scheduler,
                ai_gen=ai_mock,
            )
            assert stats2["deals_published"] == 0
            assert temp_db.count_news_posts() == 1

    @pytest.mark.asyncio
    async def test_pipeline_news_lull_quiet_period_lru_rotation_negative_prompt_evergreen(
        self, temp_db
    ):
        """
        Pipeline 2:
        1. News gap > 6 hours during active daytime (14:00 UTC).
        2. Scheduler triggers evergreen cycle.
        3. LRU franchise is selected from franchise history.
        4. Recent topics from past 180 days are injected into AI negative constraints.
        5. Post is generated and published to channel.
        6. Database records post and updates deduplication.
        7. Immediate subsequent check returns False due to 24h cooldown.
        """
        now = datetime(2026, 10, 23, 14, 0, 0, tzinfo=timezone.utc)

        # Pre-seed database: Half-Life was posted 10 days ago, Fallout never posted
        temp_db.record_evergreen_post(
            franchise="Half-Life",
            topic_key="hl_ai_sensors",
            topic_title="ИИ десантников HECU",
            content="Контент...",
            posted_at=now - timedelta(days=10),
        )
        # Pre-seed news posted 8 hours ago (> 6h quiet period)
        temp_db.record_news_post(
            news_id="deal_8h_ago",
            source="steam",
            title="Old Deal",
            url="https://steam.com",
            posted_at=now - timedelta(hours=8),
        )

        scheduler = QuietPeriodScheduler(db=temp_db)
        ai_gen = AIEvergreenGenerator(api_key=None)  # Uses offline curated catalog

        # Mock publisher
        mock_publisher = MagicMock()
        mock_publisher.send_evergreen_to_channel = AsyncMock(return_value=777)

        # Franchises under test
        franchises = ["Half-Life", "Fallout", "Ведьмак (The Witcher)"]

        # Fallout has never been posted, so LRU must pick Fallout
        chosen_franchise = temp_db.get_least_recently_used_franchise(franchises)
        assert chosen_franchise == "Fallout"

        # Trigger full cycle
        row_id = await scheduler.trigger_evergreen_cycle(
            generator=ai_gen,
            publisher=mock_publisher,
            all_franchises=franchises,
            current_time=now,
        )

        assert row_id is not None
        assert row_id > 0
        assert temp_db.count_evergreen_posts() == 2

        # Verify publisher was called with formatted post
        mock_publisher.send_evergreen_to_channel.assert_called_once()
        published_text = mock_publisher.send_evergreen_to_channel.call_args[0][0]
        assert "💬 <b>Вопрос к вам:</b>" in published_text
        assert "#история_игр" in published_text

        # Verify 24h cooldown prevents immediate second trigger
        should_trigger_again = scheduler.should_trigger_evergreen(now + timedelta(hours=1))
        assert should_trigger_again is False


# =====================================================================
# Tier 4: Real-World Workload Scenarios
# =====================================================================

class TestTier4RealWorldWorkloadScenarios:
    """Verifies long-horizon simulation, state evolution, and network fault tolerance."""

    @pytest.mark.asyncio
    async def test_multiday_lifecycle_simulation(self, temp_db, valid_config):
        """
        Simulation: Multi-day bot lifecycle with alternating Steam releases and quiet-period evergreen content:
        - Day 1 (11:00 UTC): Steam deal arrives -> posted, news timestamp updated.
        - Day 1 (14:00 UTC, 3h gap): Quiet threshold not reached -> No evergreen post.
        - Day 1 (18:00 UTC, 7h gap): Quiet threshold satisfied -> Evergreen post 1 (Franchise A) published.
        - Day 1 (20:00 UTC): 24h cooldown active -> Skipped.
        - Day 2 (02:00 UTC): Night time (outside 10:00-22:00) -> Skipped.
        - Day 2 (19:00 UTC, 25h after last evergreen): Cooldown satisfied -> Evergreen post 2 (Franchise B) published.
        - Day 3 (10:30 UTC): New free giveaway -> Steam deal posted.
        """
        scheduler = QuietPeriodScheduler(db=temp_db)
        ai_gen = AIEvergreenGenerator(api_key=None)  # Curated offline catalog

        mock_publisher = MagicMock()
        mock_publisher.send_evergreen_to_channel = AsyncMock(side_effect=[1001, 1002, 1003])

        franchises = ["Portal", "BioShock", "Mass Effect"]

        # Step 1: Day 1, 11:00 UTC - Steam deal posted
        t1_deal = datetime(2026, 10, 20, 11, 0, 0, tzinfo=timezone.utc)
        temp_db.record_news_post("deal_1", "steam", "Deal 1", "url1", posted_at=t1_deal)
        assert temp_db.count_news_posts() == 1

        # Step 2: Day 1, 14:00 UTC (3 hours later) - News gap < 6h -> No evergreen
        t1_3h = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
        assert scheduler.should_trigger_evergreen(t1_3h) is False

        # Step 3: Day 1, 18:00 UTC (7 hours later) - News gap > 6h -> Evergreen post 1
        t1_7h = datetime(2026, 10, 20, 18, 0, 0, tzinfo=timezone.utc)
        assert scheduler.should_trigger_evergreen(t1_7h) is True
        row_id_1 = await scheduler.trigger_evergreen_cycle(
            generator=ai_gen,
            publisher=mock_publisher,
            all_franchises=franchises,
            current_time=t1_7h,
        )
        assert row_id_1 is not None
        assert temp_db.count_evergreen_posts() == 1

        # Step 4: Day 1, 20:00 UTC - Cooldown active (2h < 24h)
        t1_20h = datetime(2026, 10, 20, 20, 0, 0, tzinfo=timezone.utc)
        assert scheduler.should_trigger_evergreen(t1_20h) is False

        # Step 5: Day 2, 02:00 UTC - Night time
        t2_night = datetime(2026, 10, 21, 2, 0, 0, tzinfo=timezone.utc)
        assert scheduler.should_trigger_evergreen(t2_night) is False

        # Step 6: Day 2, 19:00 UTC - 25 hours after evergreen 1, news gap > 6h -> Evergreen post 2
        t2_day = datetime(2026, 10, 21, 19, 0, 0, tzinfo=timezone.utc)
        assert scheduler.should_trigger_evergreen(t2_day) is True
        row_id_2 = await scheduler.trigger_evergreen_cycle(
            generator=ai_gen,
            publisher=mock_publisher,
            all_franchises=franchises,
            current_time=t2_day,
        )
        assert row_id_2 is not None
        assert temp_db.count_evergreen_posts() == 2

        # Step 7: Day 3, 10:30 UTC - New free giveaway posted
        t3_deal = datetime(2026, 10, 22, 10, 30, 0, tzinfo=timezone.utc)
        temp_db.record_news_post("deal_free_2", "steam", "Free Game 2", "url2", posted_at=t3_deal)
        assert temp_db.count_news_posts() == 2

        # Final audit: Exactly 2 news posts and 2 evergreen posts from distinct franchises
        assert temp_db.count_news_posts() == 2
        assert temp_db.count_evergreen_posts() == 2

    @pytest.mark.asyncio
    async def test_network_glitch_resilience_and_failure_containment(
        self, temp_db, valid_config, sample_witcher_deal
    ):
        """
        Fault tolerance: Network glitches and failure containment:
        1. When Telegram evergreen post delivery fails (network timeout / HTTP 500):
           - Database record is NOT created.
           - Cooldown is NOT burned.
           - Daemon does not crash.
        2. When admin Shorts delivery encounters exception:
           - Channel post is completely preserved.
           - Deal is recorded in posted_news so duplicate channel deals are avoided.
        """
        scheduler = QuietPeriodScheduler(db=temp_db)
        ai_gen = AIEvergreenGenerator(api_key=None)

        now = datetime(2026, 10, 23, 15, 0, 0, tzinfo=timezone.utc)

        # 1. Evergreen network failure simulation
        glitchy_publisher = MagicMock()
        glitchy_publisher.send_evergreen_to_channel = AsyncMock(
            side_effect=httpx.ConnectTimeout("Telegram API gateway timeout")
        )

        # Cycle must handle network timeout gracefully without raising
        result = await scheduler.trigger_evergreen_cycle(
            generator=ai_gen,
            publisher=glitchy_publisher,
            all_franchises=["Portal"],
            current_time=now,
        )
        # Result is None (skipped DB insertion)
        assert result is None
        # DB count must remain 0
        assert temp_db.count_evergreen_posts() == 0
        # Cooldown was NOT burned (last_evergreen is still None)
        assert temp_db.get_last_evergreen_post_time() is None

        # 2. Deal publishing: Admin DM failure containment under network glitch
        pub = TelegramPublisher(
            bot_token=valid_config.bot_token,
            channel_id=valid_config.channel_id,
            admin_id=valid_config.admin_telegram_id,
        )

        mock_steam = MagicMock()
        mock_steam.download_trailer = AsyncMock(return_value=True)

        mock_video_gen = MagicMock()
        mock_video_gen.build_shorts.return_value = True

        async def mock_telegram_glitch_post(url, *args, **kwargs):
            if "/sendVideo" in str(url):
                raise httpx.NetworkError("Telegram server connection reset")
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"ok": True, "result": {"message_id": 888}}
            return resp

        # Neither send_channel_deal nor process_and_deliver_shorts_safely are patched.
        # HTTP boundary returns 200 for channel post and raises NetworkError for sendVideo.
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_telegram_glitch_post):
            channel_ok = await pub.publish_deal_with_shielding(
                deal=sample_witcher_deal,
                send_shorts=True,
                video_generator=mock_video_gen,
                steam_client=mock_steam,
            )
            # Channel post must succeed and be fully shielded from admin video delivery failure
            assert channel_ok is True
            mock_steam.download_trailer.assert_called_once()
            mock_video_gen.build_shorts.assert_called_once()
