"""Пятничный дайджест: одним постом — все самые популярные игры Steam, которые сейчас со скидкой.

Как работает:
  1. Берём список скидок Steam у CheapShark (бесплатный открытый сервис) — несколько страниц, разные сортировки.
  2. Оставляем только популярные игры: много отзывов в Steam, высокая оценка, скидка не меньше заданной.
  3. Цены в рублях (или в первом регионе из PRICE_REGIONS) берём у самого Steam — одним запросом на все игры.
  4. Собираем один пост-список со ссылками и опрос «что берёте?».
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from . import formatting, net, steam
from .config import Config
from .models import Post

log = logging.getLogger(__name__)

CHEAPSHARK_DEALS_URL = "https://www.cheapshark.com/api/1.0/deals"
HEADERS = {"User-Agent": "GameNewsBot/1.0 (gameworld2029)"}
SPECIALS_URL = "https://store.steampowered.com/specials"
TELEGRAM_TEXT_LIMIT = 4000       # у Telegram предел 4096, оставляем запас
MIN_RATING_PERCENT = 70          # ниже этой оценки игру в «популярные» не берём
MIN_ITEMS = 6                    # если нашлось меньше — смягчаем требование к числу отзывов


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def fetch_pool() -> List[dict]:
    """Собирает общий список скидок из нескольких запросов (дубликаты убираются)."""
    queries = [
        {"sortBy": "Reviews", "pageNumber": "0"},
        {"sortBy": "Reviews", "pageNumber": "1"},
        {"sortBy": "Deal Rating", "pageNumber": "0"},
    ]
    pool: Dict[str, dict] = {}
    for extra in queries:
        params = {"storeID": "1", "onSale": "1", "pageSize": "60", "desc": "1"}
        params.update(extra)
        try:
            data = net.get(CHEAPSHARK_DEALS_URL, params=params, headers=HEADERS, timeout=20).json()
        except Exception as exc:
            log.warning("Дайджест: не получилось взять скидки (%s): %s", extra, exc)
            continue
        if not isinstance(data, list):
            continue
        for deal in data:
            appid = deal.get("steamAppID")
            if appid and str(appid).isdigit() and str(appid) not in pool:
                pool[str(appid)] = deal
    return list(pool.values())


def pick_popular(cfg: Config, pool: List[dict]) -> List[dict]:
    """Оставляет самые популярные игры со скидкой, самые народные — первыми."""

    def select(min_reviews: int) -> List[dict]:
        chosen = [
            d for d in pool
            if _int(d.get("savings")) >= cfg.digest_min_discount
            and _int(d.get("steamRatingCount")) >= min_reviews
            and _int(d.get("steamRatingPercent")) >= MIN_RATING_PERCENT
        ]
        chosen.sort(key=lambda d: _int(d.get("steamRatingCount")), reverse=True)
        return chosen

    chosen = select(cfg.digest_min_reviews)
    if len(chosen) < MIN_ITEMS:
        chosen = select(max(3000, cfg.digest_min_reviews // 4))
    return chosen


def fetch_region_prices(appids: List[int], region: str) -> Dict[int, dict]:
    """Цены Steam для списка игр одним запросом: {appid: {'initial':…, 'final':…, 'currency':…, 'pct':…}}."""
    result: Dict[int, dict] = {}
    if not appids:
        return result
    try:
        resp = net.get(
            steam.DETAILS_URL,
            params={"appids": ",".join(str(a) for a in appids), "cc": region, "filters": "price_overview"},
            timeout=25,
        )
        payload = resp.json()
    except Exception as exc:
        log.warning("Дайджест: цены Steam одним запросом не получены (%s)", exc)
        return result
    for appid in appids:
        entry = (payload or {}).get(str(appid)) or {}
        data = entry.get("data")
        overview = data.get("price_overview") if isinstance(data, dict) else None
        if overview and overview.get("currency") and overview.get("final") is not None:
            result[appid] = {
                "initial": int(overview.get("initial") or 0),
                "final": int(overview["final"]),
                "currency": overview["currency"],
                "pct": int(overview.get("discount_percent") or 0),
            }
    return result


def _usd(value) -> str:
    try:
        return formatting.format_price(int(round(float(value) * 100)), "USD")
    except (TypeError, ValueError):
        return ""


def _is_ok_game(cfg: Config, appid: int) -> bool:
    """Отсеивает не-игры и контент 18+ (если включён SKIP_ADULT). Сбой проверки — не причина терять игру."""
    if not cfg.skip_adult:
        return True
    try:
        details = steam.fetch_details(appid, cfg)
    except Exception:
        return True
    if not details:
        return True
    if details.get("type") not in (None, "game"):
        return False
    return not steam._is_adult(details)


def build_post(cfg: Config) -> Optional[Post]:
    """Собирает пост-дайджест. None — подходящих скидок не нашлось (попробуем позже)."""
    popular = pick_popular(cfg, fetch_pool())
    if not popular:
        log.info("Дайджест: популярных скидок пока нет")
        return None

    region = cfg.price_regions[0] if cfg.price_regions else "ru"
    # берём с запасом — часть игр может отсеяться из-за 18+ или цены
    shortlist = popular[: cfg.digest_max_items + 6]
    prices = fetch_region_prices([int(d["steamAppID"]) for d in shortlist], region)

    items: List[dict] = []
    for deal in shortlist:
        if len(items) >= cfg.digest_max_items:
            break
        appid = int(deal["steamAppID"])
        price = prices.get(appid)
        if price and price["pct"] == 0:
            continue            # в выбранном регионе скидки на эту игру нет — не вводим читателей в заблуждение
        if not _is_ok_game(cfg, appid):
            continue
        pct = price["pct"] if price else _int(deal.get("savings"))
        if price:
            now_txt = formatting.format_price(price["final"], price["currency"])
            was_txt = formatting.format_price(price["initial"], price["currency"])
        else:
            now_txt, was_txt = _usd(deal.get("salePrice")), _usd(deal.get("normalPrice"))
        items.append({
            "appid": appid,
            "title": deal.get("title") or f"App {appid}",
            "pct": pct,
            "now": now_txt,
            "was": was_txt,
            "rating": _int(deal.get("steamRatingPercent")),
        })
    if len(items) < 3:
        log.info("Дайджест: после проверок осталось мало игр (%d) — не публикую", len(items))
        return None

    return _assemble(cfg, items)


def _assemble(cfg: Config, items: List[dict]) -> Post:
    head = [
        f"{formatting.rubric_line(cfg, '🛒 ПЯТНИЧНЫЕ СКИДКИ')}<b>Пятничные скидки в Steam: что сейчас продаётся дешевле</b>",
        "Собрали самые популярные игры с хорошими оценками, на которые прямо сейчас действует скидка.",
    ]
    tail = []
    if cfg.post_question:
        tail.append("Что берёте на выходные?")
    if cfg.enable_hashtags:
        tail.append("#скидки #пятница #распродажа #steam")
    sig = formatting.signature_html(cfg)
    if sig:
        tail.append(sig)

    def render(count: int) -> str:
        lines = []
        for n, it in enumerate(items[:count], 1):
            price = f"{formatting.esc(it['now'])} <s>{formatting.esc(it['was'])}</s>" if it["was"] else formatting.esc(it["now"])
            rating = f" · 👍 {it['rating']}%" if it["rating"] else ""
            url = steam.STORE_APP_URL.format(it["appid"])
            lines.append(
                f'{n}. <a href="{formatting.esc_attr(url)}">{formatting.esc(it["title"])}</a> — '
                f'<b>−{it["pct"]}%</b> · {price}{rating}'
            )
        return "\n\n".join(["\n".join(head), "\n".join(lines), *tail])

    count = len(items)
    text = render(count)
    while len(text) > TELEGRAM_TEXT_LIMIT and count > 3:
        count -= 1
        text = render(count)

    top = [it["title"] for it in items[:count]][:4]
    poll_options = [t[:90] for t in top] + ["Жду скидку побольше"]
    return Post(
        text=text,
        photos=[],
        link=None,
        button_text="🛒 Все скидки Steam",
        button_url=SPECIALS_URL,
        poll_question="Что берёте на выходные?",
        poll_options=poll_options,
    )
