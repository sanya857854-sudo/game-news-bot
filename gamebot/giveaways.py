"""Источник: бесплатные раздачи игр (Epic Games Store, GOG, Steam, itch.io).

Использует GamerPower API (бесплатный публичный трекер всех легальных 100% раздач игр).
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from . import formatting, net
from .config import Config
from .models import Candidate, Post
from .storage import Storage

log = logging.getLogger(__name__)

GAMERPOWER_API = "https://www.gamerpower.com/api/giveaways"


def fetch_giveaways(platform: Optional[str] = "pc") -> List[dict]:
    """Получает список активных бесплатных раздач игр."""
    try:
        params = {"type": "game"}
        if platform:
            params["platform"] = platform
        resp = net.get(GAMERPOWER_API, params=params, timeout=15)
        data = resp.json()
        if isinstance(data, list):
            return data
    except Exception as exc:
        log.warning("Не удалось получить список раздач с GamerPower: %s", exc)
    return []


def format_giveaway_post(cfg: Config, item: dict) -> Post:
    """Форматирует пост о бесплатной раздаче игры."""
    title = formatting.clean_text(item.get("title") or "Бесплатная игра")
    # Очищаем заголовок от суффиксов "(Epic Games) Giveaway"
    clean_title = title.replace("Giveaway", "").replace("giveaway", "").strip()
    
    platforms = item.get("platforms") or "PC"
    worth = item.get("worth") or "N/A"
    end_date = item.get("end_date")
    description = formatting.clean_text(item.get("description") or "")
    image_url = item.get("image")
    giveaway_url = item.get("open_giveaway_url") or item.get("gamerpower_url")

    # Формируем текст
    lines = []
    lines.append(f"<b>🎉 ХАЛЯВА: {formatting.esc(clean_title)} отдают бесплатно!</b>\n")
    
    if worth and worth != "N/A":
        lines.append(f"💰 Обычная цена: <s>{formatting.esc(worth)}</s> ➡️ <b>0 ₽ (БЕСПЛАТНО)</b>")
    
    lines.append(f"🕹 Платформа: <b>{formatting.esc(platforms)}</b>")
    
    if end_date and end_date != "N/A":
        lines.append(f"⏳ Раздача продлится: до {formatting.esc(end_date)}")
    
    if description:
        short_desc = formatting.shorten(description, 260)
        lines.append(f"\n📝 {formatting.esc(short_desc)}")

    if giveaway_url:
        lines.append(f"\n👉 <a href=\"{formatting.esc_attr(giveaway_url)}\">Забрать игру бесплатно</a>")

    lines.append("\nУспейте забрать на свой аккаунт!")
    
    # Хэштеги
    hashtags = "#раздача #халява #игры #бесплатно"
    if "epic" in platforms.lower():
        hashtags += " #epicgames"
    elif "steam" in platforms.lower():
        hashtags += " #steam"
    elif "gog" in platforms.lower():
        hashtags += " #gog"
    
    lines.append(f"\n{hashtags}")
    
    # Подпись канала
    sig = formatting.signature_html(cfg)
    if sig:
        lines.append(f"\n{sig}")

    photos = [image_url] if image_url else []
    
    return Post(
        text="\n".join(lines),
        photos=photos,
        link=giveaway_url,
        button_text="🎁 Забрать игру бесплатно",
        button_url=giveaway_url,
    )


def list_candidates(cfg: Config, storage: Storage) -> List[Candidate]:
    """Собирает список доступных бесплатных раздач игр."""
    items = fetch_giveaways()
    candidates: List[Candidate] = []
    
    for item in items:
        gid = item.get("id")
        title = item.get("title") or f"giveaway_{gid}"
        platforms = (item.get("platforms") or "").lower()
        status = (item.get("status") or "").lower()
        
        # Берем только проверенные раздачи крупных платформ (Steam, Epic Games Store, GOG)
        # Отсекаем мусорный инди-шлак с itch.io, IndieGala, Stove и неизвестных сайтов
        if status and status != "active":
            continue
        if any(b in platforms for b in ("itch.io", "indiegala", "stove")):
            continue
        if not any(p in platforms for p in ("steam", "epic", "gog")):
            continue
            
        key = f"giveaway:{gid}"
        if storage.is_known(key):
            continue
            
        # Сбалансированный скор (не выше топовых скидок и хитов)
        score = 6.5
        if "steam" in platforms or "epic" in platforms:
            score += 0.8
        candidates.append(
            Candidate(
                key=key,
                kind="free",
                title=f"Раздача: {title}",
                score=score,
                build=lambda it=item: format_giveaway_post(cfg, it),
            )
        )
        
    log.info("Раздачи (GamerPower): найдено кандидатов — %d", len(candidates))
    return candidates
