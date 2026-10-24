"""Общие структуры данных."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

# В тексте поста это место заменяется строкой «Смотреть трейлер» (или удаляется).
TRAILER_SLOT = "§§TRAILER§§"


@dataclass
class Post:
    """Готовый к публикации пост."""

    text: str                                   # HTML-подпись (для Telegram)
    photos: List[str] = field(default_factory=list)   # ссылки на картинки
    video_url: Optional[str] = None             # прямая ссылка на mp4 (если есть)
    hls_url: Optional[str] = None               # ссылка на HLS-трейлер (нужен ffmpeg)
    trailer_url: Optional[str] = None           # готовая ссылка на трейлер (например, YouTube)
    trailer_query: Optional[str] = None         # что искать на YouTube, если ключ задан
    link: Optional[str] = None                  # страница материала
    button_text: Optional[str] = None           # текст инлайн-кнопки
    button_url: Optional[str] = None            # ссылка для инлайн-кнопки
    poll_question: Optional[str] = None         # вопрос для интерактивного опроса
    poll_options: List[str] = field(default_factory=list)  # варианты ответа опроса


@dataclass
class Candidate:
    """Кандидат на публикацию. Тяжёлую работу (запросы, перевод) делает build()."""

    key: str                                    # уникальный ключ для защиты от повторов
    kind: str                                   # release / upcoming / free / discount / news
    title: str
    score: float
    build: Callable[[], Optional[Post]]
