"""
Scheduler & Quiet-Period Engine for Game News Bot.
Monitors gaming news quiet periods (>6h without posted news during active daytime 10:00-22:00)
and triggers AI evergreen content with a minimum 24-hour cooldown between posts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from .ai import AIEvergreenGenerator, EvergreenPostData, ICONIC_FRANCHISES
from .curated_lore import get_all_curated_franchises, get_any_unexcluded_curated_post
from .database import Database

logger = logging.getLogger(__name__)


class QuietPeriodScheduler:
    """
    Evaluates news gaps and evergreen cooldown to determine if an AI evergreen
    post should be generated and published.
    """

    def __init__(
        self,
        db: Database,
        quiet_period_hours: float = 6.0,
        min_evergreen_cooldown_hours: float = 24.0,
        active_hours_range: Tuple[int, int] = (10, 22),
    ) -> None:
        self.db = db
        self.quiet_period_hours = float(quiet_period_hours)
        self.min_evergreen_cooldown_hours = float(min_evergreen_cooldown_hours)
        self.active_hours_range = active_hours_range

    def should_trigger_evergreen(self, current_time: Optional[datetime] = None) -> bool:
        """
        Evaluates whether all quiet-period conditions are met:
        1. Inside active daytime hours (default: 10:00 - 22:00).
        2. At least min_evergreen_cooldown_hours (default: 24h) since last evergreen post.
        3. At least quiet_period_hours (default: 6h) since last news post (or no news posted yet).
        """
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is not None:
            now = now.astimezone(timezone.utc).replace(tzinfo=None)

        # 1. Check daytime window
        start_hour, end_hour = self.active_hours_range
        if not (start_hour <= now.hour <= end_hour):
            logger.debug(
                "Outside active posting hours (%d:00 - %d:00), current hour is %d",
                start_hour,
                end_hour,
                now.hour,
            )
            return False

        # 2. Check cooldown since last evergreen post
        last_evergreen = self.db.get_last_evergreen_post_time()
        if last_evergreen is not None:
            if last_evergreen.tzinfo is not None:
                last_evergreen = last_evergreen.astimezone(timezone.utc).replace(tzinfo=None)
            elapsed_evergreen = (now - last_evergreen).total_seconds() / 3600.0
            if elapsed_evergreen < self.min_evergreen_cooldown_hours:
                logger.debug(
                    "Evergreen cooldown active: %.1f hours elapsed, required %.1f hours",
                    elapsed_evergreen,
                    self.min_evergreen_cooldown_hours,
                )
                return False

        # 3. Check quiet period since last news post
        last_news = self.db.get_last_news_post_time()
        if last_news is not None:
            if last_news.tzinfo is not None:
                last_news = last_news.astimezone(timezone.utc).replace(tzinfo=None)
            elapsed_news = (now - last_news).total_seconds() / 3600.0
            if elapsed_news < self.quiet_period_hours:
                logger.debug(
                    "News channel is active (last news posted %.1f hours ago, quiet threshold is %.1f hours)",
                    elapsed_news,
                    self.quiet_period_hours,
                )
                return False

        return True

    def get_status(self, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """Returns diagnostic evaluation of quiet period status."""
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is not None:
            now = now.astimezone(timezone.utc).replace(tzinfo=None)

        start_hour, end_hour = self.active_hours_range
        in_active_hours = start_hour <= now.hour <= end_hour

        last_news = self.db.get_last_news_post_time()
        hours_since_news: Optional[float] = None
        if last_news is not None:
            if last_news.tzinfo is not None:
                last_news = last_news.astimezone(timezone.utc).replace(tzinfo=None)
            hours_since_news = round((now - last_news).total_seconds() / 3600.0, 2)

        last_evergreen = self.db.get_last_evergreen_post_time()
        hours_since_evergreen: Optional[float] = None
        if last_evergreen is not None:
            if last_evergreen.tzinfo is not None:
                last_evergreen = last_evergreen.astimezone(timezone.utc).replace(tzinfo=None)
            hours_since_evergreen = round((now - last_evergreen).total_seconds() / 3600.0, 2)

        should_trigger = False
        reason = ""

        if not in_active_hours:
            reason = f"Outside active hours ({now.hour:02d}:00 not in {start_hour:02d}:00-{end_hour:02d}:00)"
        elif hours_since_evergreen is not None and hours_since_evergreen < self.min_evergreen_cooldown_hours:
            reason = f"Evergreen cooldown active ({hours_since_evergreen:.1f}h < {self.min_evergreen_cooldown_hours}h)"
        elif hours_since_news is not None and hours_since_news < self.quiet_period_hours:
            reason = f"News is active ({hours_since_news:.1f}h < {self.quiet_period_hours}h quiet threshold)"
        else:
            should_trigger = True
            reason = "Quiet period condition satisfied"

        return {
            "current_hour": now.hour,
            "in_active_hours": in_active_hours,
            "last_news_time": last_news.isoformat() if last_news else None,
            "hours_since_last_news": hours_since_news,
            "last_evergreen_time": last_evergreen.isoformat() if last_evergreen else None,
            "hours_since_last_evergreen": hours_since_evergreen,
            "quiet_period_hours": self.quiet_period_hours,
            "min_evergreen_cooldown_hours": self.min_evergreen_cooldown_hours,
            "should_trigger": should_trigger,
            "reason": reason,
        }

    async def trigger_evergreen_cycle(
        self,
        generator: AIEvergreenGenerator,
        publisher: Optional[Any] = None,
        all_franchises: Optional[List[str]] = None,
        current_time: Optional[datetime] = None,
    ) -> Optional[int]:
        """
        Executes full autonomous cycle if conditions are satisfied:
        1. Evaluates quiet-period trigger.
        2. Selects least recently used franchise (offline mode uses curated franchises).
        3. Fetches topics from the last 180 days for negative prompt constraint.
        4. Generates structured post.
        5. Enforces is_topic_recent() deduplication check post-generation.
        6. Posts to Telegram channel (if publisher provided).
        7. Records post into database only if delivery succeeds or publisher is None.
        Returns the inserted database row ID, or None if skipped/failed.
        """
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is not None:
            now = now.astimezone(timezone.utc).replace(tzinfo=None)

        if not self.should_trigger_evergreen(now):
            logger.info("Evergreen trigger skipped: quiet period conditions not met.")
            return None

        if all_franchises:
            franchises = all_franchises
        elif not generator.api_key:
            franchises = get_all_curated_franchises()
        else:
            franchises = ICONIC_FRANCHISES

        chosen_franchise = self.db.get_least_recently_used_franchise(franchises)
        recent_topics = self.db.get_recent_topics_for_franchise(chosen_franchise, days=180)

        logger.info(
            "Generating evergreen post for franchise '%s' (excluding %d recent topics)",
            chosen_franchise,
            len(recent_topics),
        )
        post_data = await generator.generate_post(chosen_franchise, excluded_topics=recent_topics)

        # Finding 5: Enforce is_topic_recent() guard post-generation
        if self.db.is_topic_recent(post_data.topic_key, days=180):
            logger.warning(
                "Generated topic '%s' (%s) was already posted within 180 days! Falling back to unexcluded curated content.",
                post_data.topic_title,
                post_data.topic_key,
            )
            post_data = generator._curated_fallback(chosen_franchise, excluded_topics=recent_topics)
            if self.db.is_topic_recent(post_data.topic_key, days=180):
                entry = get_any_unexcluded_curated_post(excluded_topics=recent_topics)
                post_data = EvergreenPostData(
                    franchise=entry.franchise,
                    topic_key=entry.topic_key,
                    topic_title=entry.topic_title,
                    content=entry.format_post(),
                    facts_count=len(entry.facts),
                    question=entry.question,
                    hashtags=entry.hashtags,
                )

        # Publish to Telegram channel if publisher is provided
        msg_id: Optional[int] = None
        if publisher is not None:
            if hasattr(publisher, "send_evergreen_to_channel"):
                try:
                    msg_id = await publisher.send_evergreen_to_channel(post_data.content)
                except Exception as e:
                    logger.error("Failed to publish evergreen post to channel: %s", e)
                    msg_id = None
            if msg_id is None:
                logger.warning(
                    "Skipping DB record: evergreen post delivery failed (msg_id is None). Cooldown not burned."
                )
                return None

        # Record into database
        row_id = self.db.record_evergreen_post(
            franchise=post_data.franchise,
            topic_key=post_data.topic_key,
            topic_title=post_data.topic_title,
            content=post_data.content,
            facts_count=post_data.facts_count,
            question=post_data.question,
            hashtags=post_data.hashtags,
            channel_message_id=msg_id,
            posted_at=now,
        )
        logger.info(
            "Evergreen cycle complete: recorded post #%d for '%s' (telegram msg_id=%s)",
            row_id,
            post_data.franchise,
            msg_id,
        )
        return row_id
