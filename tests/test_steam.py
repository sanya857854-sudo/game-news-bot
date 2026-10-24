"""
Unit tests for gamebot.steam module.
Validates Steam API client, trailer extraction, resolution priorities, and GameDeal models.
"""

import pytest
import os
import tempfile
from unittest.mock import AsyncMock, patch, MagicMock
from gamebot.steam import SteamClient, SteamTrailer, GameDeal


def test_steam_trailer_dataclass():
    trailer = SteamTrailer(
        id=256844458,
        name="Official Launch Trailer",
        mp4_url="https://video.steam.com/trailer_max.mp4",
        webm_url="https://video.steam.com/trailer_max.webm",
        is_highlight=True,
    )
    assert trailer.id == 256844458
    assert trailer.name == "Official Launch Trailer"
    assert trailer.mp4_url == "https://video.steam.com/trailer_max.mp4"
    assert trailer.webm_url == "https://video.steam.com/trailer_max.webm"
    assert trailer.is_highlight is True


def test_game_deal_is_major_event():
    # 1. 50% discount -> Major Event
    deal_50 = GameDeal(
        appid=10,
        title="Test Game 50",
        short_description="Desc",
        header_image="https://img.com/header.jpg",
        is_free=False,
        discount_percent=50,
        original_price="1000 руб.",
        final_price="500 руб.",
        store_url="https://store.steampowered.com/app/10/",
    )
    assert deal_50.is_major_event is True

    # 2. 75% discount -> Major Event
    deal_75 = GameDeal(
        appid=11,
        title="Test Game 75",
        short_description="Desc",
        header_image="https://img.com/header.jpg",
        is_free=False,
        discount_percent=75,
        original_price="2000 руб.",
        final_price="500 руб.",
        store_url="https://store.steampowered.com/app/11/",
    )
    assert deal_75.is_major_event is True

    # 3. 49% discount -> Not Major Event
    deal_49 = GameDeal(
        appid=12,
        title="Test Game 49",
        short_description="Desc",
        header_image="https://img.com/header.jpg",
        is_free=False,
        discount_percent=49,
        original_price="1000 руб.",
        final_price="510 руб.",
        store_url="https://store.steampowered.com/app/12/",
    )
    assert deal_49.is_major_event is False

    # 4. Free game -> Major Event (Giveaway)
    deal_free = GameDeal(
        appid=13,
        title="Free Game",
        short_description="Desc",
        header_image="https://img.com/header.jpg",
        is_free=True,
        discount_percent=100,
        original_price="0 руб.",
        final_price="Бесплатно",
        store_url="https://store.steampowered.com/app/13/",
    )
    assert deal_free.is_major_event is True


def test_extract_trailers_prioritizes_max_mp4():
    client = SteamClient()
    app_data = {
        "movies": [
            {
                "id": 1001,
                "name": "Teaser",
                "highlight": False,
                "mp4": {
                    "480": "https://steamcdn/teaser_480.mp4",
                    "max": "https://steamcdn/teaser_max.mp4",
                },
                "webm": {
                    "480": "https://steamcdn/teaser_480.webm",
                    "max": "https://steamcdn/teaser_max.webm",
                },
            },
            {
                "id": 1002,
                "name": "Gameplay Trailer",
                "highlight": True,
                "mp4": {
                    "480": "https://steamcdn/gameplay_480.mp4",
                    "max": "https://steamcdn/gameplay_max.mp4",
                },
            },
        ]
    }

    trailers = client.extract_trailers(app_data)
    assert len(trailers) == 2
    assert trailers[0].id == 1001
    assert trailers[0].mp4_url == "https://steamcdn/teaser_max.mp4"
    assert trailers[0].is_highlight is False

    assert trailers[1].id == 1002
    assert trailers[1].mp4_url == "https://steamcdn/gameplay_max.mp4"
    assert trailers[1].is_highlight is True


def test_extract_trailers_fallback_to_480_and_webm():
    client = SteamClient()
    app_data = {
        "movies": [
            {
                "id": 2001,
                "name": "SD Trailer",
                "highlight": False,
                "mp4": {"480": "https://steamcdn/sd_480.mp4"},
            },
            {
                "id": 2002,
                "name": "Webm Only Trailer",
                "highlight": False,
                "webm": {"max": "https://steamcdn/only_webm.webm"},
            },
        ]
    }

    trailers = client.extract_trailers(app_data)
    assert len(trailers) == 2
    assert trailers[0].mp4_url == "https://steamcdn/sd_480.mp4"
    assert trailers[1].mp4_url == "https://steamcdn/only_webm.webm"


def test_extract_trailers_empty_and_malformed():
    client = SteamClient()
    assert client.extract_trailers({}) == []
    assert client.extract_trailers({"movies": []}) == []
    assert client.extract_trailers({"movies": [None, "invalid", {}]}) == []


def test_extract_best_trailer_highlight_preference():
    client = SteamClient()
    app_data = {
        "movies": [
            {
                "id": 1,
                "name": "Regular Trailer",
                "highlight": False,
                "mp4": {"max": "https://steamcdn/regular.mp4"},
            },
            {
                "id": 2,
                "name": "Featured Main Trailer",
                "highlight": True,
                "mp4": {"max": "https://steamcdn/featured.mp4"},
            },
        ]
    }

    best = client.extract_best_trailer(app_data)
    assert best is not None
    assert best.id == 2
    assert best.is_highlight is True
    assert best.name == "Featured Main Trailer"


