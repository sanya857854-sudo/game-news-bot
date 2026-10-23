"""Память бота (SQLite): что уже опубликовано, чтобы не повторяться."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

MAX_FAILS = 3  # после стольких неудач подряд материал пропускается навсегда


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                key        TEXT PRIMARY KEY,
                kind       TEXT NOT NULL,
                title      TEXT,
                status     TEXT NOT NULL,          -- posted / skipped / failing
                fails      INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_items_status_time ON items(status, created_at)")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS joins (
                user_id   INTEGER PRIMARY KEY,
                username  TEXT,
                link_name TEXT,                     -- по какой ссылке человек пришёл
                joined_at INTEGER NOT NULL,
                active    INTEGER NOT NULL DEFAULT 1,
                left_at   INTEGER
            )
            """
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_joins_link ON joins(link_name)")
        self._db.commit()

    # ---------- проверки ----------
    def is_known(self, key: str) -> bool:
        """True, если материал уже опубликован или окончательно пропущен."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM items WHERE key = ? AND status IN ('posted', 'skipped')", (key,)
            ).fetchone()
        return row is not None

    def posted_with_prefix_since(self, prefix: str, since: float) -> bool:
        """Был ли уже пост с ключом, начинающимся на prefix, после момента since."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM items WHERE status = 'posted' AND key LIKE ? AND created_at >= ? LIMIT 1",
                (prefix + "%", int(since)),
            ).fetchone()
        return row is not None

    # ---------- записи ----------
    def mark(self, key: str, kind: str, title: str, status: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO items(key, kind, title, status, fails, created_at) VALUES (?, ?, ?, ?, 0, ?)",
                (key, kind, title, status, int(time.time())),
            )
            self._db.commit()

    def bump_fail(self, key: str, kind: str, title: str) -> int:
        """Запоминает неудачу. После MAX_FAILS неудач материал помечается как пропущенный."""
        with self._lock:
            row = self._db.execute("SELECT fails FROM items WHERE key = ?", (key,)).fetchone()
            fails = (row[0] if row else 0) + 1
            status = "skipped" if fails >= MAX_FAILS else "failing"
            self._db.execute(
                "INSERT OR REPLACE INTO items(key, kind, title, status, fails, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (key, kind, title, status, fails, int(time.time())),
            )
            self._db.commit()
        return fails

    # ---------- статистика ----------
    def last_post_time(self) -> Optional[int]:
        with self._lock:
            row = self._db.execute("SELECT MAX(created_at) FROM items WHERE status = 'posted'").fetchone()
        return row[0] if row and row[0] else None

    def posts_since(self, timestamp: float) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM items WHERE status = 'posted' AND created_at >= ?", (int(timestamp),)
            ).fetchone()
        return row[0] if row else 0

    def recent_kinds(self, limit: int) -> List[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT kind FROM items WHERE status = 'posted' ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r[0] for r in rows]

    def recent_titles(self, since: float, kind: Optional[str] = None) -> List[str]:
        query = "SELECT title FROM items WHERE status = 'posted' AND created_at >= ?"
        args: list = [int(since)]
        if kind:
            query += " AND kind = ?"
            args.append(kind)
        with self._lock:
            rows = self._db.execute(query, args).fetchall()
        return [r[0] for r in rows if r[0]]

    def total_posted(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM items WHERE status = 'posted'").fetchone()
        return row[0] if row else 0

    # ---------- служебные пары «ключ → значение» ----------
    def meta_get(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
            self._db.commit()

    def meta_items(self, prefix: str) -> List[Tuple[str, str]]:
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM meta WHERE key LIKE ?", (prefix + "%",)).fetchall()
        return [(r[0], r[1]) for r in rows]

    # ---------- подписчики ----------
    def record_join(self, user_id: Optional[int], username: Optional[str], link_name: Optional[str]) -> bool:
        """Запоминает нового подписчика. False, если он у нас уже записан."""
        if user_id is None:
            return False
        with self._lock:
            row = self._db.execute("SELECT active FROM joins WHERE user_id = ?", (user_id,)).fetchone()
            if row and row[0]:
                return False
            self._db.execute(
                "INSERT OR REPLACE INTO joins(user_id, username, link_name, joined_at, active, left_at) "
                "VALUES (?, ?, ?, ?, 1, NULL)",
                (user_id, username, link_name, int(time.time())),
            )
            self._db.commit()
        return True

    def record_leave(self, user_id: Optional[int]) -> None:
        if user_id is None:
            return
        with self._lock:
            self._db.execute(
                "UPDATE joins SET active = 0, left_at = ? WHERE user_id = ?", (int(time.time()), user_id)
            )
            self._db.commit()

    def join_stats(self) -> List[Tuple[str, int, int]]:
        """По каждой ссылке: (источник, пришло всего, осталось в канале сейчас)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT COALESCE(link_name, 'неизвестно'), COUNT(*), COALESCE(SUM(active), 0) "
                "FROM joins GROUP BY 1 ORDER BY COUNT(*) DESC"
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def joins_total(self) -> Tuple[int, int]:
        """(пришло всего, из них в канале сейчас)."""
        with self._lock:
            row = self._db.execute("SELECT COUNT(*), COALESCE(SUM(active), 0) FROM joins").fetchone()
        return (row[0] or 0, row[1] or 0)

    def joins_since(self, timestamp: float) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM joins WHERE joined_at >= ?", (int(timestamp),)).fetchone()
        return row[0] or 0
