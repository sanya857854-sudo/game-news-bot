"""
Unit tests for gamebot.telegram module.
Validates TelegramPublisher, channel posting, admin Shorts delivery (9:16),
required tags (#игры #гейминг #shorts #tiktok), and Channel Shielding / Isolation.
"""

import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from gamebot.telegram import TelegramPublisher
from gamebot.steam import GameDeal, SteamTrailer
from gamebot.video import ShortsGenerator


@pytest.fixture
def sample_deal():
    trailer = SteamTrailer(
        id=123,
        name="Official Trailer",
        mp4_url="https://steamcdn.com/trailer.mp4",
        is_highlight=True,
    )
    return GameDeal(
        appid=1091500,
        title="Cyberpunk 2077",
        short_description="An open-world action-adventure RPG set in Night City.",
        header_image="https://steamcdn.com/header.jpg",
        is_free=False,
        discount_percent=50,
        original_price="2 000 руб.",
        final_price="1 000 руб.",
        store_url="https://store.steampowered.com/app/1091500/",
        trailer=trailer,
    )


@pytest.fixture
def sample_free_deal():
    return GameDeal(
        appid=730,
        title="Counter-Strike 2",
        short_description="Tactical competitive shooter.",
        header_image="https://steamcdn.com/cs2.jpg",
        is_free=True,
        discount_percent=100,
        original_price="0 руб.",
        final_price="Бесплатно",
        store_url="https://store.steampowered.com/app/730/",
    )


def test_telegram_publisher_init():
    pub = TelegramPublisher(
        bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        channel_id="@gameworld2029",
        admin_id=987654321,
    )
    assert pub.channel_id == "@gameworld2029"
    assert pub.admin_id == 987654321
    assert "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11" in pub.api_base


@pytest.mark.asyncio
async def test_send_channel_deal_success_with_photo(sample_deal):
    pub = TelegramPublisher("dummy_token", "@gameworld2029")

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        success = await pub.send_channel_deal(sample_deal)

        assert success is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs.get("json", {})

        assert payload["chat_id"] == "@gameworld2029"
        assert payload["photo"] == sample_deal.header_image
        assert sample_deal.title in payload["caption"]
        assert "-50%" in payload["caption"]
        assert "#игры" in payload["caption"]
        assert "#скидки" in payload["caption"]
        assert "#gameworld2029" in payload["caption"]


@pytest.mark.asyncio
async def test_send_channel_deal_fallback_to_text_message(sample_deal):
    pub = TelegramPublisher("dummy_token", "@gameworld2029")

    # First call (sendPhoto) returns 400, second call (sendMessage) returns 200
    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 400
    mock_resp_fail.text = "Bad Request: wrong file identifier/HTTP URL"

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [mock_resp_fail, mock_resp_ok]
        success = await pub.send_channel_deal(sample_deal)

        assert success is True
        assert mock_post.call_count == 2
        second_call = mock_post.call_args_list[1]
        assert "sendMessage" in mock_post.call_args_list[1][0][0]
        payload = second_call[1]["json"]
        assert payload["chat_id"] == "@gameworld2029"
        assert sample_deal.title in payload["text"]


@pytest.mark.asyncio
async def test_send_channel_deal_missing_token_or_channel(sample_deal):
    pub_no_token = TelegramPublisher("", "@gameworld2029")
    assert await pub_no_token.send_channel_deal(sample_deal) is False

    pub_no_channel = TelegramPublisher("dummy_token", "")
    assert await pub_no_channel.send_channel_deal(sample_deal) is False


@pytest.mark.asyncio
async def test_send_shorts_to_admin_missing_admin_id(sample_deal):
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=None)
    assert await pub.send_shorts_to_admin("any_file.mp4", sample_deal) is False


@pytest.mark.asyncio
async def test_send_shorts_to_admin_missing_file(sample_deal):
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    assert await pub.send_shorts_to_admin("non_existent_file.mp4", sample_deal) is False


@pytest.mark.asyncio
async def test_send_shorts_to_admin_success(sample_deal):
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=987654321)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_vid:
        tmp_vid.write(b"mp4_binary_content")
        video_path = tmp_vid.name

    try:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            success = await pub.send_shorts_to_admin(video_path, sample_deal)

            assert success is True
            mock_post.assert_called_once()
            call_url = mock_post.call_args[0][0]
            assert "sendVideo" in call_url

            call_kwargs = mock_post.call_args[1]
            data = call_kwargs["data"]
            assert data["chat_id"] == "987654321"

            # Check required caption and social media hashtags
            caption = data["caption"]
            assert "Вертикальное видео (Shorts / TikTok)" in caption
            assert sample_deal.title in caption
            assert "#игры" in caption
            assert "#гейминг" in caption
            assert "#shorts" in caption
            assert "#tiktok" in caption

            # Check multipart video file parameter
            files = call_kwargs["files"]
            assert "video" in files

    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)