def test_extract_best_trailer_returns_first_when_no_highlight():
    client = SteamClient()
    app_data = {
        "movies": [
            {
                "id": 10,
                "name": "First Trailer",
                "highlight": False,
                "mp4": {"max": "https://steamcdn/first.mp4"},
            },
            {
                "id": 20,
                "name": "Second Trailer",
                "highlight": False,
                "mp4": {"max": "https://steamcdn/second.mp4"},
            },
        ]
    }

    best = client.extract_best_trailer(app_data)
    assert best is not None
    assert best.id == 10


def test_extract_best_trailer_returns_none_if_no_movies():
    client = SteamClient()
    assert client.extract_best_trailer({}) is None


def test_parse_deal_standard():
    client = SteamClient()
    app_data = {
        "steam_appid": 1091500,
        "name": "Cyberpunk 2077",
        "short_description": "Cyberpunk 2077 is an open-world action-adventure RPG.",
        "header_image": "https://steamcdn/header.jpg",
        "is_free": False,
        "price_overview": {
            "discount_percent": 50,
            "initial_formatted": "2 000 руб.",
            "final_formatted": "1 000 руб.",
        },
        "movies": [
            {
                "id": 555,
                "name": "Launch Trailer",
                "highlight": True,
                "mp4": {"max": "https://steamcdn/launch.mp4"},
            }
        ],
    }

    deal = client.parse_deal(app_data)
    assert deal is not None
    assert deal.appid == 1091500
    assert deal.title == "Cyberpunk 2077"
    assert deal.discount_percent == 50
    assert deal.original_price == "2 000 руб."
    assert deal.final_price == "1 000 руб."
    assert deal.store_url == "https://store.steampowered.com/app/1091500/"
    assert deal.is_major_event is True
    assert deal.trailer is not None
    assert deal.trailer.id == 555


def test_parse_deal_free_game():
    client = SteamClient()
    app_data = {
        "steam_appid": 730,
        "name": "Counter-Strike 2",
        "short_description": "For over two decades, Counter-Strike has offered an elite experience.",
        "header_image": "https://steamcdn/cs2_header.jpg",
        "is_free": True,
    }

    deal = client.parse_deal(app_data)
    assert deal is not None
    assert deal.appid == 730
    assert deal.is_free is True
    assert deal.discount_percent == 100
    assert deal.final_price == "Бесплатно"
    assert deal.is_major_event is True


def test_parse_deal_invalid_input():
    client = SteamClient()
    assert client.parse_deal({}) is None
    assert client.parse_deal(None) is None
    assert client.parse_deal({"name": "No Appid"}) is None


@pytest.mark.asyncio
async def test_get_app_details_success():
    client = SteamClient()
    mock_payload = {
        "1091500": {
            "success": True,
            "data": {
                "steam_appid": 1091500,
                "name": "Cyberpunk 2077",
            },
        }
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        data = await client.get_app_details(1091500)
        assert data is not None
        assert data["steam_appid"] == 1091500
        assert data["name"] == "Cyberpunk 2077"


@pytest.mark.asyncio
async def test_get_app_details_api_error_or_unsuccessful():
    client = SteamClient()

    # 1. HTTP 404
    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_404
        assert await client.get_app_details(9999999) is None

    # 2. success: False
    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 200
    mock_resp_fail.json.return_value = {"9999999": {"success": False}}
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_fail
        assert await client.get_app_details(9999999) is None


@pytest.mark.asyncio
async def test_fetch_deal_end_to_end():
    client = SteamClient()
    mock_data = {
        "steam_appid": 292030,
        "name": "The Witcher 3: Wild Hunt",
        "short_description": "RPG of a generation.",
        "header_image": "https://steamcdn/witcher.jpg",
        "is_free": False,
        "price_overview": {
            "discount_percent": 75,
            "initial_formatted": "2 000 руб.",
            "final_formatted": "500 руб.",
        },
    }

    with patch.object(client, "get_app_details", new_callable=AsyncMock) as mock_get_details:
        mock_get_details.return_value = mock_data
        deal = await client.fetch_deal(292030)
        assert deal is not None
        assert deal.appid == 292030
        assert deal.title == "The Witcher 3: Wild Hunt"
        assert deal.discount_percent == 75
        assert deal.is_major_event is True


@pytest.mark.asyncio
async def test_download_trailer_success():
    client = SteamClient()

    async def fake_aiter_bytes(chunk_size):
        yield b"fake_mp4_bytes_part1_"
        yield b"fake_mp4_bytes_part2"

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.status_code = 200
    mock_stream_ctx.aiter_bytes = fake_aiter_bytes

    # Mock AsyncClient.stream context manager
    mock_client = MagicMock()
    mock_client.stream.return_value.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
    mock_client.stream.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            ok = await client.download_trailer("https://cdn.steam.com/test.mp4", tmp_path)
            assert ok is True
            with open(tmp_path, "rb") as f:
                content = f.read()
            assert content == b"fake_mp4_bytes_part1_fake_mp4_bytes_part2"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
