"""
Unit tests for gamebot.database module.
Verifies SQLite schema creation, 180-day deduplication boundary conditions (179 vs 181 days),
LRU franchise rotation, news tracking, and timestamp parsing.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
import pytest

from gamebot.database import Database, _parse_datetime


@pytest.fixture
def mem_db():
    """Provides an isolated in-memory SQLite database instance."""
    db = Database(":memory:")
    yield db
    db.close()


@pytest.fixture
def file_db():
    """Provides a temporary file-based SQLite database instance."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    db = Database(db_path)
    yield db
    db.close()
    if os.path.exists(db_path):
        os.remove(db_path)


def test_init_db_in_memory(mem_db):
    """Verifies tables and indexes exist in memory database."""
    assert mem_db.count_evergreen_posts() == 0
    assert mem_db.count_news_posts() == 0


def test_init_db_file_database(file_db):
    """Verifies file database is created and initialized."""
    assert file_db.count_evergreen_posts() == 0
    assert file_db.count_news_posts() == 0


def test_record_evergreen_post(mem_db):
    """Verifies inserting evergreen post returns valid row ID and increments count."""
    row_id = mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="half_life_ai",
        topic_title="Революционный ИИ десантников",
        content="Текст поста о десантниках HECU...",
        facts_count=4,
        question="Помните первый бой?",
        hashtags="#история_игр #факты #игры",
        channel_message_id=101,
    )
    assert row_id > 0
    assert mem_db.count_evergreen_posts() == 1


def test_is_topic_recent_fresh_post(mem_db):
    """Topic posted just now should be detected as recent within 180 days."""
    mem_db.record_evergreen_post(
        franchise="The Witcher",
        topic_key="witcher_bloody_baron",
        topic_title="Квест Кровавого Барона",
        content="Текст...",
        facts_count=3,
    )
    assert mem_db.is_topic_recent("witcher_bloody_baron", days=180) is True
    assert mem_db.is_topic_recent("unknown_topic", days=180) is False


def test_is_topic_recent_case_insensitive(mem_db):
    """Deduplication should match topic keys regardless of case."""
    mem_db.record_evergreen_post(
        franchise="BioShock",
        topic_key="bioshock_would_you_kindly",
        topic_title="Смысл Would you kindly",
        content="Текст...",
    )
    assert mem_db.is_topic_recent("BIOSHOCK_WOULD_YOU_KINDLY", days=180) is True
    assert mem_db.is_topic_recent("BioShock_Would_You_Kindly", days=180) is True


def test_deduplication_boundary_179_days(mem_db):
    """Post recorded 179 days ago is WITHIN the 180-day window -> is_topic_recent is True."""
    ts_179_days_ago = datetime.now() - timedelta(days=179)
    mem_db.record_evergreen_post(
        franchise="Fallout",
        topic_key="fallout_vault_77",
        topic_title="Убежище 77 и марионетки",
        content="Текст...",
        posted_at=ts_179_days_ago,
    )

    assert mem_db.is_topic_recent("fallout_vault_77", days=180) is True


def test_deduplication_boundary_181_days(mem_db):
    """Post recorded 181 days ago is OUTSIDE the 180-day window -> is_topic_recent is False."""
    ts_181_days_ago = datetime.now() - timedelta(days=181)
    mem_db.record_evergreen_post(
        franchise="Fallout",
        topic_key="fallout_water_chip",
        topic_title="Водяной чип Убежища 13",
        content="Текст...",
        posted_at=ts_181_days_ago,
    )

    assert mem_db.is_topic_recent("fallout_water_chip", days=180) is False


