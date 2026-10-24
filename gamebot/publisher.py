"""Публикация поста в канал: картинки + трейлер, а если что-то не вышло — запасные варианты."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import formatting, media, youtube
from .config import Config
from .models import Post
from .telegram import Telegram, TelegramError, dual_button_markup, url_button_markup

log = logging.getLogger(__name__)


def build_markup(cfg: Config, post: Post, invite_url: Optional[str] = None) -> Optional[str]:
    """Формирует клавиатуру под постом: инлайн-кнопку действия и/или кнопку подписки."""
    sub_text = cfg.subscribe_button_text if cfg.subscribe_button and invite_url else None
    sub_url = invite_url if cfg.subscribe_button and invite_url else None
    
    if post.button_text and post.button_url:
        return dual_button_markup(post.button_text, post.button_url, sub_text, sub_url)
    elif sub_text and sub_url:
        return url_button_markup(sub_text, sub_url)
    return None


def publish(cfg: Config, tg: Telegram, post: Post, invite_url: Optional[str] = None) -> str:
    """Отправляет пост. Возвращает, каким способом удалось (для лога).

    invite_url — ссылка-приглашение для кнопки «Подписаться на канал».
    """
    markup = build_markup(cfg, post, invite_url)
    with tempfile.TemporaryDirectory(prefix="gamebot_") as tmp:
        photos: List[bytes] = []
        for url in post.photos:
            data = media.fetch_image(url)
            if data:
                photos.append(data)

        video: Optional[Tuple[Path, dict]] = None
        if cfg.send_trailers and (post.video_url or post.hls_url):
            path = media.prepare_video(cfg, post, Path(tmp))
            if path:
                video = (path, media.probe(path))

        # Видео получить не вышло — пробуем дать ссылку на трейлер (YouTube).
        trailer_line = ""
        if cfg.send_trailers and video is None:
            trailer_url = post.trailer_url or youtube.find_trailer(cfg, post.trailer_query)
            if trailer_url:
                trailer_line = formatting.trailer_line_html(cfg, trailer_url)
        text = formatting.fill_trailer_slot(post.text, trailer_line)

        attempts: List[Tuple[str, Callable[[], object]]] = []
        if video and photos:
            attempts.append(("альбом: картинки + трейлер", lambda: tg.send_album(photos, video, text, markup)))
        elif video:
            attempts.append(("видео-трейлер", lambda: tg.send_video(video, text, markup)))
        if len(photos) >= 2:
            attempts.append(("альбом из картинок", lambda: tg.send_album(photos, None, text, markup)))
        if photos:
            attempts.append(("одна картинка", lambda: tg.send_photo(photos[0], text, markup)))
        attempts.append(("текст со ссылкой", lambda: tg.send_message(text, preview_url=post.link, reply_markup=markup)))

        last_error: Optional[Exception] = None
        for label, action in attempts:
            try:
                action()
                log.info("Опубликовано: %s", label)
                # Если у поста есть интерактивный опрос — публикуем его следом
                if post.poll_question and post.poll_options:
                    try:
                        tg.send_poll(post.poll_question, post.poll_options)
                        log.info("Опубликован интерактивный опрос: %s", post.poll_question)
                    except Exception as poll_exc:
                        log.warning("Не удалось отправить опрос: %s", poll_exc)
                return label
            except TelegramError as exc:
                if exc.fatal:
                    raise
                log.warning("Способ «%s» не сработал: %s", label, exc)
                last_error = exc
            except Exception as exc:  # неожиданная ошибка — пробуем следующий способ
                log.warning("Способ «%s» не сработал: %s", label, exc)
                last_error = exc
        assert last_error is not None
        raise last_error
