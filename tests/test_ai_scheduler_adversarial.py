"""
Adversarial Stress Test Suite for Milestone 2: AI Evergreen Content & Quiet-Period Scheduler.
Challenges:
1. Missing/empty/whitespace AI_API_KEY and guaranteed offline curated fallback.
2. Format & hashtag strict compliance, Cyrillic language ratio, and malformed post rejection.
3. Negative prompt constraint injection with 10+ and 25+ excluded topics and unicode/punctuation resilience.
4. Quiet-period scheduler edge timing boundaries:
   - News gap: 5.9h vs 6.0h vs 6.1h.
   - Evergreen cooldown: 23.9h vs 24.0h vs 24.1h.
   - Active hours: 09:59 vs 10:00 vs 10:01 vs 22:59 vs 23:00.
   - Timezone-aware vs naive datetime interoperability.
   - Clock drift / future timestamp safety.
5. Exception containment in trigger_evergreen_cycle (publisher failures, API crashes, missing publisher).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from gamebot.ai import (
    AIEvergreenGenerator,
    EvergreenPostData,
    ICONIC_FRANCHISES,
    DEFAULT_HASHTAGS,
)
from gamebot.curated_lore import (
    CURATED_POSTS,
    CuratedLoreEntry,
    get_all_curated_franchises,
    get_curated_post_for_franchise,
    get_any_unexcluded_curated_post,
)
from gamebot.database import Database
from gamebot.scheduler import QuietPeriodScheduler


@pytest.fixture
def mem_db():
    """Provides an isolated in-memory SQLite database instance."""
    db = Database(":memory:")
    yield db
    db.close()


# ==============================================================================
# 1. AI_API_KEY BOUNDARY CONDITIONS & OFFLINE CURATED FALLBACK
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("empty_key", [None, "", "   ", "\t\n"])
async def test_ai_api_key_missing_empty_whitespace_resilience(empty_key):
    """
    Adversarial Challenge 1.1:
    When AI_API_KEY is completely missing, empty, or whitespace,
    AIEvergreenGenerator must initialize safely with api_key=None
    and generate_post() must seamlessly return a valid curated post without exceptions.
    """
    with patch.dict(os.environ, {}, clear=True):
        gen = AIEvergreenGenerator(api_key=empty_key)
        assert gen.api_key is None

        # Test with standard franchise
        post = await gen.generate_post("Half-Life")
        assert isinstance(post, EvergreenPostData)
        assert post.franchise == "Half-Life"
        assert post.facts_count in (3, 4)
        assert len(post.content) >= 300
        assert "#история_игр #факты #игры" in post.content
        assert "💬 <b>Вопрос к вам:</b>" in post.content

        # Test with unknown/unlisted franchise: should fall back to any unexcluded post
        unknown_post = await gen.generate_post("NonExistentFranchise_12345")
        assert isinstance(unknown_post, EvergreenPostData)
        assert unknown_post.facts_count in (3, 4)
        assert len(unknown_post.content) >= 300
        assert "#история_игр #факты #игры" in unknown_post.content


@pytest.mark.asyncio
async def test_generate_post_with_all_curated_franchises_excluded():
    """
    Adversarial Challenge 1.2:
    Even when excluded_topics contains every single curated post's key,
    the generator still safely returns a valid curated post without raising IndexError.
    """
    all_keys = [p.topic_key for p in CURATED_POSTS]
    all_titles = [p.topic_title for p in CURATED_POSTS]
    excluded = all_keys + all_titles

    with patch.dict(os.environ, {}, clear=True):
        gen = AIEvergreenGenerator(api_key=None)
        post = await gen.generate_post("Half-Life", excluded_topics=excluded)

        assert isinstance(post, EvergreenPostData)
        assert post.facts_count in (3, 4)
        assert "#история_игр #факты #игры" in post.hashtags


# ==============================================================================
# 2. FORMAT, LANGUAGE, FACT COUNT & EXACT RUBRIC HASHTAG COMPLIANCE
# ==============================================================================

def test_all_curated_posts_strictly_comply_with_rubric():
    """
    Adversarial Challenge 2.1:
    Verifies that every entry in CURATED_POSTS strictly satisfies:
    1. Russian language (Cyrillic character ratio >= 60%).
    2. Hook headline starting with '🎮 <b>'.
    3. Exactly 3 or 4 fact bullet points starting with '🔹 <b>'.
    4. Audience question ending with '?' and >= 15 characters.
    5. Exact hashtags: '#история_игр #факты #игры'.
    6. Formatted post contains '💬 <b>Вопрос к вам:</b>'.
    """
    assert len(CURATED_POSTS) >= 10, "Curated catalog must contain at least 10 entries"

    for entry in CURATED_POSTS:
        # 1. Headline format
        assert entry.headline.startswith("🎮 <b>"), f"Invalid headline in {entry.franchise}: {entry.headline}"

        # 2. Facts count (3-4 facts)
        assert 3 <= len(entry.facts) <= 4, f"Facts count not in 3..4 for {entry.franchise}: {len(entry.facts)}"
        for fact in entry.facts:
            assert fact.startswith("🔹 <b>"), f"Fact missing bullet prefix in {entry.franchise}: {fact}"

        # 3. Question format
        assert len(entry.question.strip()) >= 15, f"Question too short in {entry.franchise}"
        assert entry.question.strip().endswith("?"), f"Question must end with '?' in {entry.franchise}"

        # 4. Exact hashtags
        assert entry.hashtags == "#история_игр #факты #игры"

        # 5. Formatted output checks
        formatted = entry.format_post()
        assert "💬 <b>Вопрос к вам:</b>" in formatted
        assert entry.hashtags in formatted

        # 6. Cyrillic language ratio
        cyrillic = len(re.findall(r"[\u0400-\u04FF]", formatted))
        alpha = len(re.findall(r"[a-zA-Z\u0400-\u04FF]", formatted))
        assert alpha > 0
        cyrillic_ratio = cyrillic / alpha
        assert cyrillic_ratio >= 0.60, f"Insufficient Cyrillic in {entry.franchise}: ratio={cyrillic_ratio}"


def test_validate_post_rejects_malformed_and_foreign_content():
    """
    Adversarial Challenge 2.2:
    _validate_post must aggressively reject invalid posts:
    - Pure English text (no Cyrillic).
    - Under 300 characters or over 4000 characters.
    - Facts count outside [3..6].
    - Missing audience question.
    - Missing required rubric hashtags.
    """
    gen = AIEvergreenGenerator(api_key="mock-key")

    # Base valid post
    valid_data = EvergreenPostData(
        franchise="Half-Life",
        topic_key="hl_test",
        topic_title="Тестовый заголовок",
        content=(
            "🎮 <b>Half-Life 2</b>\n\n"
            "В 2004 году физика в играх вышла на совершенно новый уровень благодаря движку Source и грави-пушке.\n\n"
            "🔹 <b>Факт 1</b>: Оружие изначально планировалось как инструмент для физиков в лабораториях.\n\n"
            "🔹 <b>Факт 2</b>: Звукорежиссеры записали работу старого пылесоса и трансформатора для звука гравитации.\n\n"
            "🔹 <b>Факт 3</b>: Реакция рэгдолл-моделей на взрывы рассчитывалась в реальном времени.\n\n"
            "💬 <b>Вопрос к вам:</b> Какая локация в Half-Life 2 запомнилась вам сильнее всего?\n\n"
            "#история_игр #факты #игры"
        ),
        facts_count=3,
        question="Какая локация в Half-Life 2 запомнилась вам сильнее всего?",
        hashtags="#история_игр #факты #игры",
    )
    assert gen._validate_post(valid_data) is True

    # Attack 1: Pure English
    english_data = EvergreenPostData(
        franchise=valid_data.franchise,
        topic_key=valid_data.topic_key,
        topic_title="English title",
        content=(
            "🎮 <b>Half-Life 2</b>\n\n"
            "In 2004, gaming physics reached a brand new level thanks to the Source engine and gravity gun.\n\n"
            "🔹 <b>Fact 1</b>: The gravity gun was originally developed as a physics tool for lab researchers.\n\n"
            "🔹 <b>Fact 2</b>: Sound designers recorded a vacuum cleaner and power transformers.\n\n"
            "🔹 <b>Fact 3</b>: Ragdoll physics reactions to explosions were calculated in real-time.\n\n"
            "💬 <b>Вопрос к вам:</b> Which location in Half-Life 2 was your favorite one?\n\n"
            "#история_игр #факты #игры"
        ),
        facts_count=3,
        question="Which location in Half-Life 2 was your favorite one?",
        hashtags="#история_игр #факты #игры",
    )
    assert gen._validate_post(english_data) is False, "Pure English post must be rejected"

    # Attack 2: Too short (<300 chars)
    short_data = EvergreenPostData(
        franchise=valid_data.franchise,
        topic_key=valid_data.topic_key,
        topic_title="Короткий пост",
        content="Короткий текст поста с #история_игр #факты #игры",
        facts_count=3,
        question="Вопрос к вам?",
        hashtags="#история_игр #факты #игры",
    )
    assert gen._validate_post(short_data) is False, "Short content must be rejected"

    # Attack 3: Too few facts (facts_count = 1)
    few_facts_data = EvergreenPostData(
        franchise=valid_data.franchise,
        topic_key=valid_data.topic_key,
        topic_title=valid_data.topic_title,
        content=valid_data.content,
        facts_count=1,
        question=valid_data.question,
        hashtags=valid_data.hashtags,
    )
    assert gen._validate_post(few_facts_data) is False, "Fewer than 3 facts must be rejected"

    # Attack 4: Missing hashtag
    missing_tag_data = EvergreenPostData(
        franchise=valid_data.franchise,
        topic_key=valid_data.topic_key,
        topic_title=valid_data.topic_title,
        content=valid_data.content.replace("#факты", "#новости"),
        facts_count=3,
        question=valid_data.question,
        hashtags="#история_игр #новости #игры",
    )
    assert gen._validate_post(missing_tag_data) is False, "Missing required hashtag must be rejected"


# ==============================================================================
# 3. NEGATIVE PROMPT CONSTRAINT INJECTION (10+ & 25+ EXCLUDED TOPICS)
# ==============================================================================

def test_negative_prompt_injection_with_10_plus_topics():
    """
    Adversarial Challenge 3.1:
    Verify that negative prompt constraint injection works seamlessly when
    excluded topics list is large (10+ topics) and contains complex characters.
    """
    gen = AIEvergreenGenerator(api_key="mock-key")
    excluded = [
        "1. Революционный ИИ десантников HECU",
        "2. Звуковая карта распространения звука",
        "3. Монтировка Гордона Фримена",
        "4. Взрыв микроволновки в Black Mesa",
        "5. Загадочный G-Man и его дипломат",
        "6. Нихилант и измерение Зен",
        "7. Щупальца в ракетной шахте",
        "8. Ученые Black Mesa и хедкрабы",
        "9. Барни Калхаун и Blue Shift",
        "10. Адриан Шепард и Opposing Force",
        "11. Вырезанные монстры и глава гидроэлектростанции",
        "12. Скорость движения Гордона в оригинальной игре",
    ]

    prompt = gen.build_user_prompt("Half-Life", excluded_topics=excluded)

    assert "ВАЖНОЕ ОГРАНИЧЕНИЕ (АНТИ-ПОВТОРЫ):" in prompt
    assert "СТРОГО ЗАПРЕЩЕНО повторять:" in prompt
    for topic in excluded:
        assert topic in prompt, f"Excluded topic '{topic}' missing from negative prompt"


def test_negative_prompt_injection_caps_at_25_topics_safely():
    """
    Adversarial Challenge 3.2:
    When excluded list contains 35 topics, it safely caps at 25 items
    without unbounded prompt explosion.
    """
    gen = AIEvergreenGenerator(api_key="mock-key")
    topics_35 = [f"Тема номер {i}" for i in range(1, 36)]

    prompt = gen.build_user_prompt("Fallout", excluded_topics=topics_35)

    assert "Тема номер 1" in prompt
    assert "Тема номер 25" in prompt
    assert "Тема номер 26" not in prompt, "Prompt should cap at 25 topics"


def test_negative_prompt_injection_handles_empty_and_whitespace_items():
    """
    Adversarial Challenge 3.3:
    Excluded list containing empty strings or whitespace entries is sanitized cleanly.
    """
    gen = AIEvergreenGenerator(api_key="mock-key")
    dirty_excluded = ["", "  ", "Действительная тема 1", "\n\t", "Действительная тема 2", "   "]

    prompt = gen.build_user_prompt("BioShock", excluded_topics=dirty_excluded)

    assert "Действительная тема 1" in prompt
    assert "Действительная тема 2" in prompt
    assert "; ;" not in prompt


# ==============================================================================
# 4. QUIET-PERIOD SCHEDULER TIMING BOUNDARIES
# ==============================================================================

def test_scheduler_news_gap_boundary_conditions(mem_db):
    """
    Adversarial Challenge 4.1:
    Test quiet period triggers with edge news timestamps:
    - 5.9 hours ago (< 6.0h threshold) -> False (blocked)
    - 6.0 hours ago (== 6.0h threshold) -> True (allowed)
    - 6.1 hours ago (> 6.0h threshold) -> True (allowed)
    """
    scheduler = QuietPeriodScheduler(mem_db, quiet_period_hours=6.0, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 16, 0, 0)

    # Sub-test 1: 5.9 hours ago (news was at 10:06)
    mem_db.record_news_post("n1", "steam", "T1", "http://1", posted_at=now - timedelta(hours=5.9))
    assert scheduler.should_trigger_evergreen(now) is False, "News 5.9h ago must block trigger"

    # Sub-test 2: 6.0 hours ago (news was at 10:00)
    with mem_db.get_connection() as conn:
        conn.execute("DELETE FROM posted_news;")
    mem_db.record_news_post("n2", "steam", "T2", "http://2", posted_at=now - timedelta(hours=6.0))
    assert scheduler.should_trigger_evergreen(now) is True, "News 6.0h ago must allow trigger"

    # Sub-test 3: 6.1 hours ago (news was at 09:54)
    with mem_db.get_connection() as conn:
        conn.execute("DELETE FROM posted_news;")
    mem_db.record_news_post("n3", "steam", "T3", "http://3", posted_at=now - timedelta(hours=6.1))
    assert scheduler.should_trigger_evergreen(now) is True, "News 6.1h ago must allow trigger"


def test_scheduler_evergreen_cooldown_boundary_conditions(mem_db):
    """
    Adversarial Challenge 4.2:
    Test evergreen cooldown with edge timestamps:
    - 23.9 hours ago (< 24.0h cooldown) -> False (blocked)
    - 24.0 hours ago (== 24.0h cooldown) -> True (allowed)
    - 24.1 hours ago (> 24.0h cooldown) -> True (allowed)
    """
    scheduler = QuietPeriodScheduler(
        mem_db,
        quiet_period_hours=6.0,
        min_evergreen_cooldown_hours=24.0,
        active_hours_range=(10, 22),
    )
    now = datetime(2026, 10, 23, 15, 0, 0)

    # Sub-test 1: 23.9 hours ago
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_1",
        topic_title="HL",
        content="...",
        posted_at=now - timedelta(hours=23.9),
    )
    assert scheduler.should_trigger_evergreen(now) is False, "Evergreen 23.9h ago must block trigger"

    # Sub-test 2: 24.0 hours ago
    with mem_db.get_connection() as conn:
        conn.execute("DELETE FROM evergreen_posts;")
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_2",
        topic_title="HL",
        content="...",
        posted_at=now - timedelta(hours=24.0),
    )
    assert scheduler.should_trigger_evergreen(now) is True, "Evergreen 24.0h ago must allow trigger"

    # Sub-test 3: 24.1 hours ago
    with mem_db.get_connection() as conn:
        conn.execute("DELETE FROM evergreen_posts;")
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_3",
        topic_title="HL",
        content="...",
        posted_at=now - timedelta(hours=24.1),
    )
    assert scheduler.should_trigger_evergreen(now) is True, "Evergreen 24.1h ago must allow trigger"


def test_scheduler_active_hours_boundary_conditions(mem_db):
    """
    Adversarial Challenge 4.3:
    Test active hours window boundaries (10:00 - 22:00):
    - 09:59 -> False (outside window)
    - 10:00 -> True (inside window)
    - 10:01 -> True (inside window)
    - 22:59 -> True (inside window, 22:xx is inclusive)
    - 23:00 -> False (outside window)
    """
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))

    t_0959 = datetime(2026, 10, 23, 9, 59, 59)
    assert scheduler.should_trigger_evergreen(t_0959) is False, "09:59 must be rejected"

    t_1000 = datetime(2026, 10, 23, 10, 0, 0)
    assert scheduler.should_trigger_evergreen(t_1000) is True, "10:00 must be accepted"

    t_1001 = datetime(2026, 10, 23, 10, 1, 0)
    assert scheduler.should_trigger_evergreen(t_1001) is True, "10:01 must be accepted"

    t_2259 = datetime(2026, 10, 23, 22, 59, 59)
    assert scheduler.should_trigger_evergreen(t_2259) is True, "22:59 must be accepted"

    t_2300 = datetime(2026, 10, 23, 23, 0, 0)
    assert scheduler.should_trigger_evergreen(t_2300) is False, "23:00 must be rejected"


def test_scheduler_timezone_aware_and_naive_interoperability(mem_db):
    """
    Adversarial Challenge 4.4:
    QuietPeriodScheduler must handle timezone-aware `current_time`
    without raising TypeError: can't subtract offset-naive and offset-aware datetimes.
    """
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))

    # Naive timestamp in DB
    now_naive = datetime(2026, 10, 23, 14, 0, 0)
    mem_db.record_news_post("n1", "steam", "T1", "http://1", posted_at=now_naive - timedelta(hours=8))
    mem_db.record_evergreen_post("HL", "hl_1", "HL", "...", posted_at=now_naive - timedelta(hours=30))

    # Aware current_time passed to scheduler
    now_aware = datetime(2026, 10, 23, 14, 0, 0, tzinfo=timezone.utc)

    # Should compute cleanly without TypeError
    assert scheduler.should_trigger_evergreen(now_aware) is True

    status = scheduler.get_status(now_aware)
    assert status["should_trigger"] is True
    assert status["hours_since_last_news"] == 8.0
    assert status["hours_since_last_evergreen"] == 30.0


def test_scheduler_clock_drift_future_timestamp_safety(mem_db):
    """
    Adversarial Challenge 4.5:
    If a news item or evergreen post has a timestamp in the future (due to clock skew / NTP error),
    should_trigger_evergreen must safely return False and not misinterpret negative elapsed time.
    """
    scheduler = QuietPeriodScheduler(mem_db, quiet_period_hours=6.0, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 14, 0, 0)

    # News posted 10 minutes into the future!
    mem_db.record_news_post("future_deal", "steam", "Deal", "http://1", posted_at=now + timedelta(minutes=10))

    # Elapsed time is negative (-0.17h < 6.0h), which correctly indicates news was very recent / future
    assert scheduler.should_trigger_evergreen(now) is False


# ==============================================================================
# 5. EXCEPTION CONTAINMENT IN TRIGGER_EVERGREEN_CYCLE
# ==============================================================================

@pytest.mark.asyncio
async def test_trigger_evergreen_cycle_publisher_exception_containment(mem_db):
    """
    Adversarial Challenge 5.1:
    When TelegramPublisher raises an unexpected exception (e.g. network timeout, 500 error),
    trigger_evergreen_cycle must catch it, log it, and safely return None without recording
    into DB (preventing 24-hour lockout and 180-day topic burn on network glitches).
    """
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 12, 0, 0)

    # Mock generator returning valid post
    mock_gen = MagicMock(spec=AIEvergreenGenerator)
    mock_post = EvergreenPostData(
        franchise="BioShock",
        topic_key="bioshock_would_you_kindly",
        topic_title="Смысл Would you kindly",
        content="Текст поста...",
        facts_count=4,
        question="Вопрос?",
        hashtags="#история_игр #факты #игры",
    )
    mock_gen.generate_post = AsyncMock(return_value=mock_post)

    # Mock publisher raising severe exception
    mock_publisher = MagicMock()
    mock_publisher.send_evergreen_to_channel = AsyncMock(
        side_effect=RuntimeError("Catastrophic Telegram connection failure")
    )

    # Execute trigger cycle: must NOT raise RuntimeError and must return None
    row_id = await scheduler.trigger_evergreen_cycle(
        generator=mock_gen,
        publisher=mock_publisher,
        all_franchises=["BioShock"],
        current_time=now,
    )

    assert row_id is None
    assert mem_db.count_evergreen_posts() == 0
    assert mem_db.is_topic_recent("bioshock_would_you_kindly", days=180) is False


@pytest.mark.asyncio
async def test_trigger_evergreen_cycle_with_none_publisher(mem_db):
    """
    Adversarial Challenge 5.2:
    When publisher is None (e.g., dry-run mode or admin disabled publishing),
    trigger_evergreen_cycle generates and records the post without error.
    """
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 12, 0, 0)

    gen = AIEvergreenGenerator(api_key=None)  # offline curated fallback

    row_id = await scheduler.trigger_evergreen_cycle(
        generator=gen,
        publisher=None,
        all_franchises=["Half-Life"],
        current_time=now,
    )

    assert row_id is not None
    assert row_id > 0
    assert mem_db.count_evergreen_posts() == 1
