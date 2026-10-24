"""
Adversarial Stress Test Suite for gamebot.telegram module.
Tests boundary conditions, HTTP error responses (400, 403, 504),
network timeouts, unhandled exceptions, caption format compliance,
and Channel Shielding / Isolation invariants.
"""

import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

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
        title="Cyberpunk 2077 <Night City Edition & 'DLC'>",
        short_description="An open-world action-adventure RPG set in Night City.",
        header_image="https://steamcdn.com/header.jpg",
        is_free=False,
        discount_percent=50,
        original_price="2 000 руб.",
        final_price="1 000 руб.",
        store_url="https://store.steampowered.com/app/1091500/?snr=1",
        trailer=trailer,
    )


@pytest.fixture
def sample_free_deal():
    trailer = SteamTrailer(
        id=456,
        name="Launch Trailer",
        mp4_url="https://steamcdn.com/trailer_free.mp4",
        is_highlight=True,
    )
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
        trailer=trailer,
    )


# ==============================================================================
# 1. ADMIN_TELEGRAM_ID BOUNDARY CONDITIONS
# ==============================================================================

@pytest.mark.asyncio
async def test_send_shorts_to_admin_none_admin_id(sample_deal):
    """Admin ID is None: send_shorts_to_admin fails gracefully returning False."""
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=None)
    assert pub.admin_id is None
    res = await pub.send_shorts_to_admin("any_path.mp4", sample_deal)
    assert res is False


@pytest.mark.asyncio
async def test_send_shorts_to_admin_zero_admin_id(sample_deal):
    """Admin ID is 0: send_shorts_to_admin treats it as falsy and returns False."""
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=0)
    res = await pub.send_shorts_to_admin("any_path.mp4", sample_deal)
    assert res is False


@pytest.mark.asyncio
async def test_check_admin_chat_none_admin_id():
    """check_admin_chat returns (False, '...') when admin_id is None."""
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=None)
    ok, msg = await pub.check_admin_chat()
    assert ok is False
    assert "ADMIN_TELEGRAM_ID is not configured" in msg


# ==============================================================================
# 2. TELEGRAM API HTTP ERROR CODES (400, 403, 504) & NETWORK FAILURES
# ==============================================================================

@pytest.mark.asyncio
async def test_send_shorts_to_admin_http_400_bad_request(sample_deal):
    """HTTP 400 Bad Request during video upload returns False without raising."""
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(b"video_bytes")
        video_path = tmp.name

    try:
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = '{"ok":false,"error_code":400,"description":"Bad Request: wrong file format"}'

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            success = await pub.send_shorts_to_admin(video_path, sample_deal)
            assert success is False
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)


@pytest.mark.asyncio
async def test_send_shorts_to_admin_http_403_forbidden_blocked(sample_deal):
    """HTTP 403 Forbidden (bot was blocked by user) returns False without raising."""
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(b"video_bytes")
        video_path = tmp.name

    try:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = '{"ok":false,"error_code":403,"description":"Forbidden: bot was blocked by the user"}'

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            success = await pub.send_shorts_to_admin(video_path, sample_deal)
            assert success is False
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)


@pytest.mark.asyncio
async def test_send_shorts_to_admin_http_504_gateway_timeout(sample_deal):
    """HTTP 504 Gateway Timeout during video upload returns False without raising."""
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(b"video_bytes")
        video_path = tmp.name

    try:
        mock_resp = MagicMock()
        mock_resp.status_code = 504
        mock_resp.text = '<html><head><title>504 Gateway Time-out</title></head></html>'

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            success = await pub.send_shorts_to_admin(video_path, sample_deal)
            assert success is False
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)


@pytest.mark.asyncio
async def test_send_shorts_to_admin_httpx_timeout_exception(sample_deal):
    """httpx.ReadTimeout raised during video upload is caught and returns False."""
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(b"video_bytes")
        video_path = tmp.name

    try:
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.ReadTimeout("Read timed out after 180s")
            success = await pub.send_shorts_to_admin(video_path, sample_deal)
            assert success is False
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)


@pytest.mark.asyncio
async def test_send_shorts_to_admin_httpx_connect_error(sample_deal):
    """httpx.ConnectError is caught and returns False."""
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(b"video_bytes")
        video_path = tmp.name

    try:
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.ConnectError("Connection refused by peer")
            success = await pub.send_shorts_to_admin(video_path, sample_deal)
            assert success is False
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)


