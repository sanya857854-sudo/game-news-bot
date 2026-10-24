"""
Database Access Layer for Game News Bot.
Manages SQLite database (bot.db) for news deduplication and 180-day (6 months)
evergreen AI content topic deduplication, franchise rotation, and quiet-period tracking.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def _parse_datetime(ts_val: Any) -> Optional[datetime]:
    """Safely parse SQLite timestamp string into naive UTC datetime object."""
    if ts_val is None:
        return None
    if isinstance(ts_val, datetime):
        if ts_val.tzinfo is not None:
            return ts_val.astimezone(timezone.utc).replace(tzinfo=None)
        return ts_val
    if isinstance(ts_val, str):
        ts_str = ts_val.strip()
        if not ts_str:
            return None
        # Try fromisoformat first (handles ISO 8601 strings with offsets like +03:00, Z)
        try:
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            pass
        # Try common SQLite / ISO formats
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ):
            try:
                clean_str = ts_str.split("+")[0].rstrip("Z")
                return datetime.strptime(clean_str, fmt)
            except ValueError:
                continue
    return None


class Database:
    """
    SQLite Data Access Layer for Game News Bot.
    Enforces 180-day deduplication for evergreen topics, LRU franchise rotation,
    and news publication tracking.
    """

    def __init__(self, db_path: str = "bot.db") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:" and not os.path.isabs(self.db_path):
            root_db = Path(__file__).resolve().parent.parent / self.db_path
            if root_db.exists() or not Path(self.db_path).exists():
                self.db_path = str(root_db)
        self._memory_conn: Optional[sqlite3.Connection] = None
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """
        Returns a SQLite connection.
        Maintains a single persistent connection for in-memory databases (:memory:),
        or opens a connection for file-based paths.
        """
        if self.db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn

        # Ensure parent directory exists for file databases
        db_file = Path(self.db_path)
        if db_file.parent and not db_file.parent.exists():
            db_file.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Initializes tables and indexes for news and evergreen posts."""
        conn = self.get_connection()
        try:
            with conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS posted_news (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        news_id TEXT UNIQUE NOT NULL,
                        source TEXT NOT NULL,
                        title TEXT NOT NULL,
                        url TEXT NOT NULL,
                        posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS evergreen_posts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        franchise TEXT COLLATE NOCASE NOT NULL,
                        topic_key TEXT COLLATE NOCASE NOT NULL,
                        topic_title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        facts_count INTEGER DEFAULT 3,
                        question TEXT NOT NULL,
                        hashtags TEXT NOT NULL,
                        channel_message_id INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE INDEX IF NOT EXISTS idx_evergreen_franchise_posted 
                    ON evergreen_posts(franchise COLLATE NOCASE, posted_at);

                    CREATE INDEX IF NOT EXISTS idx_evergreen_topic_key_posted 
                    ON evergreen_posts(topic_key COLLATE NOCASE, posted_at);

                    CREATE INDEX IF NOT EXISTS idx_evergreen_posted_at 
                    ON evergreen_posts(posted_at);

                    CREATE INDEX IF NOT EXISTS idx_posted_news_posted_at 
                    ON posted_news(posted_at);

                    CREATE INDEX IF NOT EXISTS idx_posted_news_news_id 
                    ON posted_news(news_id);
                """)
            logger.debug("Database initialized successfully at %s", self.db_path)
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def close(self) -> None:
        """Closes memory connection if open."""
        if self._memory_conn is not None:
            try:
                self._memory_conn.close()
            except Exception:
                pass
            self._memory_conn = None

    def is_topic_recent(self, topic_key: str, days: int = 180) -> bool:
        """
        Check if a topic_key was posted within the given window (default: 180 days / 6 months).
        Returns True if recently posted, False otherwise.
        """
        clean_key = str(topic_key).strip().lower()
        if not clean_key:
            return False

        query = """
            SELECT 1 FROM evergreen_posts
            WHERE topic_key = ? COLLATE NOCASE
              AND datetime(posted_at) >= datetime('now', '-' || ? || ' days')
            LIMIT 1;
        """
        conn = self.get_connection()
        try:
            cur = conn.execute(query, (clean_key, int(days)))
            return cur.fetchone() is not None
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def record_evergreen_post(
        self,
        franchise: str,
        topic_key: str,
        topic_title: str,
        content: str,
        facts_count: int = 3,
        question: str = "",
        hashtags: str = "#история_игр #факты #игры",
        channel_message_id: Optional[int] = None,
        posted_at: Optional[Union[datetime, str]] = None,
    ) -> int:
        """
        Inserts a newly published evergreen post into evergreen_posts.
        Allows specifying custom posted_at datetime for testing / backdating.
        Returns the new row ID.
        """
        clean_franchise = str(franchise).strip()
        clean_key = str(topic_key).strip().lower()
        clean_title = str(topic_title).strip()
        clean_content = str(content).strip()
        clean_question = str(question).strip()
        clean_hashtags = str(hashtags).strip()

        ts_formatted: str
        if posted_at is not None:
            if isinstance(posted_at, datetime):
                if posted_at.tzinfo is not None:
                    posted_at = posted_at.astimezone(timezone.utc).replace(tzinfo=None)
                ts_formatted = posted_at.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(posted_at, str):
                ts_formatted = posted_at
            else:
                ts_formatted = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_formatted = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        conn = self.get_connection()
        try:
            with conn:
                query = """
                    INSERT INTO evergreen_posts (
                        franchise, topic_key, topic_title, content, facts_count,
                        question, hashtags, channel_message_id, posted_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """
                cur = conn.execute(
                    query,
                    (
                        clean_franchise,
                        clean_key,
                        clean_title,
                        clean_content,
                        int(facts_count),
                        clean_question,
                        clean_hashtags,
                        channel_message_id,
                        ts_formatted,
                        ts_formatted,
                    ),
                )
                row_id = cur.lastrowid or 0
                logger.info(
                    "Recorded evergreen post #%d for franchise '%s' (topic: %s)",
                    row_id,
                    clean_franchise,
                    clean_title,
                )
                return row_id
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def get_recent_topics_for_franchise(self, franchise: str, days: int = 180) -> List[str]:
        """
        Returns list of topic titles published for the franchise within the given days.
        Used to inject negative constraints into AI prompts.
        """
        clean_franchise = str(franchise).strip().lower()
        query = """
            SELECT topic_title FROM evergreen_posts
            WHERE franchise = ? COLLATE NOCASE
              AND datetime(posted_at) >= datetime('now', '-' || ? || ' days')
            ORDER BY datetime(posted_at) DESC;
        """
        conn = self.get_connection()
        try:
            cur = conn.execute(query, (clean_franchise, int(days)))
            return [str(row["topic_title"]) for row in cur.fetchall()]
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def get_least_recently_used_franchise(self, all_franchises: List[str]) -> str:
        """
        Selects the franchise from all_franchises that was either never posted,
        or was posted longest ago.
        Raises ValueError if all_franchises is empty.
        """
        if not all_franchises:
            raise ValueError("all_franchises list cannot be empty")

        query = """
            SELECT franchise, MAX(datetime(posted_at)) as last_posted
            FROM evergreen_posts
            GROUP BY franchise COLLATE NOCASE;
        """
        conn = self.get_connection()
        try:
            cur = conn.execute(query)
            # Store lowercase map for resilient matching
            history: Dict[str, str] = {}
            for row in cur.fetchall():
                f_name = str(row["franchise"]).strip().lower()
                history[f_name] = str(row["last_posted"])

            # Sort franchises: unposted get default oldest date '1970-01-01 00:00:00'
            def _sort_key(franchise_name: str) -> str:
                return history.get(franchise_name.strip().lower(), "1970-01-01 00:00:00")

            chosen = min(all_franchises, key=_sort_key)
            return chosen
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def get_last_news_post_time(self) -> Optional[datetime]:
        """Get timestamp of the most recently posted news item."""
        query = "SELECT MAX(datetime(posted_at)) as last_ts FROM posted_news;"
        conn = self.get_connection()
        try:
            cur = conn.execute(query)
            row = cur.fetchone()
            if row and row["last_ts"]:
                return _parse_datetime(row["last_ts"])
            return None
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def get_last_evergreen_post_time(self) -> Optional[datetime]:
        """Get timestamp of the most recently posted evergreen item."""
        query = "SELECT MAX(datetime(posted_at)) as last_ts FROM evergreen_posts;"
        conn = self.get_connection()
        try:
            cur = conn.execute(query)
            row = cur.fetchone()
            if row and row["last_ts"]:
                return _parse_datetime(row["last_ts"])
            return None
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def record_news_post(
        self,
        news_id: str,
        source: str,
        title: str,
        url: str,
        posted_at: Optional[Union[datetime, str]] = None,
    ) -> int:
        """Records a posted news/deal item to prevent duplicate publishing."""
        clean_id = str(news_id).strip()
        clean_source = str(source).strip()
        clean_title = str(title).strip()
        clean_url = str(url).strip()

        ts_formatted: str
        if posted_at is not None:
            if isinstance(posted_at, datetime):
                if posted_at.tzinfo is not None:
                    posted_at = posted_at.astimezone(timezone.utc).replace(tzinfo=None)
                ts_formatted = posted_at.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(posted_at, str):
                ts_formatted = posted_at
            else:
                ts_formatted = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_formatted = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        conn = self.get_connection()
        try:
            with conn:
                query = """
                    INSERT OR IGNORE INTO posted_news (news_id, source, title, url, posted_at)
                    VALUES (?, ?, ?, ?, ?);
                """
                cur = conn.execute(query, (clean_id, clean_source, clean_title, clean_url, ts_formatted))
                return cur.lastrowid or 0
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def is_news_posted(self, news_id: str) -> bool:
        """Checks if a news item was already posted."""
        clean_id = str(news_id).strip()
        query = "SELECT 1 FROM posted_news WHERE news_id = ? LIMIT 1;"
        conn = self.get_connection()
        try:
            cur = conn.execute(query, (clean_id,))
            return cur.fetchone() is not None
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def count_evergreen_posts(self) -> int:
        """Returns total number of evergreen posts."""
        conn = self.get_connection()
        try:
            cur = conn.execute("SELECT COUNT(*) as cnt FROM evergreen_posts;")
            row = cur.fetchone()
            return int(row["cnt"]) if row else 0
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def count_news_posts(self) -> int:
        """Returns total number of posted news items."""
        conn = self.get_connection()
        try:
            cur = conn.execute("SELECT COUNT(*) as cnt FROM posted_news;")
            row = cur.fetchone()
            return int(row["cnt"]) if row else 0
        finally:
            if self.db_path != ":memory:":
                conn.close()
