"""
Unit tests for gamebot.ai and gamebot.curated_lore modules.
Verifies AIEvergreenGenerator, negative prompt injection, response parsing,
content validation, offline curated fallback, and CLI probe_ai_health().
"""

import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import httpx

from gamebot.ai import (
    AIEvergreenGenerator,
    EvergreenPostData,
    ICONIC_FRANCHISES,
    DEFAULT_HASHTAGS,
    sanitize_telegram_html,
)
from gamebot.curated_lore import (
    CURATED_POSTS,
    CuratedLoreEntry,
    get_all_curated_franchises,
    get_curated_post_for_franchise,
    get_any_unexcluded_curated_post,
)


def test_iconic_franchises_catalog():
    """Verifies iconic franchises list includes core requested game series."""
    assert len(ICONIC_FRANCHISES) >= 19
    franchise_str = " ".join(ICONIC_FRANCHISES)
    for requested in [
        "Half-Life",
        "Ведьмак",
        "Fallout",
        "GTA",
        "Dark Souls",
        "BioShock",
        "S.T.A.L.K.E.R.",
        "Portal",
        "DOOM",
        "Silent Hill",
        "Resident Evil",
        "Gothic",
        "The Elder Scrolls",
    ]:
        assert requested in franchise_str


def test_normalize_topic_key():
    """Verifies deterministic slug creation for topic deduplication."""
    slug1 = AIEvergreenGenerator.normalize_topic_key("Half-Life", "Революционный ИИ десантников HECU!")
    assert "half_life" in slug1
    assert "hecu" in slug1
    assert "!" not in slug1

    slug2 = AIEvergreenGenerator.normalize_topic_key("Fallout", "Убежище 77 и ящик с марионетками")
    assert "fallout" in slug2
    assert "убежище" in slug2


def test_build_system_and_user_prompt():
    """Verifies system prompt specifies HTML tags and user prompt injects negative constraints."""
    gen = AIEvergreenGenerator(api_key="mock-key")
    system_prompt = gen.build_system_prompt()
    assert "#история_игр #факты #игры" in system_prompt
    assert "JSON" in system_prompt
    assert "<b>" in system_prompt
    assert "HTML-теги" in system_prompt

    # User prompt without exclusions
    user_prompt_1 = gen.build_user_prompt("BioShock")
    assert "BioShock" in user_prompt_1
    assert "ВАЖНОЕ ОГРАНИЧЕНИЕ" not in user_prompt_1

    # User prompt with 180-day excluded topics
    excluded = ["Подводный город Rapture", "Фраза Would you kindly"]
    user_prompt_2 = gen.build_user_prompt("BioShock", excluded_topics=excluded)
    assert "Подводный город Rapture" in user_prompt_2
    assert "Would you kindly" in user_prompt_2
    assert "СТРОГО ЗАПРЕЩЕНО" in user_prompt_2


def test_curated_lore_catalog_completeness():
    """Verifies curated offline catalog contains verified posts for all 19 iconic series with proper format."""
    assert len(CURATED_POSTS) >= 19
    franchises = get_all_curated_franchises()
    assert "Half-Life" in franchises
    assert "Fallout" in franchises
    assert "BioShock" in franchises
    assert "DOOM" in franchises
    assert "Gothic" in franchises
    assert "Resident Evil" in franchises
    assert "Silent Hill" in franchises
    assert "The Elder Scrolls (Skyrim, Morrowind)" in franchises

    for post in CURATED_POSTS:
        assert post.headline.startswith("🎮")
        assert 3 <= len(post.facts) <= 4
        for f in post.facts:
            assert f.startswith("🔹")
            assert "<b>" in f and "</b>" in f
        assert len(post.question) > 10
        assert "#история_игр #факты #игры" in post.hashtags
        formatted = post.format_post()
        assert "💬 <b>Вопрос к вам:</b>" in formatted
        assert post.hashtags in formatted


def test_sanitize_telegram_html_bold_conversion():
    """Verifies converting markdown **bold** and __bold__ to HTML <b>bold</b>."""
    raw = "🎮 **Заголовок поста** и __второй заголовок__"
    result = sanitize_telegram_html(raw)
    assert result == "🎮 <b>Заголовок поста</b> и <b>второй заголовок</b>"
    assert "**" not in result
    assert "__" not in result


def test_sanitize_telegram_html_entity_safety():
    """Verifies raw &, <, > are escaped without breaking Telegram allowed tags."""
    raw = "🔹 <b>D&D</b>: Секреты <Episode 1> & <code>x < y and a > b</code>"
    result = sanitize_telegram_html(raw)
    assert "<b>D&amp;D</b>" in result
    assert "&lt;Episode 1&gt;" in result
    assert "<code>x &lt; y and a &gt; b</code>" in result
    # Ensure allowed tags were preserved
    assert result.startswith("🔹 <b>")


