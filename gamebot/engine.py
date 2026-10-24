"""Главный цикл: раз в N минут выбирает лучшую свежую новость и публикует её."""
from __future__ import annotations

import logging
import random
import re
import threading
import time
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

from . import deals, formatting, giveaways, growth, hotness, publisher, rss, steam
from .config import Config
from .models import Candidate
from .storage import Storage
from .telegram import Telegram, TelegramError

log = logging.getLogger(__name__)

MAX_BUILD_ATTEMPTS = 15      # сколько кандидатов пробуем за один заход
SAME_KIND_PENALTY = 1.5      # штраф за повтор одного типа постов подряд

# Слова, которые не определяют тему новости — их не учитываем при поиске повторов.
_TOPIC_STOPWORDS = {
    "для", "что", "как", "это", "все", "уже", "или", "при", "над", "под", "без",
    "после", "будет", "будут", "могут", "может", "своих", "своей", "свою", "его",
    "новый", "новая", "новое", "новые", "игра", "игры", "игру", "игре", "игрой",
    "steam", "the", "and", "for", "with", "new", "game", "games", "dlc", "ps5",
}
_TOPIC_RE = re.compile(r"[a-zа-яё0-9]{3,}")


class Bot:
    def __init__(self, cfg: Config, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.storage = Storage(cfg.db_path)
        self.tg = Telegram(cfg.bot_token, cfg.channel_id, cfg.api_base)
        self.tz = ZoneInfo(cfg.timezone)
        self._stop = threading.Event()
        self._retry_at = 0.0
        self._invite_url_val: Optional[str] = None

    # ---------------------------------------------------------------- ссылка «Подписаться»
    def _invite_url(self) -> Optional[str]:
        """Ссылка-приглашение создаётся один раз; при сбое попробуем при следующем посте."""
        if self._invite_url_val is not None:
            return self._invite_url_val
        self._invite_url_val = growth.ensure_invite_link(self.cfg, self.tg, self.storage)
        return self._invite_url_val

    # ---------------------------------------------------------------- сбор и выбор
    def collect(self) -> List[Candidate]:
        found: List[Candidate] = []
        try:
            found += giveaways.list_candidates(self.cfg, self.storage)
        except Exception as exc:
            log.warning("Раздачи сейчас недоступны: %s", exc)
        try:
            found += deals.list_candidates(self.cfg, self.storage)
        except Exception as exc:
            log.warning("Скидки CheapShark сейчас недоступны: %s", exc)
        if self.cfg.enable_steam:
            try:
                found += steam.list_candidates(self.cfg, self.storage)
            except Exception as exc:
                log.warning("Steam сейчас недоступен: %s", exc)
        if self.cfg.enable_rss:
            try:
                found += rss.list_candidates(self.cfg, self.storage)
            except Exception as exc:
                log.warning("RSS-ленты не обработались: %s", exc)
        return found

    def rank(self, candidates: List[Candidate]) -> List[Candidate]:
        """Лучшие — вверху. Чтобы канал не был однообразным, повтор типа поста подряд штрафуется."""
        recent = self.storage.recent_kinds(3)

        def adjusted(c: Candidate) -> float:
            return c.score - SAME_KIND_PENALTY * recent.count(c.kind) + random.random() * 0.3

        return sorted(candidates, key=adjusted, reverse=True)

    # ---------------------------------------------------------------- один пост
    def _mark(self, c: Candidate, status: str) -> None:
        if not self.dry_run:
            self.storage.mark(c.key, c.kind, c.title, status)

    def post_one(self, candidates: Optional[List[Candidate]] = None) -> bool:
        """Находит и публикует один пост. True — получилось."""
        if candidates is None:
            candidates = self.rank(self.collect())
        if not candidates:
            log.info("Свежих материалов пока нет")
            return False

        recent_news = self.storage.recent_titles(time.time() - 3 * 86400, kind="news")
        recent_all = self.storage.recent_titles(time.time() - 60 * 86400)
        blacklist_file = self.cfg.db_path.parent / "blacklist.txt"
        blacklist = []
        if blacklist_file.exists():
            blacklist = [line.strip().lower() for line in blacklist_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]

        attempts = 0
        network_failures = 0
        for cand in candidates:
            if attempts >= MAX_BUILD_ATTEMPTS or network_failures >= 3:
                break

            title_low = cand.title.lower()
            if blacklist and any(b in title_low for b in blacklist):
                log.info("Материал в черном списке, пропускаю: %s", cand.title)
                self._mark(cand, "skipped")
                continue

            if cand.kind == "news" and formatting.is_similar_to_any(cand.title, recent_news):
                log.info("Похожая новость уже была, пропускаю: %s", cand.title)
                self._mark(cand, "skipped")
                continue

            if cand.kind in ("release", "discount", "free", "upcoming") and formatting.is_similar_to_any(cand.title, recent_all, threshold=0.7):
                log.info("Игра уже публиковалась в канале за последние 60 дней, пропускаю: %s", cand.title)
                self._mark(cand, "skipped")
                continue

            attempts += 1
            try:
                post = cand.build()
            except requests.RequestException as exc:
                network_failures += 1          # временная проблема сети — не наказываем материал
                log.warning("Не удалось собрать «%s» (сеть): %s", cand.title, exc)
                continue
            except Exception as exc:
                log.warning("Не удалось собрать «%s»: %s", cand.title, exc)
                if not self.dry_run:
                    self.storage.bump_fail(cand.key, cand.kind, cand.title)
                continue

            if post is None:
                self._mark(cand, "skipped")
                continue

            if self.dry_run:
                self._print_dry_run(cand, post)
                return True

            try:
                publisher.publish(self.cfg, self.tg, post, self._invite_url())
            except TelegramError as exc:
                if exc.fatal:
                    log.error(
                        "Telegram отказал: %s. Проверьте токен, ID канала и что бот добавлен в канал "
                        "администратором с правом публикации.", exc,
                    )
                else:
                    log.warning("Публикация «%s» не удалась: %s", cand.title, exc)
                    self.storage.bump_fail(cand.key, cand.kind, cand.title)
                return False
            except Exception:
                log.exception("Публикация «%s» не удалась", cand.title)
                self.storage.bump_fail(cand.key, cand.kind, cand.title)
                return False

            self.storage.mark(cand.key, cand.kind, cand.title, "posted")
            log.info("✔ Пост опубликован [%s]: %s", cand.kind, cand.title)
            return True

        log.info("Подходящего материала в этот раз не нашлось")
        return False

    @staticmethod
    def _print_dry_run(cand: Candidate, post) -> None:
        print("\n" + "=" * 60)
        print(f"ТЕСТОВЫЙ РЕЖИМ (в канал ничего не отправляется)\nТип: {cand.kind} | Ключ: {cand.key}")
        print("-" * 60)
        print(post.text.replace("§§TRAILER§§", "[здесь будет ссылка на трейлер, если найдётся]"))
        print("-" * 60)
        print("Картинки:", post.photos or "нет")
        print("Трейлер (mp4):", post.video_url or "нет")
        print("Трейлер (HLS, нужен ffmpeg):", post.hls_url or "нет")
        print("Ссылка на трейлер:", post.trailer_url or "нет")
        print("Инлайн-кнопка:", f"[{post.button_text}] ➔ {post.button_url}" if post.button_text else "нет")
        print("Интерактивный опрос:", f"«{post.poll_question}» -> {post.poll_options}" if post.poll_question else "нет")
        print("=" * 60 + "\n")

    # ---------------------------------------------------------------- расписание
    def in_quiet_hours(self) -> bool:
        window = self.cfg.quiet_hours
        if not window:
            return False
        start, end = window
        hour = datetime.now(self.tz).hour
        return (start <= hour < end) if start < end else (hour >= start or hour < end)

    def daily_cap_reached(self) -> bool:
        midnight = datetime.now(self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.storage.posts_since(midnight.timestamp()) >= self.cfg.max_posts_per_day

    def seconds_until_next_post(self) -> float:
        last = self.storage.last_post_time()
        target = (last + self.cfg.post_interval_minutes * 60) if last else 0
        return max(0.0, max(target, self._retry_at) - time.time())

    # ---------------------------------------------------------------- горячие новости
    def hot_gap_ok(self) -> bool:
        """Не публикуем важное чаще, чем раз в HOT_MIN_GAP_MINUTES."""
        last = self.storage.last_post_time()
        return last is None or (time.time() - last) >= self.cfg.hot_min_gap_minutes * 60

    @staticmethod
    def _topic_words(title: str) -> set:
        return {w for w in _TOPIC_RE.findall((title or "").lower()) if w not in _TOPIC_STOPWORDS}

    def topic_already_covered(self, title: str, hours: int = 6) -> bool:
        """Не публикуем вторую новость про то же самое в течение нескольких часов."""
        words = self._topic_words(title)
        if len(words) < 2:
            return False
        recent = self.storage.recent_titles(time.time() - hours * 3600, kind="news")
        return any(len(words & self._topic_words(other)) >= 2 for other in recent)

    def post_hot(self, candidates: List[Candidate]) -> bool:
        """Публикует самый интересный материал, если он достаточно «горячий»."""
        hot: List[Candidate] = []
        for cand in candidates:
            if not hotness.is_hot(cand, self.cfg.hot_min_score):
                continue
            if cand.kind == "news" and self.topic_already_covered(cand.title):
                log.info("Тема уже была за последние часы — пропускаю: %s", cand.title)
                continue
            hot.append(cand)
        if not hot:
            return False
        hot.sort(key=hotness.score, reverse=True)
        best = hot[0]
        log.info(
            "Горячая тема: %s (%.1f; %s)",
            best.title, hotness.score(best), hotness.describe(best),
        )
        return self.post_one(hot)

    # ---------------------------------------------------------------- запуск
    def request_stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        cfg = self.cfg
        log.info(
            "Бот запущен. Плановый пост раз в %d мин., канал %s, часовой пояс %s",
            cfg.post_interval_minutes, cfg.channel_id, cfg.timezone,
        )
        if cfg.hot_news:
            log.info(
                "Режим горячих новостей: проверка каждые %d мин., пауза не меньше %d мин., порог интересности %.1f",
                max(1, cfg.poll_seconds // 60), cfg.hot_min_gap_minutes, cfg.hot_min_score,
            )
        self._startup_check()
        if self.cfg.track_joins:
            growth.JoinsTracker(self.cfg, self.tg, self.storage, self._stop).start()
        while not self._stop.is_set():
            try:
                if self.in_quiet_hours():
                    self._stop.wait(300)
                    continue
                if self.daily_cap_reached():
                    log.info("Дневной лимит постов (%d) достигнут — жду до завтра", cfg.max_posts_per_day)
                    self._stop.wait(1800)
                    continue

                due = self.seconds_until_next_post() <= 0
                candidates: List[Candidate] = []
                if due or cfg.hot_news:
                    candidates = self.rank(self.collect())

                posted = False
                if cfg.hot_news and self.hot_gap_ok():
                    posted = self.post_hot(candidates)     # важное — вне расписания
                if not posted and due:
                    posted = self.post_one(candidates)     # обычный плановый пост

                if not posted and due:
                    self._retry_at = time.time() + cfg.retry_minutes * 60
                    log.info("Следующая попытка через %d мин.", cfg.retry_minutes)

                wait = self.seconds_until_next_post()
                if cfg.hot_news:
                    pause = cfg.poll_seconds if wait <= 0 else min(cfg.poll_seconds, wait)
                else:
                    pause = 60 if wait <= 0 else min(60, wait)
                self._stop.wait(max(20, pause))
            except Exception:
                log.exception("Неожиданная ошибка в главном цикле — продолжаю работу")
                self._retry_at = time.time() + cfg.retry_minutes * 60
                self._stop.wait(30)
        log.info("Бот остановлен")

    def _startup_check(self) -> None:
        """Сообщает о проблемах сразу при запуске, а не молча."""
        while not self._stop.is_set():
            try:
                me = self.tg.get_me()
                log.info("Telegram: бот @%s подключён", me.get("username"))
                return
            except TelegramError as exc:
                if exc.code == 401:
                    raise SystemExit("Telegram не принял токен (TELEGRAM_BOT_TOKEN). Проверьте его в файле .env.")
                log.warning("Не удалось связаться с Telegram: %s. Повторю через минуту.", exc)
                self._stop.wait(60)
