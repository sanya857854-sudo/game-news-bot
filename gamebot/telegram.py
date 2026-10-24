"""Минимальный клиент Telegram Bot API (через requests).

Умеет: повторять запросы при сбоях сети, ждать при лимитах Telegram (ошибка 429)
и никогда не пишет токен бота в логи.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import requests

log = logging.getLogger(__name__)

FileSource = Union[bytes, Path]
Video = Tuple[Path, Dict[str, int]]


def url_button_markup(text: str, url: str) -> str:
    """Клавиатура из одной кнопки-ссылки (для подписи «Подписаться» под постом)."""
    return json.dumps({"inline_keyboard": [[{"text": text, "url": url}]]})


def dual_button_markup(
    primary_text: str,
    primary_url: str,
    sub_text: Optional[str] = None,
    sub_url: Optional[str] = None,
) -> str:
    """Клавиатура из двух кнопок: целевое действие (Открыть/Забрать) и Подписаться."""
    keyboard = []
    if primary_text and primary_url:
        keyboard.append([{"text": primary_text, "url": primary_url}])
    if sub_text and sub_url:
        keyboard.append([{"text": sub_text, "url": sub_url}])
    return json.dumps({"inline_keyboard": keyboard})


class TelegramError(Exception):
    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code

    @property
    def fatal(self) -> bool:
        """Ошибки настройки: повторять бессмысленно (неверный токен, бот не админ и т.п.)."""
        text = str(self).lower()
        return self.code in (401, 403) or "chat not found" in text or "not enough rights" in text


class Telegram:
    def __init__(self, token: str, chat_id: str, api_base: str = "https://api.telegram.org"):
        self.token = token
        self.chat_id = chat_id
        self.api_base = api_base.rstrip("/")

    # ---------------------------------------------------------------- низкий уровень
    def _redact(self, text: str) -> str:
        return text.replace(self.token, "<токен>") if self.token else text

    def call(
        self,
        method: str,
        data: Optional[dict] = None,
        files: Optional[Dict[str, Tuple[str, FileSource]]] = None,
        timeout: Tuple[int, int] = (15, 120),
        retries: int = 4,
    ):
        url = f"{self.api_base}/bot{self.token}/{method}"
        for attempt in range(1, retries + 1):
            try:
                with ExitStack() as stack:
                    prepared = None
                    if files:
                        prepared = {}
                        for field, (filename, source) in files.items():
                            if isinstance(source, Path):
                                source = stack.enter_context(open(source, "rb"))
                            prepared[field] = (filename, source)
                    resp = requests.post(url, data=data, files=prepared, timeout=timeout)
            except requests.RequestException as exc:
                message = self._redact(str(exc))
                if attempt == retries:
                    raise TelegramError(f"Нет связи с Telegram: {message}") from None
                log.warning("Telegram %s: сбой сети (%s), попытка %d/%d", method, message[:120], attempt, retries)
                time.sleep(3 * attempt)
                continue

            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            if resp.status_code == 200 and payload.get("ok"):
                return payload.get("result")

            description = payload.get("description") or resp.text[:200]
            code = int(payload.get("error_code") or resp.status_code)
            if code == 429 and attempt < retries:
                wait = int((payload.get("parameters") or {}).get("retry_after", 5)) + 1
                if wait <= 180:
                    log.warning("Telegram просит подождать %d с (лимит) — жду", wait)
                    time.sleep(wait)
                    continue
            elif code >= 500 and attempt < retries:
                log.warning("Telegram %s: ошибка сервера %d, повторю", method, code)
                time.sleep(3 * attempt)
                continue
            raise TelegramError(self._redact(description), code=code)

    # ---------------------------------------------------------------- проверки
    def get_me(self) -> dict:
        return self.call("getMe", timeout=(10, 20), retries=2)

    def get_chat(self) -> dict:
        return self.call("getChat", data={"chat_id": self.chat_id}, timeout=(10, 20), retries=2)

    def get_member(self, user_id: int) -> dict:
        return self.call(
            "getChatMember", data={"chat_id": self.chat_id, "user_id": user_id}, timeout=(10, 20), retries=2
        )

    def get_chat_member_count(self) -> int:
        """Сколько человек сейчас в канале."""
        return int(self.call("getChatMemberCount", data={"chat_id": self.chat_id}, timeout=(10, 20), retries=2))

    def create_chat_invite_link(self, name: str = "post") -> dict:
        """Создаёт пригласительную ссылку на канал (имя — чтобы потом отличать источники)."""
        return self.call(
            "createChatInviteLink", data={"chat_id": self.chat_id, "name": name}, timeout=(10, 20), retries=2
        )

    def get_updates(self, offset: Optional[int] = None, timeout: int = 25, allowed_updates: Optional[List[str]] = None):
        """Долгое ожидание обновлений Telegram (нужно, чтобы считать новых подписчиков)."""
        data: dict = {"timeout": timeout}
        if offset is not None:
            data["offset"] = offset
        if allowed_updates:
            data["allowed_updates"] = json.dumps(allowed_updates)
        return self.call("getUpdates", data=data, timeout=(10, timeout + 20), retries=2)

    # ---------------------------------------------------------------- отправка
    def send_message(
        self, text: str, preview_url: Optional[str] = None, reply_markup: Optional[str] = None
    ) -> dict:
        data: dict = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        if preview_url:
            data["link_preview_options"] = json.dumps(
                {"url": preview_url, "prefer_large_media": True, "show_above_text": True}
            )
        else:
            data["link_preview_options"] = json.dumps({"is_disabled": True})
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.call("sendMessage", data=data)

    def send_photo(self, photo: bytes, caption: str, reply_markup: Optional[str] = None) -> dict:
        data = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.call("sendPhoto", data=data, files={"photo": ("photo.jpg", photo)})

    def send_video(self, video: Video, caption: str, reply_markup: Optional[str] = None) -> dict:
        path, meta = video
        data = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML", "supports_streaming": "true"}
        data.update({k: str(v) for k, v in meta.items()})
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.call("sendVideo", data=data, files={"video": ("trailer.mp4", path)}, timeout=(15, 300))

    def send_album(
        self, photos: List[bytes], video: Optional[Video], caption: str, reply_markup: Optional[str] = None
    ) -> dict:
        """Альбом: первая картинка → трейлер → остальные картинки. Подпись — под всем альбомом."""
        media: List[dict] = []
        files: Dict[str, Tuple[str, FileSource]] = {}

        def add_photo(index: int) -> None:
            media.append({"type": "photo", "media": f"attach://photo{index}"})
            files[f"photo{index}"] = (f"photo{index}.jpg", photos[index])

        def add_video() -> None:
            assert video is not None
            path, meta = video
            item = {"type": "video", "media": "attach://video0", "supports_streaming": True}
            item.update(meta)
            media.append(item)
            files["video0"] = ("trailer.mp4", path)

        if photos:
            add_photo(0)
        if video is not None:
            add_video()
        for i in range(1, len(photos)):
            add_photo(i)

        media[0]["caption"] = caption
        media[0]["parse_mode"] = "HTML"
        data = {"chat_id": self.chat_id, "media": json.dumps(media)}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.call("sendMediaGroup", data=data, files=files, timeout=(15, 300))

    def send_poll(
        self,
        question: str,
        options: List[str],
        is_anonymous: bool = True,
        allows_multiple_answers: bool = False,
    ) -> dict:
        """Отправляет интерактивный опрос в канал."""
        data = {
            "chat_id": self.chat_id,
            "question": question[:300],
            "options": json.dumps(options[:10]),
            "is_anonymous": is_anonymous,
            "allows_multiple_answers": allows_multiple_answers,
        }
        return self.call("sendPoll", data=data)