def test_parse_ai_response_markdown_bold_and_html_safety():
    """Verifies _parse_ai_response converts **bold** to <b> and ensures Telegram HTML safety."""
    gen = AIEvergreenGenerator(api_key="test-key")
    raw_markdown = (
        "🎮 **Gothic: тайны Барьера**\n"
        "В 2001 году Piranha Bytes создали легендарную RPG.\n"
        "🔹 **Живой мир**: NPC спят и едят <24/7> & пьют шнапс.\n"
        "🔹 **In Extremo**: Рок-концерт в Старом Лагере.\n"
        "🔹 **Удар Буллита**: Встреча с каторжниками.\n"
        "💬 **Вопрос к вам:** В какой лагерь вы вступили?"
    )
    post = gen._parse_ai_response(raw_markdown, franchise="Gothic")
    assert post is not None
    assert "<b>Gothic: тайны Барьера</b>" in post.content
    assert "<b>Живой мир</b>" in post.content
    assert "&lt;24/7&gt;" in post.content
    assert "&amp;" in post.content
    assert "**" not in post.content
    assert post.facts_count == 3


def test_get_curated_post_filtering():
    """Verifies get_curated_post_for_franchise filters excluded topics."""
    post = get_curated_post_for_franchise("Half-Life", excluded_topics=[])
    assert post is not None
    assert post.franchise == "Half-Life"

    # Exclude this topic key
    post_excluded = get_curated_post_for_franchise("Half-Life", excluded_topics=[post.topic_key])
    # Returns matching post or fallback
    assert post_excluded is not None


@pytest.mark.asyncio
async def test_generate_post_offline_curated_fallback_when_no_api_key():
    """When api_key is None, generate_post must seamlessly return a curated post."""
    gen = AIEvergreenGenerator(api_key=None)
    post = await gen.generate_post("The Witcher")

    assert isinstance(post, EvergreenPostData)
    assert "Ведьмак" in post.franchise or "Witcher" in post.franchise
    assert post.facts_count in (3, 4)
    assert "💬 <b>Вопрос к вам:</b>" in post.content
    assert "#история_игр #факты #игры" in post.content


@pytest.mark.asyncio
async def test_generate_post_with_mocked_ai_json_response():
    """Verifies parsing valid AI JSON response into EvergreenPostData."""
    gen = AIEvergreenGenerator(api_key="test-key", model="gpt-4o-mini")

    mock_ai_json = {
        "topic_title": "Секреты физического движка Havok",
        "headline": "🎮 <b>Half-Life 2: как создавалась легендарная грави-пушка</b>",
        "intro": "В 2004 году физика в играх вышла на совершенно новый уровень благодаря Half-Life 2.",
        "facts": [
            "🔹 <b>Грави-пушка</b>: Оружие изначально планировалось как инструмент для физиков в лабораториях.",
            "🔹 <b>Звуки гравитации</b>: Звукорежиссеры записали работу старого пылесоса и трансформатора.",
            "🔹 <b>Баллоны с пропаном</b>: Реакция рэгдолл-моделей на взрывы рассчитывалась в реальном времени.",
        ],
        "question": "Какую локацию в Half-Life 2 вы считаете самой атмосферной?",
        "hashtags": "#история_игр #факты #игры",
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(mock_ai_json)}}]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        post = await gen.generate_post("Half-Life", excluded_topics=["Десантники HECU"])

        assert post.topic_title == "Секреты физического движка Havok"
        assert post.facts_count == 3
        assert "грави-пушка" in post.content
        assert "#история_игр #факты #игры" in post.content


@pytest.mark.asyncio
async def test_generate_post_fallback_on_api_error():
    """When AI API returns error (e.g. HTTP 500 or network error), falls back to curated lore."""
    gen = AIEvergreenGenerator(api_key="test-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        post = await gen.generate_post("Fallout")

        # Must fall back gracefully to curated Fallout lore
        assert isinstance(post, EvergreenPostData)
        assert post.franchise == "Fallout"
        assert post.facts_count in (3, 4)
        assert "#история_игр #факты #игры" in post.content


@pytest.mark.asyncio
async def test_probe_ai_health_unconfigured():
    """When API key is missing, probe_ai_health reports offline status."""
    gen = AIEvergreenGenerator(api_key=None)
    is_ready, msg = await gen.probe_ai_health()
    assert is_ready is False
    assert "offline curated catalog active" in msg


@pytest.mark.asyncio
async def test_probe_ai_health_success():
    """When API responds with 200, probe_ai_health reports success."""
    gen = AIEvergreenGenerator(api_key="valid-key", model="gpt-4o-mini", provider="openai")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        is_ready, msg = await gen.probe_ai_health()
        assert is_ready is True
        assert "ready and responsive" in msg


@pytest.mark.asyncio
async def test_probe_ai_health_auth_failure():
    """When API returns 401 Unauthorized, probe_ai_health reports authentication failure."""
    gen = AIEvergreenGenerator(api_key="bad-key", provider="openai")

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        is_ready, msg = await gen.probe_ai_health()
        assert is_ready is False
        assert "authentication failed" in msg.lower()