# ==============================================================================
# 3. CHANNEL SHIELDING & ISOLATION GUARANTEES
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("status_code,err_text", [
    (400, "Bad Request: caption too long"),
    (403, "Forbidden: bot was blocked by the user"),
    (504, "504 Gateway Time-out"),
])
async def test_channel_shielding_when_admin_upload_returns_http_error(sample_deal, status_code, err_text):
    """
    Channel Isolation Invariant:
    If channel posting succeeds, but the admin upload returns HTTP 400, 403, or 504,
    publish_deal_with_shielding MUST return True.
    """
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    mock_gen = MagicMock(spec=ShortsGenerator)

    # Channel succeeds
    pub.send_channel_deal = AsyncMock(return_value=True)

    # Admin shorts upload returns HTTP error
    pub.send_shorts_to_admin = AsyncMock(return_value=False)

    # Mock process_and_deliver_shorts_safely to simulate real call flow
    pub.process_and_deliver_shorts_safely = AsyncMock(return_value=False)

    result = await pub.publish_deal_with_shielding(
        deal=sample_deal,
        send_shorts=True,
        video_generator=mock_gen,
    )

    assert result is True
    pub.send_channel_deal.assert_called_once_with(sample_deal)
    pub.process_and_deliver_shorts_safely.assert_called_once()


@pytest.mark.asyncio
async def test_channel_shielding_when_shorts_delivery_times_out(sample_deal):
    """
    Channel Isolation Invariant:
    If admin delivery encounters a network timeout, publish_deal_with_shielding still returns True.
    """
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    mock_gen = MagicMock(spec=ShortsGenerator)

    pub.send_channel_deal = AsyncMock(return_value=True)
    pub.process_and_deliver_shorts_safely = AsyncMock(side_effect=httpx.ReadTimeout("Timeout in shorts flow"))

    result = await pub.publish_deal_with_shielding(
        deal=sample_deal,
        send_shorts=True,
        video_generator=mock_gen,
    )

    assert result is True
    pub.send_channel_deal.assert_called_once_with(sample_deal)


@pytest.mark.asyncio
async def test_channel_shielding_when_shorts_raises_catastrophic_exception(sample_deal):
    """
    Channel Isolation Invariant:
    Catastrophic crashes (MemoryError, RuntimeError, OSError) in the shorts pipeline
    are completely isolated and do not break the channel post result.
    """
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    mock_gen = MagicMock(spec=ShortsGenerator)

    pub.send_channel_deal = AsyncMock(return_value=True)
    pub.process_and_deliver_shorts_safely = AsyncMock(side_effect=RuntimeError("FFmpeg core dump or segfault"))

    result = await pub.publish_deal_with_shielding(
        deal=sample_deal,
        send_shorts=True,
        video_generator=mock_gen,
    )

    assert result is True


@pytest.mark.asyncio
async def test_channel_failure_is_faithfully_returned(sample_deal):
    """
    If channel posting itself fails, publish_deal_with_shielding must return False.
    """
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    mock_gen = MagicMock(spec=ShortsGenerator)

    pub.send_channel_deal = AsyncMock(return_value=False)
    pub.process_and_deliver_shorts_safely = AsyncMock(return_value=True)

    result = await pub.publish_deal_with_shielding(
        deal=sample_deal,
        send_shorts=True,
        video_generator=mock_gen,
    )

    assert result is False


@pytest.mark.asyncio
async def test_channel_shielding_skips_shorts_when_admin_id_is_none(sample_deal):
    """
    When admin_id is None, publish_deal_with_shielding publishes to channel
    and cleanly skips shorts processing without error.
    """
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=None)
    mock_gen = MagicMock(spec=ShortsGenerator)

    pub.send_channel_deal = AsyncMock(return_value=True)
    pub.process_and_deliver_shorts_safely = AsyncMock()

    result = await pub.publish_deal_with_shielding(
        deal=sample_deal,
        send_shorts=True,
        video_generator=mock_gen,
    )

    assert result is True
    pub.send_channel_deal.assert_called_once_with(sample_deal)
    pub.process_and_deliver_shorts_safely.assert_not_called()


# ==============================================================================
# 4. CAPTION STRUCTURE & EXACT SOCIAL MEDIA TAGS
# ==============================================================================

def test_admin_shorts_caption_structure(sample_deal):
    """
    Caption format validation:
    - Includes game title (with HTML escaping)
    - Includes @gameworld2029
    - Includes exact hashtags: #игры #гейминг #shorts #tiktok
    """
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    caption = pub._format_admin_shorts_caption(sample_deal)

    # 1. Game title presence and HTML escaping
    assert "Cyberpunk 2077" in caption
    assert "&lt;Night City Edition &amp; &#x27;DLC&#x27;&gt;" in caption or "&lt;Night City Edition" in caption

    # 2. Channel mention
    assert "@gameworld2029" in caption

    # 3. Exact social media hashtags
    assert "#игры" in caption
    assert "#гейминг" in caption
    assert "#shorts" in caption
    assert "#tiktok" in caption
    assert "#игры #гейминг #shorts #tiktok" in caption

    # 4. Price info
    assert "-50%" in caption


def test_admin_shorts_caption_free_game(sample_free_deal):
    """Caption format validation for 100% free giveaway games."""
    pub = TelegramPublisher("dummy_token", "@gameworld2029", admin_id=12345678)
    caption = pub._format_admin_shorts_caption(sample_free_deal)

    assert "Counter-Strike 2" in caption
    assert "Бесплатно" in caption
    assert "@gameworld2029" in caption
    assert "#игры #гейминг #shorts #tiktok" in caption