def test_get_recent_topics_for_franchise(mem_db):
    """Verifies fetching recent topics for negative prompt constraint."""
    now = datetime.now()
    # 2 recent topics for S.T.A.L.K.E.R.
    mem_db.record_evergreen_post(
        franchise="S.T.A.L.K.E.R.",
        topic_key="stalker_alife",
        topic_title="Система A-Life",
        content="...",
        posted_at=now - timedelta(days=10),
    )
    mem_db.record_evergreen_post(
        franchise="S.T.A.L.K.E.R.",
        topic_key="stalker_guitar",
        topic_title="Гитара у костра",
        content="...",
        posted_at=now - timedelta(days=50),
    )
    # 1 expired topic (>180 days) for S.T.A.L.K.E.R.
    mem_db.record_evergreen_post(
        franchise="S.T.A.L.K.E.R.",
        topic_key="stalker_monolith",
        topic_title="Тайна Монолита",
        content="...",
        posted_at=now - timedelta(days=200),
    )
    # 1 topic for another franchise
    mem_db.record_evergreen_post(
        franchise="Portal",
        topic_key="portal_cake",
        topic_title="Торт — это ложь",
        content="...",
        posted_at=now - timedelta(days=5),
    )

    stalker_topics = mem_db.get_recent_topics_for_franchise("S.T.A.L.K.E.R.", days=180)
    assert len(stalker_topics) == 2
    assert "Система A-Life" in stalker_topics
    assert "Гитара у костра" in stalker_topics
    assert "Тайна Монолита" not in stalker_topics
    assert "Торт — это ложь" not in stalker_topics


def test_least_recently_used_franchise_unposted_prioritized(mem_db):
    """Unposted franchises must be selected before any already posted franchise."""
    now = datetime.now()
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_test",
        topic_title="HL",
        content="...",
        posted_at=now - timedelta(days=5),
    )
    mem_db.record_evergreen_post(
        franchise="Fallout",
        topic_key="fo_test",
        topic_title="FO",
        content="...",
        posted_at=now - timedelta(days=20),
    )

    all_franchises = ["Half-Life", "Fallout", "BioShock", "Portal"]
    # BioShock and Portal are unposted; BioShock comes first among unposted
    chosen = mem_db.get_least_recently_used_franchise(all_franchises)
    assert chosen in ["BioShock", "Portal"]


def test_least_recently_used_franchise_rotation_when_all_posted(mem_db):
    """When all franchises have been posted, the one with the oldest timestamp is chosen."""
    now = datetime.now()
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_1",
        topic_title="HL",
        content="...",
        posted_at=now - timedelta(days=10),
    )
    mem_db.record_evergreen_post(
        franchise="Fallout",
        topic_key="fo_1",
        topic_title="FO",
        content="...",
        posted_at=now - timedelta(days=40),  # Oldest!
    )
    mem_db.record_evergreen_post(
        franchise="BioShock",
        topic_key="bs_1",
        topic_title="BS",
        content="...",
        posted_at=now - timedelta(days=5),
    )

    candidates = ["Half-Life", "Fallout", "BioShock"]
    chosen = mem_db.get_least_recently_used_franchise(candidates)
    assert chosen == "Fallout"


