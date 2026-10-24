"""
Unit tests for gamebot.scheduler module.
Verifies QuietPeriodScheduler news gap logic (>6h), active daytime window (10:00-22:00),
24-hour evergreen cooldown, status diagnostics, and end-to-end trigger cycle.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from gamebot.database import Database
from gamebot.scheduler import QuietPeriodScheduler
from gamebot.ai import AIEvergreenGenerator, EvergreenPostData


@pytest.fixture
def mem_db():
    """Provides an isolated in-memory SQLite database instance."""
    db = Database(":memory:")
    yield db
    db.close()


def test_should_trigger_outside_active_hours(mem_db):
    """At 03:00 AM (outside 10:00-22:00), scheduler must not trigger."""
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    night_time = datetime(2026, 10, 23, 3, 30, 0)
    assert scheduler.should_trigger_evergreen(night_time) is False


def test_should_trigger_inside_active_hours_no_prior_posts(mem_db):
    """At 14:00 with no prior news or evergreen posts, scheduler triggers."""
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    day_time = datetime(2026, 10, 23, 14, 0, 0)
    assert scheduler.should_trigger_evergreen(day_time) is True


def test_should_trigger_blocked_by_recent_news(mem_db):
    """When news was posted 2 hours ago (< 6h quiet threshold), scheduler must not trigger."""
    scheduler = QuietPeriodScheduler(mem_db, quiet_period_hours=6.0, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 15, 0, 0)

    # Record news 2 hours ago
    mem_db.record_news_post(
        news_id="deal_recent",
        source="steam",
        title="Recent Game Deal",
        url="https://store.steampowered.com/app/1",
        posted_at=now - timedelta(hours=2),
    )

    assert scheduler.should_trigger_evergreen(now) is False


def test_should_trigger_allowed_after_6_hours_news_gap(mem_db):
    """When last news was posted 7 hours ago (> 6h threshold), scheduler must trigger."""
    scheduler = QuietPeriodScheduler(mem_db, quiet_period_hours=6.0, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 17, 0, 0)

    # Record news 7 hours ago
    mem_db.record_news_post(
        news_id="deal_old",
        source="steam",
        title="Old Game Deal",
        url="https://store.steampowered.com/app/2",
        posted_at=now - timedelta(hours=7),
    )

    assert scheduler.should_trigger_evergreen(now) is True


def test_should_trigger_blocked_by_evergreen_cooldown(mem_db):
    """When evergreen post was published 12 hours ago (< 24h cooldown), scheduler must not trigger."""
    scheduler = QuietPeriodScheduler(
        mem_db,
        quiet_period_hours=6.0,
        min_evergreen_cooldown_hours=24.0,
        active_hours_range=(10, 22),
    )
    now = datetime(2026, 10, 23, 16, 0, 0)

    # News posted 10 hours ago (quiet)
    mem_db.record_news_post(
        news_id="deal_old",
        source="steam",
        title="Old Deal",
        url="https://store.steampowered.com/app/3",
        posted_at=now - timedelta(hours=10),
    )

    # Evergreen posted 12 hours ago (cooldown active!)
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_test",
        topic_title="HL",
        content="...",
        posted_at=now - timedelta(hours=12),
    )

    assert scheduler.should_trigger_evergreen(now) is False


def test_should_trigger_allowed_after_24_hours_evergreen_cooldown(mem_db):
    """When evergreen was published 25 hours ago (> 24h cooldown), scheduler triggers."""
    scheduler = QuietPeriodScheduler(
        mem_db,
        quiet_period_hours=6.0,
        min_evergreen_cooldown_hours=24.0,
        active_hours_range=(10, 22),
    )
    now = datetime(2026, 10, 23, 16, 0, 0)

    # News posted 8 hours ago
    mem_db.record_news_post(
        news_id="deal_old",
        source="steam",
        title="Old Deal",
        url="https://store.steampowered.com/app/4",
        posted_at=now - timedelta(hours=8),
    )

    # Evergreen posted 25 hours ago
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_test",
        topic_title="HL",
        content="...",
        posted_at=now - timedelta(hours=25),
    )

    assert scheduler.should_trigger_evergreen(now) is True


def test_scheduler_get_status_diagnostics(mem_db):
    """Verifies get_status returns complete diagnostic information."""
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 12, 0, 0)

    status = scheduler.get_status(now)
    assert status["in_active_hours"] is True
    assert status["should_trigger"] is True
    assert "Quiet period condition satisfied" in status["reason"]


@pytest.mark.asyncio
async def test_trigger_evergreen_cycle_execution(mem_db):
    """Verifies trigger_evergreen_cycle generates, publishes, and records post in DB."""
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 14, 0, 0)

    # Mock generator
    mock_generator = MagicMock()
    mock_post_data = EvergreenPostData(
        franchise="The Witcher",
        topic_key="witcher_test_key",
        topic_title="Секреты Ведьмака",
        content="🎮 <b>Ведьмак 3</b>\n\nИнтро...\n\n🔹 Факт 1\n🔹 Факт 2\n🔹 Факт 3\n\n💬 Вопрос\n\n#история_игр #факты #игры",
        facts_count=3,
        question="Любимая часть?",
        hashtags="#история_игр #факты #игры",
    )
    mock_generator.generate_post = AsyncMock(return_value=mock_post_data)

    # Mock publisher
    mock_publisher = MagicMock()
    mock_publisher.send_evergreen_to_channel = AsyncMock(return_value=42)

    row_id = await scheduler.trigger_evergreen_cycle(
        generator=mock_generator,
        publisher=mock_publisher,
        all_franchises=["The Witcher", "Fallout"],
        current_time=now,
    )

    assert row_id is not None
    assert row_id > 0
    assert mem_db.count_evergreen_posts() == 1
    mock_publisher.send_evergreen_to_channel.assert_called_once_with(mock_post_data.content)
    assert mem_db.is_topic_recent("witcher_test_key", days=180) is True


@pytest.mark.asyncio
async def test_trigger_evergreen_cycle_skipped_outside_hours(mem_db):
    """When quiet period conditions are not met, cycle is skipped."""
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    night_time = datetime(2026, 10, 23, 2, 0, 0)

    mock_generator = MagicMock()
    mock_publisher = MagicMock()

    result = await scheduler.trigger_evergreen_cycle(
        generator=mock_generator,
        publisher=mock_publisher,
        current_time=night_time,
    )
    assert result is None
    assert mem_db.count_evergreen_posts() == 0


def test_timezone_safety_across_offsets(mem_db):
    """Verifies timezone safety across UTC+0, UTC+3, and UTC+7."""
    scheduler = QuietPeriodScheduler(mem_db, quiet_period_hours=6.0, active_hours_range=(10, 22))

    now_utc = datetime(2026, 10, 23, 14, 0, 0, tzinfo=timezone.utc)
    # News posted 2 hours ago in UTC (at 12:00:00 UTC)
    mem_db.record_news_post(
        news_id="deal_recent_utc",
        source="steam",
        title="Recent Steam Deal",
        url="https://store.steampowered.com/app/10",
        posted_at=now_utc - timedelta(hours=2),
    )

    # Test with UTC+0 (14:00 UTC)
    assert scheduler.should_trigger_evergreen(now_utc) is False

    # Test with UTC+3 (17:00 local time = 14:00 UTC)
    now_utc3 = datetime(2026, 10, 23, 17, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    assert scheduler.should_trigger_evergreen(now_utc3) is False

    # Test with UTC+7 (21:00 local time = 14:00 UTC)
    now_utc7 = datetime(2026, 10, 23, 21, 0, 0, tzinfo=timezone(timedelta(hours=7)))
    assert scheduler.should_trigger_evergreen(now_utc7) is False


@pytest.mark.asyncio
async def test_trigger_evergreen_cycle_publisher_failure_containment(mem_db):
    """When Telegram publishing fails, post must NOT be recorded into DB, preventing 24h lockout."""
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 14, 0, 0)

    mock_generator = MagicMock()
    mock_post_data = EvergreenPostData(
        franchise="Half-Life",
        topic_key="hl_ai_test",
        topic_title="ИИ десантников",
        content="🎮 <b>Half-Life</b>\n\nИнтро...\n\n🔹 Факт 1\n🔹 Факт 2\n🔹 Факт 3\n\n💬 Вопрос\n\n#история_игр #факты #игры",
        facts_count=3,
        question="Вопрос?",
        hashtags="#история_игр #факты #игры",
    )
    mock_generator.generate_post = AsyncMock(return_value=mock_post_data)

    # Mock publisher returning None (failed delivery)
    mock_publisher = MagicMock()
    mock_publisher.send_evergreen_to_channel = AsyncMock(return_value=None)

    result = await scheduler.trigger_evergreen_cycle(
        generator=mock_generator,
        publisher=mock_publisher,
        current_time=now,
    )

    # Must return None and not insert row into DB
    assert result is None
    assert mem_db.count_evergreen_posts() == 0
    assert mem_db.is_topic_recent("hl_ai_test", days=180) is False

    # Scheduler must still evaluate to should_trigger_evergreen == True (cooldown NOT burned!)
    assert scheduler.should_trigger_evergreen(now) is True

    # Test with exception during sending
    mock_publisher.send_evergreen_to_channel = AsyncMock(side_effect=RuntimeError("Network down"))
    result2 = await scheduler.trigger_evergreen_cycle(
        generator=mock_generator,
        publisher=mock_publisher,
        current_time=now,
    )
    assert result2 is None
    assert mem_db.count_evergreen_posts() == 0
    assert scheduler.should_trigger_evergreen(now) is True


@pytest.mark.asyncio
async def test_offline_lru_rotation_ten_cycles(mem_db):
    """Verifies that in offline mode (api_key=None), 10 consecutive cycles rotate cleanly across 10 distinct curated franchises."""
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    generator = AIEvergreenGenerator(api_key=None)

    start_time = datetime(2026, 10, 1, 12, 0, 0)
    seen_franchises = set()

    for i in range(10):
        current_time = start_time + timedelta(days=i)
        row_id = await scheduler.trigger_evergreen_cycle(
            generator=generator,
            publisher=None,  # Test/dry-run mode
            current_time=current_time,
        )
        assert row_id is not None
        assert row_id > 0

    assert mem_db.count_evergreen_posts() == 10

    # Retrieve all 10 recorded franchises
    conn = mem_db.get_connection()
    cur = conn.execute("SELECT franchise FROM evergreen_posts ORDER BY id ASC;")
    recorded_franchises = [row["franchise"] for row in cur.fetchall()]
    conn.close()

    # Verify all 10 posts represent 10 distinct franchises
    assert len(set(recorded_franchises)) == 10


@pytest.mark.asyncio
async def test_trigger_evergreen_post_generation_duplicate_guard(mem_db):
    """Verifies that if generator generates a duplicate topic, the post-generation guard intercepts it."""
    scheduler = QuietPeriodScheduler(mem_db, active_hours_range=(10, 22))
    now = datetime(2026, 10, 23, 14, 0, 0)

    # Pre-record a topic for Half-Life
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="half_life_ai_and_sound_sensors",
        topic_title="Революционный ИИ десантников",
        content="Текст...",
        posted_at=now - timedelta(days=5),
    )

    # Mock generator that hallucinates this exact same topic_key
    mock_generator = MagicMock()
    duplicate_post = EvergreenPostData(
        franchise="Half-Life",
        topic_key="half_life_ai_and_sound_sensors",
        topic_title="Революционный ИИ десантников",
        content="Текст...",
        facts_count=3,
        question="Вопрос?",
        hashtags="#история_игр #факты #игры",
    )
    mock_generator.generate_post = AsyncMock(return_value=duplicate_post)

    # Set up curated fallback returning an unexcluded alternative
    alt_entry = EvergreenPostData(
        franchise="Fallout",
        topic_key="fallout_vault_77_puppets",
        topic_title="Убежище 77 и марионетки",
        content="🎮 <b>Fallout</b>\n\nИнтро...\n\n🔹 Факт 1\n🔹 Факт 2\n🔹 Факт 3\n\n💬 Вопрос\n\n#история_игр #факты #игры",
        facts_count=3,
        question="Вопрос?",
        hashtags="#история_игр #факты #игры",
    )
    mock_generator._curated_fallback = MagicMock(return_value=alt_entry)

    # Let's advance time by 25 hours to clear cooldown
    trigger_time = now + timedelta(hours=25)
    row_id = await scheduler.trigger_evergreen_cycle(
        generator=mock_generator,
        publisher=None,
        all_franchises=["Half-Life", "Fallout"],
        current_time=trigger_time,
    )

    assert row_id is not None
    # Verify the fallback topic was recorded, NOT the hallucinated duplicate
    assert mem_db.is_topic_recent("fallout_vault_77_puppets", days=180) is True

