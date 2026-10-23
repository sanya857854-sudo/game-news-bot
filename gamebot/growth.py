"""Рост канала: пригласительная ссылка, кнопка «Подписаться» под постами и подсчёт, откуда пришли люди.

Важно понимать: Telegram не разрешает боту самому добавлять подписчиков в канал —
такого метода в Bot API нет. Здесь собрано то, что действительно помогает и не нарушает правила:
кнопка-ссылка в постах и статистика по разным пригласительным ссылкам.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

from .config import Config
from .storage import Storage
from .telegram import Telegram, TelegramError, url_button_markup

log = logging.getLogger(__name__)

POST_LINK = "post"                      # имя ссылки, которую показываем кнопкой под постами
MEMBER_STATUSES = ("member", "administrator", "creator")
OUT_STATUSES = ("left", "kicked")


# ---------------------------------------------------------------- ссылка и кнопка
def ensure_invite_link(cfg: Config, tg: Telegram, storage: Storage, name: str = POST_LINK) -> Optional[str]:
    """Возвращает пригласительную ссылку с этим именем. Создаёт один раз и запоминает."""
    if not cfg.subscribe_button:
        return None
    key = f"invite_link:{name}"
    cached = storage.meta_get(key)
    if cached:
        return cached
    try:
        link = tg.create_chat_invite_link(name)
    except TelegramError as exc:
        log.warning("Не удалось создать пригласительную ссылку (%s). Посты выйдут без кнопки «Подписаться».", exc)
        return None
    url = (link or {}).get("invite_link")
    if not url:
        log.warning("Telegram вернул ссылку без адреса — кнопка «Подписаться» не будет добавлена")
        return None
    storage.meta_set(key, url)
    log.info("Пригласительная ссылка «%s»: %s", name, url)
    return url


def post_markup(cfg: Config, url: Optional[str]) -> Optional[str]:
    """Клавиатура с кнопкой «Подписаться» для поста (или None, если ссылки нет)."""
    if not (cfg.subscribe_button and url):
        return None
    return url_button_markup(cfg.subscribe_button_text or "📣 Подписаться", url)


# ---------------------------------------------------------------- подсчёт подписчиков
class JoinsTracker(threading.Thread):
    """Слушает изменения участников канала и записывает, сколько людей пришло по каждой ссылке.

    Работает через getUpdates и «долгое ожидание»: почти не тратит ресурсы, но сразу
    замечает нового подписчика. Бот должен быть администратором канала.
    """

    def __init__(self, cfg: Config, tg: Telegram, storage: Storage, stop: threading.Event):
        super().__init__(name="joins-tracker", daemon=True)
        self.cfg = cfg
        self.tg = tg
        self.storage = storage
        self._stop = stop
        self._offset: Optional[int] = None
        self._links: Dict[str, str] = {}

    def _refresh_links(self) -> None:
        """Обновляет список «адрес ссылки → её имя», чтобы понимать источник подписки."""
        self._links = {value: key.split(":", 1)[1] for key, value in self.storage.meta_items("invite_link:")}

    def run(self) -> None:
        self._refresh_links()
        log.info("Слежу за новыми подписчиками (источник определяю по пригласительной ссылке)")
        while not self._stop.is_set():
            try:
                updates = self.tg.get_updates(offset=self._offset, timeout=25, allowed_updates=["chat_member"])
            except TelegramError as exc:
                if "webhook" in str(exc).lower():
                    log.warning("У бота установлен webhook — подсчёт подписчиков невозможен: %s", exc)
                    return
                log.warning("Не удалось получить обновления Telegram: %s", exc)
                self._stop.wait(60)
                continue
            except Exception as exc:
                log.warning("Сбой при получении обновлений Telegram: %s", exc)
                self._stop.wait(60)
                continue

            for update in updates or []:
                self._offset = int(update.get("update_id", 0)) + 1
                try:
                    self._handle(update)
                except Exception:
                    log.exception("Ошибка при разборе обновления Telegram")
            if not updates:
                self._refresh_links()

    def _handle(self, update: dict) -> None:
        change = update.get("chat_member")
        if not change:
            return
        new = change.get("new_chat_member") or {}
        old = change.get("old_chat_member") or {}
        user = new.get("user") or {}
        was_inside = old.get("status") in MEMBER_STATUSES
        now_inside = new.get("status") in MEMBER_STATUSES

        if now_inside and not was_inside:
            invite = change.get("invite_link") or {}
            url = invite.get("invite_link")
            if url:
                source = self._links.get(url) or invite.get("name") or "другая ссылка"
            else:
                source = "без ссылки (добавил админ)"
            if self.storage.record_join(user.get("id"), user.get("username"), source):
                log.info("Новый подписчик: %s (источник: %s)", user.get("username") or user.get("id"), source)
        elif was_inside and new.get("status") in OUT_STATUSES:
            self.storage.record_leave(user.get("id"))
            log.info("Подписчик ушёл: %s", user.get("username") or user.get("id"))


# ---------------------------------------------------------------- отчёт
def show_stats(cfg: Config) -> int:
    """Печатает статистику подписчиков: команда `python main.py --stats`."""
    storage = Storage(cfg.db_path)
    tg = Telegram(cfg.bot_token, cfg.channel_id, cfg.api_base)
    print("\n=== Подписчики канала ===\n")
    try:
        print(f"Сейчас в канале: {tg.get_chat_member_count()}")
    except TelegramError as exc:
        print(f"Число подписчиков узнать не удалось: {exc}")

    total, active = storage.joins_total()
    if not total:
        print(
            "\nБот считает только новые подписки, поэтому на старте список пустой.\n"
            "Ведите бота дальше — данные накопятся (они хранятся в data/bot.db)."
        )
        return 0

    print(f"Пришло всего: {total}, из них осталось: {active}\n")
    print(f"{'источник':<32}{'пришло':>8}{'осталось':>10}")
    print("-" * 50)
    for name, came, stayed in storage.join_stats():
        print(f"{name:<32}{came:>8}{stayed:>10}")
    print()
    for label, days in (("за сутки", 1), ("за 7 дней", 7), ("за 30 дней", 30)):
        print(f"Новых {label}: {storage.joins_since(time.time() - days * 86400)}")
    return 0