def test_least_recently_used_franchise_empty_raises(mem_db):
    """Empty franchise list must raise ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        mem_db.get_least_recently_used_franchise([])


def test_news_tracking(mem_db):
    """Verifies recording news and checking is_news_posted."""
    assert mem_db.get_last_news_post_time() is None

    now = datetime(2026, 10, 23, 15, 30, 0)
    row_id = mem_db.record_news_post(
        news_id="deal_1091500",
        source="steam",
        title="Cyberpunk 2077 -50%",
        url="https://store.steampowered.com/app/1091500",
        posted_at=now,
    )
    assert row_id > 0
    assert mem_db.count_news_posts() == 1
    assert mem_db.is_news_posted("deal_1091500") is True
    assert mem_db.is_news_posted("deal_unknown") is False

    last_news_time = mem_db.get_last_news_post_time()
    assert last_news_time is not None
    assert last_news_time.year == 2026
    assert last_news_time.hour == 15


def test_get_last_evergreen_post_time(mem_db):
    """Verifies retrieving last evergreen post timestamp."""
    assert mem_db.get_last_evergreen_post_time() is None

    ts = datetime(2026, 10, 20, 12, 0, 0)
    mem_db.record_evergreen_post(
        franchise="Dark Souls & Bloodborne",
        topic_key="ds_solair",
        topic_title="Рыцарь Солер",
        content="Текст...",
        posted_at=ts,
    )

    last_time = mem_db.get_last_evergreen_post_time()
    assert last_time is not None
    assert last_time == ts


def test_parse_datetime_helper():
    """Verifies _parse_datetime with diverse SQLite string formats."""
    assert _parse_datetime(None) is None
    assert _parse_datetime("") is None
    dt = _parse_datetime("2026-10-23 20:30:00")
    assert dt == datetime(2026, 10, 23, 20, 30, 0)
    dt_iso = _parse_datetime("2026-10-23T20:30:00")
    assert dt_iso == datetime(2026, 10, 23, 20, 30, 0)
    dt_z = _parse_datetime("2026-10-23T20:30:00Z")
    assert dt_z == datetime(2026, 10, 23, 20, 30, 0)
    # Test timezone offset conversion to UTC naive datetime
    dt_offset = _parse_datetime("2026-10-23T18:00:00+03:00")
    assert dt_offset == datetime(2026, 10, 23, 15, 0, 0)


def test_record_posts_default_utc_timestamp(mem_db):
    """Verifies default timestamp is recorded in UTC string format."""
    now_utc = datetime.now(timezone.utc)
    row_id = mem_db.record_evergreen_post(
        franchise="DOOM",
        topic_key="doom_bsp_trees",
        topic_title="Алгоритмы Кармака",
        content="Текст...",
    )
    assert row_id > 0
    last_time = mem_db.get_last_evergreen_post_time()
    assert last_time is not None
    # Difference between last_time and now_utc should be within 5 seconds
    delta = abs((now_utc.replace(tzinfo=None) - last_time).total_seconds())
    assert delta < 5.0


def test_record_posts_with_aware_datetime_converted_to_utc(mem_db):
    """Verifies timezone-aware datetimes are converted to UTC before insertion."""
    local_offset = timezone(timedelta(hours=3))
    aware_time = datetime(2026, 10, 23, 18, 0, 0, tzinfo=local_offset)
    row_id = mem_db.record_evergreen_post(
        franchise="Gothic",
        topic_key="gothic_barrier",
        topic_title="Барьер Рудниковой долины",
        content="Текст...",
        posted_at=aware_time,
    )
    assert row_id > 0
    last_time = mem_db.get_last_evergreen_post_time()
    # 18:00 UTC+3 is 15:00 UTC
    assert last_time == datetime(2026, 10, 23, 15, 0, 0)


def test_collate_nocase_lru_franchise_grouping(mem_db):
    """Verifies COLLATE NOCASE groups franchise names regardless of letter casing."""
    t1 = datetime(2026, 10, 20, 10, 0, 0)
    t2 = datetime(2026, 10, 22, 10, 0, 0)
    mem_db.record_evergreen_post(
        franchise="resident evil",
        topic_key="re_1",
        topic_title="RE 1",
        content="...",
        posted_at=t1,
    )
    mem_db.record_evergreen_post(
        franchise="RESIDENT EVIL",
        topic_key="re_2",
        topic_title="RE 2",
        content="...",
        posted_at=t2,
    )
    mem_db.record_evergreen_post(
        franchise="Silent Hill",
        topic_key="sh_1",
        topic_title="SH 1",
        content="...",
        posted_at=t1,
    )

    # Resident Evil's latest post was at t2, while Silent Hill was at t1
    chosen = mem_db.get_least_recently_used_franchise(["Resident Evil", "Silent Hill"])
    assert chosen == "Silent Hill"