@pytest.mark.asyncio
async def test_send_shorts_to_admin_api_failure(sample_deal):
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=987654321)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_vid:
        tmp_vid.write(b"mp4_binary_content")
        video_path = tmp_vid.name

    try:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden: bot was blocked by the user"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            success = await pub.send_shorts_to_admin(video_path, sample_deal)
            assert success is False
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)


# ==============================================================================
# CHANNEL ISOLATION / SHIELDING TESTS (MANDATORY REQUIREMENT)
# ==============================================================================

@pytest.mark.asyncio
async def test_channel_shielding_when_shorts_delivery_crashes(sample_deal):
    """
    Channel Isolation Guarantee:
    If video generation or admin shorts delivery raises an unexpected exception,
    the primary channel deal post MUST succeed and return True without disruption.
    """
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=987654321)
    mock_gen = MagicMock(spec=ShortsGenerator)

    # Channel posting succeeds
    pub.send_channel_deal = AsyncMock(return_value=True)

    # Shorts process raises an unhandled exception
    pub.process_and_deliver_shorts_safely = AsyncMock(side_effect=RuntimeError("FFmpeg out of memory or crash!"))

    result = await pub.publish_deal_with_shielding(
        deal=sample_deal,
        send_shorts=True,
        video_generator=mock_gen,
    )

    # Must still return True because channel post succeeded
    assert result is True
    pub.send_channel_deal.assert_called_once_with(sample_deal)
    pub.process_and_deliver_shorts_safely.assert_called_once()


@pytest.mark.asyncio
async def test_channel_shielding_when_video_generation_fails(sample_deal):
    """
    Channel Isolation Guarantee:
    When video generation returns False, channel post is still unaffected.
    """
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=987654321)
    mock_gen = MagicMock(spec=ShortsGenerator)

    pub.send_channel_deal = AsyncMock(return_value=True)
    pub.process_and_deliver_shorts_safely = AsyncMock(return_value=False)

    result = await pub.publish_deal_with_shielding(
        deal=sample_deal,
        send_shorts=True,
        video_generator=mock_gen,
    )

    assert result is True
    pub.send_channel_deal.assert_called_once()


@pytest.mark.asyncio
async def test_channel_shielding_when_channel_post_fails(sample_deal):
    """
    If the channel post fails, publish_deal_with_shielding reports False.
    """
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=987654321)
    mock_gen = MagicMock(spec=ShortsGenerator)

    pub.send_channel_deal = AsyncMock(return_value=False)

    result = await pub.publish_deal_with_shielding(
        deal=sample_deal,
        send_shorts=True,
        video_generator=mock_gen,
    )

    assert result is False


# ==============================================================================
# PRE-FLIGHT DIAGNOSTICS TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_check_bot_auth():
    # Success
    pub = TelegramPublisher("valid_token", "@gameworld2029")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"username": "gameworld_bot"}}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        ok, msg = await pub.check_bot_auth()
        assert ok is True
        assert "@gameworld_bot" in msg

    # Failure
    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 401
    mock_resp_fail.text = "Unauthorized"
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_fail
        ok, msg = await pub.check_bot_auth()
        assert ok is False
        assert "401" in msg


@pytest.mark.asyncio
async def test_check_admin_chat():
    pub = TelegramPublisher("valid_token", "@gameworld2029", admin_id=123456)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"first_name": "AdminUser"}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        ok, msg = await pub.check_admin_chat()
        assert ok is True
        assert "AdminUser" in msg

    # Unconfigured admin
    pub_no_admin = TelegramPublisher("valid_token", "@gameworld2029", admin_id=None)
    ok, msg = await pub_no_admin.check_admin_chat()
    assert ok is False
    assert "not configured" in msg


@pytest.mark.asyncio
async def test_send_evergreen_to_channel():
    pub = TelegramPublisher("valid_token", "@gameworld2029")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"message_id": 42}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        msg_id = await pub.send_evergreen_to_channel("<b>Test Evergreen Post</b>")
        assert msg_id == 42
