"""Необязательно: поиск официального трейлера на YouTube через официальный API.

Нужен бесплатный ключ YouTube Data API v3. Без ключа модуль ничего не делает.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from .config import Config

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def find_trailer(cfg: Config, game_name: Optional[str]) -> Optional[str]:
    if not cfg.youtube_api_key or not game_name:
        return None
    try:
        resp = requests.get(
            SEARCH_URL,
            params={
                "part": "snippet",
                "q": f"{game_name} official trailer",
                "type": "video",
                "videoEmbeddable": "true",
                "maxResults": 5,
                "key": cfg.youtube_api_key,
            },
            timeout=20,
        )
        resp.raise_for_status()
        wanted = _norm(game_name)
        for item in resp.json().get("items", []):
            title = _norm(item["snippet"]["title"])
            if wanted in title and ("trailer" in title or "трейлер" in title):
                return f"https://www.youtube.com/watch?v={item['id']['videoId']}"
    except Exception as exc:
        log.warning("Поиск трейлера на YouTube не удался: %s", str(exc).replace(cfg.youtube_api_key, "***"))
    return None
