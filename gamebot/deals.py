"""Источник: топовые скидки Steam и исторические минимумы цен (All-Time Low).

Использует CheapShark Deals API для поиска лучших скидок, сравнения с историческим
минимумом за всё время и отбора признанных хитов.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from . import formatting, net, steam
from .config import Config
from .models import Candidate, Post
from .storage import Storage

log = logging.getLogger(__name__)

CHEAPSHARK_DEALS_URL = "https://www.cheapshark.com/api/1.0/deals"
USER_AGENT_HEADERS = {"User-Agent": "GameNewsBot/1.0 (gameworld2029)"}


def fetch_top_deals(limit: int = 30) -> List[dict]:
    """Получает топ самых выгодных сделок Steam из CheapShark."""
    try:
        params = {
            "storeID": "1",  # 1 = Steam
            "pageSize": str(limit),
            "sortBy": "Deal Rating",
        }
        resp = net.get(CHEAPSHARK_DEALS_URL, params=params, headers=USER_AGENT_HEADERS, timeout=15)
        data = resp.json()
        if isinstance(data, list):
            return data
    except Exception as exc:
        log.warning("Не удалось получить скидки из CheapShark: %s", exc)
    return []


def fetch_deal_details(deal_id: str) -> Optional[dict]:
    """Получает детальную информацию о сделке, включая рекордную цену (cheapestPrice)."""
    try:
        resp = net.get(CHEAPSHARK_DEALS_URL, params={"id": deal_id}, headers=USER_AGENT_HEADERS, timeout=12)
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception as exc:
        log.debug("Не удалось получить детали сделки %s: %s", deal_id, exc)
    return None


def format_deal_post(cfg: Config, deal: dict, details: Optional[dict] = None) -> Optional[Post]:
    """Форматирует пост о скидке в стиле лидеров рынка (Препод Стим+)."""
    appid_str = deal.get("steamAppID")
    if not appid_str or not appid_str.isdigit():
        return None
    appid = int(appid_str)

    title = deal.get("title") or "Игра"
    savings = int(float(deal.get("savings", 0)))
    sale_price_usd = deal.get("salePrice", "0")
    normal_price_usd = deal.get("normalPrice", "0")

    # Проверка на исторический минимум цены (All-Time Low)
    is_historic_low = False
    if details:
        cheapest_info = details.get("cheapestPrice") or {}
        lowest_ever = float(cheapest_info.get("price", 999999))
        current_sale = float(sale_price_usd)
        if current_sale <= lowest_ever + 0.05:
            is_historic_low = True

    # Рейтинг и количество отзывов в Steam
    steam_rating_percent = deal.get("steamRatingPercent")
    steam_rating_text = deal.get("steamRatingText") or ""
    rating_count = int(deal.get("steamRatingCount") or 0)
    metacritic = int(deal.get("metacriticScore") or 0)

    # Проверка на хит
    is_hit = rating_count >= 5000 and int(steam_rating_percent or 0) >= 80

    # Получаем данные о приложении из Steam (описание, цены в рублях/валютах, скриншоты)
    steam_data = steam.fetch_details(appid, cfg)
    if not steam_data:
        return None

    # Описание на русском
    description = steam._describe(cfg, steam_data, title)

    # Мультивалютные цены из Steam
    prices = steam.fetch_prices(appid, cfg.price_regions, "final")
    if not prices:
        # Запасной вариант цен из CheapShark в долларах
        prices = [f"{sale_price_usd}$"]

    slash_prices = "/".join(prices)

    # Картинки и скриншоты
    screenshots = [
        s.get("path_full") or s.get("path_thumbnail")
        for s in (steam_data.get("screenshots") or [])
    ]
    photos = [u for u in screenshots if u][: cfg.max_screenshots]
    if not photos and steam_data.get("header_image"):
        photos = [steam_data["header_image"]]

    # Трейлер (если доступен)
    video_url, hls_url = steam._pick_trailer(steam_data.get("movies") or []) if cfg.send_trailers else (None, None)

    # Формирование заголовка и плашки
    esc_title = formatting.esc(title)
    if is_historic_low:
        badge = "🔥 <b>ИСТОРИЧЕСКИЙ МИНИМУМ ЦЕНЫ В STEAM</b>\n"
        title_line = f"<b>{esc_title} — скидка {savings}% (Рекордная цена!)</b>"
    elif is_hit:
        badge = "⭐ <b>ТОП-ХИТ СО СКИДКОЙ В STEAM</b>\n"
        title_line = f"<b>{esc_title} со скидкой {savings}%</b>"
    else:
        badge = "🏷 <b>ВЫГОДНОЕ ПРЕДЛОЖЕНИЕ В STEAM</b>\n"
        title_line = f"<b>{esc_title} со скидкой {savings}%</b>"

    lines = [f"{badge}{title_line}"]

    if description:
        lines.append(formatting.esc(description))

    # Блок цен
    lines.append(f"💰 Купить игру можно за <b>{formatting.esc(slash_prices)}</b>.")

    # Блок оценок
    metrics = []
    if metacritic > 0:
        metrics.append(f"🏆 Metacritic: <b>{metacritic}/100</b>")
    if steam_rating_percent and int(steam_rating_percent) > 0:
        rc_str = f"{rating_count:,}".replace(",", " ")
        rating_label = steam_rating_text or "Положительные"
        metrics.append(f"👍 Отзывы в Steam: <b>{rating_label}</b> ({steam_rating_percent}% из {rc_str})")

    # Онлайн прямо сейчас
    player_count = steam.fetch_player_count(appid)
    if player_count and player_count >= 1000:
        pc_str = f"{player_count:,}".replace(",", " ")
        metrics.append(f"👥 Онлайн прямо сейчас: <b>{pc_str}</b> игроков")

    if metrics:
        lines.append("\n".join(metrics))

    # Вопрос для вовлечения
    if is_historic_low:
        lines.append("Ниже цены ещё не было. Будете брать?")
    else:
        lines.append("Отличный повод добавить в коллекцию!")

    # Хэштеги
    hashtags = ["#скидки", "#игры", "#steam"]
    if is_historic_low:
        hashtags.append("#исторический_минимум")
    if is_hit:
        hashtags.append("#хит")
    lines.append(" ".join(hashtags))

    # Подпись канала
    sig = formatting.signature_html(cfg)
    if sig:
        lines.append(sig)

    store_url = steam.STORE_APP_URL.format(appid)

    # Интерактивный опрос
    poll_question = f"Как вам скидка на {title} (-{savings}%)?"
    poll_options = ["🔥 Однозначно брать!", "👀 Уже прошел / есть в библиотеке", "❌ Не мой жанр"]

    return Post(
        text="\n\n".join(lines),
        photos=photos,
        video_url=video_url,
        hls_url=hls_url,
        trailer_query=title,
        link=store_url,
        button_text=f"🎮 Забрать со скидкой -{savings}% в Steam",
        button_url=store_url,
        poll_question=poll_question,
        poll_options=poll_options,
    )


def list_candidates(cfg: Config, storage: Storage) -> List[Candidate]:
    """Сбор кандидатов на публикацию из топа скидок CheapShark."""
    deals = fetch_top_deals(limit=25)
    candidates: List[Candidate] = []
    month_tag = time.strftime("%Y%m")

    for deal in deals:
        appid_str = deal.get("steamAppID")
        if not appid_str or not appid_str.isdigit():
            continue
        appid = int(appid_str)

        savings = int(float(deal.get("savings", 0)))
        if savings < cfg.min_discount:
            continue

        deal_id = deal.get("dealID") or f"deal_{appid}"
        key = f"deal:{appid}:{savings}:{month_tag}"

        if storage.is_known(key):
            continue
        # Не повторяем ту же игру чаще чем раз в 30 дней
        if storage.posted_with_prefix_since(f"deal:{appid}:", time.time() - 30 * 86400):
            continue
        if storage.posted_with_prefix_since(f"steam:{appid}:", time.time() - 30 * 86400):
            continue

        # Быстрый скоринг
        rating_count = int(deal.get("steamRatingCount") or 0)
        steam_rating = int(deal.get("steamRatingPercent") or 0)
        deal_rating = float(deal.get("dealRating", 0))

        # Базовый скор от 5.0 до 8.0
        score = 4.5 + min(3.5, deal_rating / 3.0)
        if rating_count >= 5000 and steam_rating >= 80:
            score += 1.5  # бонус за топ-хит

        title = deal.get("title") or f"App {appid}"

        def _builder(d=deal, did=deal_id):
            det = fetch_deal_details(did)
            return format_deal_post(cfg, d, det)

        candidates.append(
            Candidate(
                key=key,
                kind="discount",
                title=f"Скидка: {title} (-{savings}%)",
                score=score,
                build=_builder,
            )
        )

    log.info("Скидки CheapShark: найдено кандидатов — %d", len(candidates))
    return candidates
