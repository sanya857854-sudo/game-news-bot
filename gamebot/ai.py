"""Необязательный ИИ-помощник: переводит и сокращает тексты на русский.

Работает с любым сервисом, у которого есть «OpenAI-совместимый» API
(у Google AI Studio, Groq, OpenRouter есть бесплатные тарифы с лимитами).
Если ключ не задан, бот просто обходится без перевода.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional, Tuple

import requests

from .config import Config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты редактор русскоязычного Telegram-канала о видеоиграх. Пиши по-русски, просто и живо. "
    "Названия игр, студий и платформ оставляй в оригинале. Ничего не выдумывай: используй "
    "только факты из присланного текста. Отвечай строго одним JSON-объектом, без пояснений и без markdown."
)


def enabled(cfg: Config) -> bool:
    return bool(cfg.ai_base_url and cfg.ai_api_key and cfg.ai_model)


def _chat(cfg: Config, user_prompt: str) -> Optional[str]:
    url = cfg.ai_base_url.rstrip("/") + "/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {cfg.ai_api_key}", "Content-Type": "application/json"},
            json={
                "model": cfg.ai_model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        log.warning("ИИ-перевод не удался: %s", str(exc).replace(cfg.ai_api_key, "***"))
        return None


def _json_from(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def translate_news(cfg: Config, title: str, summary: str) -> Optional[Tuple[str, str]]:
    prompt = (
        "Переведи новость на русский и сократи.\n"
        'Верни JSON: {"title": "...", "text": "..."}.\n'
        "title — заголовок до 90 символов, без кавычек вокруг; "
        "text — 1–2 коротких предложения (до 250 символов) по сути новости; "
        "если исходного текста нет, text оставь пустой строкой.\n\n"
        f"Заголовок: {title}\nТекст: {summary or '(нет)'}"
    )
    data = _json_from(_chat(cfg, prompt))
    if not data:
        return None
    new_title = str(data.get("title") or "").strip()
    new_text = str(data.get("text") or "").strip()
    if not new_title:
        return None
    return new_title[:150], new_text[:400]


def translate_description(cfg: Config, name: str, text: str) -> Optional[str]:
    prompt = (
        f"Игра: {name}.\n"
        "Переведи описание на русский и сократи до 1–2 предложений (до 230 символов).\n"
        'Верни JSON: {"text": "..."}.\n\n'
        f"Описание: {text}"
    )
    data = _json_from(_chat(cfg, prompt))
    if not data:
        return None
    result = str(data.get("text") or "").strip()
    return result or None
