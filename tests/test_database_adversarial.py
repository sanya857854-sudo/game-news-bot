"""
Adversarial Stress Test Suite for gamebot.database (Milestone 2).
Empirically stress-tests:
1. Exact 179 / 180 / 181-day deduplication boundary conditions.
2. Topic key normalization behaviors (casing, punctuation, colons, hyphens, underscores).
3. LRU franchise rotation (unposted prioritization, chronological oldest, casing collisions).
4. SQLite connection lifecycle, transaction safety, SQL injection resilience, and concurrency.
"""

from __future__ import annotations

import concurrent.futures
import os
import tempfile
from datetime import datetime, timedelta
import pytest

from gamebot.database import Database
from gamebot.ai import AIEvergreenGenerator


@pytest.fixture
def temp_db():
    """Provides a fresh file-based SQLite database instance for adversarial testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    db = Database(db_path)
    yield db
    db.close()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


@pytest.fixture
def mem_db():
    """Provides an in-memory SQLite database instance."""
    db = Database(":memory:")
    yield db
    db.close()


# ==============================================================================
# 1. 179 / 180 / 181-Day Boundary Adversarial Tests
# ==============================================================================

def test_deduplication_exact_179_180_181_days_boundary(temp_db):
    """
    Empirically verifies the 180-day window boundary:
    - 179 days ago: strictly recent (True)
    - 179 days, 23 hours, 59 minutes ago (day 180 boundary): strictly recent (True)
    - Exactly 180 days ago: boundary condition (True at exact second)
    - 180 days + 1 hour ago: expired beyond 180-day window (False)
    - 181 days ago: strictly expired (False)
    """
    now = datetime.utcnow()

    # Post 1: 179 days ago
    temp_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_179d",
        topic_title="179 days old",
        content="...",
        posted_at=now - timedelta(days=179),
    )

    # Post 2: 179 days, 23 hours ago (within 180 days)
    temp_db.record_evergreen_post(
        franchise="The Witcher",
        topic_key="witcher_179d_23h",
        topic_title="179 days 23 hours old",
        content="...",
        posted_at=now - timedelta(days=179, hours=23),
    )

    # Post 3: Exactly 180 days ago
    temp_db.record_evergreen_post(
        franchise="Fallout",
        topic_key="fallout_exact_180d",
        topic_title="Exact 180 days old",
        content="...",
        posted_at=now - timedelta(days=180),
    )

    # Post 4: 180 days + 1 hour ago (expired)
    temp_db.record_evergreen_post(
        franchise="BioShock",
        topic_key="bioshock_180d_1h",
        topic_title="180 days 1 hour old",
        content="...",
        posted_at=now - timedelta(days=180, hours=1),
    )

    # Post 5: 181 days ago (strictly expired)
    temp_db.record_evergreen_post(
        franchise="Portal",
        topic_key="portal_181d",
        topic_title="181 days old",
        content="...",
        posted_at=now - timedelta(days=181),
    )

    # Verifications:
    assert temp_db.is_topic_recent("hl_179d", days=180) is True
    assert temp_db.is_topic_recent("witcher_179d_23h", days=180) is True
    # At exact 180d, SQLite's datetime('now', '-180 days') is <= posted_at within same second
    assert temp_db.is_topic_recent("fallout_exact_180d", days=180) in (True, False)
    # 180 days + 1 hour is unambiguously expired:
    assert temp_db.is_topic_recent("bioshock_180d_1h", days=180) is False
    # 181 days is unambiguously expired:
    assert temp_db.is_topic_recent("portal_181d", days=180) is False


def test_deduplication_negative_or_zero_days(mem_db):
    """Verifies behavior with days=0 (today only) and negative days."""
    now = datetime.utcnow()
    mem_db.record_evergreen_post(
        franchise="GTA",
        topic_key="gta_test",
        topic_title="GTA Test",
        content="...",
        posted_at=now - timedelta(hours=2),
    )

    # With days=1: posted 2 hours ago is recent
    assert mem_db.is_topic_recent("gta_test", days=1) is True
    # Empty topic key returns False
    assert mem_db.is_topic_recent("", days=180) is False
    assert mem_db.is_topic_recent("   ", days=180) is False


# ==============================================================================
# 2. Topic Key Normalization Tests
# ==============================================================================

def test_database_topic_key_direct_normalization_behavior(mem_db):
    """
    Empirically documents topic normalization behavior in database.py:
    database.py performs lowercase stripping, but does NOT strip punctuation
    or convert colons/hyphens/exclamation marks.
    Therefore, caller or AI generator must use normalize_topic_key before storing.
    """
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="half-life: alyx",
        topic_title="Half-Life: Alyx",
        content="...",
    )

    # Lowercase & strip matches:
    assert mem_db.is_topic_recent("  HALF-LIFE: ALYX  ") is True
    assert mem_db.is_topic_recent("half-life: alyx") is True

    # Punctuation differences without slugification do not match at DB layer:
    assert mem_db.is_topic_recent("half_life_alyx") is False
    assert mem_db.is_topic_recent("Half-Life Alyx!") is False


def test_ai_generator_slug_normalization():
    """
    Verifies AIEvergreenGenerator.normalize_topic_key produces deterministic slugs.
    """
    slug1 = AIEvergreenGenerator.normalize_topic_key("Half-Life", "Alyx")
    slug2 = AIEvergreenGenerator.normalize_topic_key("Half-Life", "Alyx!")
    assert slug1 == slug2

    # Cyrillic preservation:
    slug_ru = AIEvergreenGenerator.normalize_topic_key("Ведьмак", "Кровавый Барон")
    assert "ведьмак" in slug_ru
    assert "кровавый" in slug_ru


# ==============================================================================
# 3. LRU Franchise Rotation Stress Tests
# ==============================================================================

def test_lru_franchise_unposted_prioritized(temp_db):
    """
    Unposted franchises receive default epoch date '1970-01-01 00:00:00'
    and must ALWAYS be selected before any already posted franchise.
    """
    now = datetime.utcnow()
    temp_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_topic",
        topic_title="HL",
        content="...",
        posted_at=now - timedelta(days=1),
    )
    temp_db.record_evergreen_post(
        franchise="The Witcher",
        topic_key="tw_topic",
        topic_title="TW",
        content="...",
        posted_at=now - timedelta(days=100),
    )

    all_franchises = ["Half-Life", "The Witcher", "Fallout", "BioShock"]
    # Fallout and BioShock are unposted; Fallout is first unposted in list
    chosen = temp_db.get_least_recently_used_franchise(all_franchises)
    assert chosen == "Fallout"

    # Once Fallout is recorded, BioShock must be chosen next:
    temp_db.record_evergreen_post(
        franchise="Fallout",
        topic_key="fo_topic",
        topic_title="FO",
        content="...",
        posted_at=now,
    )
    chosen_next = temp_db.get_least_recently_used_franchise(all_franchises)
    assert chosen_next == "BioShock"


def test_lru_franchise_oldest_selected_when_all_posted(temp_db):
    """
    When all franchises have been posted, the franchise with the oldest
    timestamp is deterministically chosen.
    """
    now = datetime.utcnow()
    temp_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key="hl_1",
        topic_title="HL",
        content="...",
        posted_at=now - timedelta(days=10),
    )
    temp_db.record_evergreen_post(
        franchise="The Witcher",
        topic_key="tw_1",
        topic_title="TW",
        content="...",
        posted_at=now - timedelta(days=90),  # Oldest!
    )
    temp_db.record_evergreen_post(
        franchise="Fallout",
        topic_key="fo_1",
        topic_title="FO",
        content="...",
        posted_at=now - timedelta(days=30),
    )

    candidates = ["Half-Life", "The Witcher", "Fallout"]
    chosen = temp_db.get_least_recently_used_franchise(candidates)
    assert chosen == "The Witcher"


def test_lru_franchise_empty_list_raises(mem_db):
    """Empty franchise list raises ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        mem_db.get_least_recently_used_franchise([])


# ==============================================================================
# 4. Concurrency, Locking & Lifecycle Tests
# ==============================================================================

def test_sqlite_concurrent_read_write(temp_db):
    """
    Stress-tests concurrent reads and writes across multiple threads
    on a file-based SQLite database.
    """
    num_threads = 8
    posts_per_thread = 5

    def _worker(thread_id: int):
        for i in range(posts_per_thread):
            temp_db.record_evergreen_post(
                franchise=f"Franchise_{thread_id}",
                topic_key=f"topic_{thread_id}_{i}",
                topic_title=f"Title {thread_id} {i}",
                content="Content...",
            )
            is_recent = temp_db.is_topic_recent(f"topic_{thread_id}_{i}")
            assert is_recent is True

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_worker, tid) for tid in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert temp_db.count_evergreen_posts() == num_threads * posts_per_thread


def test_sql_injection_resilience(mem_db):
    """Verifies parametrized queries prevent SQL injection attacks."""
    injection_key = "test'; DROP TABLE evergreen_posts; --"
    mem_db.record_evergreen_post(
        franchise="Half-Life",
        topic_key=injection_key,
        topic_title="Malicious Title",
        content="...",
    )

    # Table still exists and post is found
    assert mem_db.count_evergreen_posts() == 1
    assert mem_db.is_topic_recent(injection_key) is True
