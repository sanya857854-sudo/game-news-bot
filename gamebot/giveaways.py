"""Источник: бесплатные раздачи игр — ТОЛЬКО Steam и Epic Games Store.

Откуда берутся данные:
  • Epic Games Store — официальная витрина Epic (раздел «Бесплатные игры»), без ключей и регистрации.
  • Steam — трекер GamerPower. Каждая раздача проверяется: ссылка должна вести на настоящую
    страницу игры в магазине Steam. Раздачи «ключей» на сторонних сайтах, GOG, itch.io и прочее отбрасываются.
    Пост про Steam-раздачу собирается тем же кодом, что и обычные посты про Steam
    (описание по-русски, цены в рублях/тенге/гривнах, скриншоты).
  • Если витрина Epic временно недоступна, Epic-раздачи берутся из GamerPower — как запасной вариант.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional

from . import formatting, net, steam
from .config import Config
from .models import Candidate, Post
from .storage import Storage

log = logging.getLogger(__name__)

EPIC_PROMO_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
EPIC_STORE_URL = "https://store.epicgames.com/ru/p/{}"
EPIC_FREE_PAGE = "https://store.epicgames.com/ru/free-games"
GAMERPOWER_API = "https://www.gamerpower.com/api/giveaways"

MAX_RESOLVE_PER_RUN = 12   # сколько новых ссылок GamerPower проверяем за один заход

_APP_RE = re.compile(r"store\.steampowered\.com/(?:agecheck/)?app/(\d+)", re.I)


# ---------------------------------------------------------------- общие мелочи
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "", (name or "").lower())


def clean_title(raw: str) -> str:
    """«Death Stranding (Epic Games) Giveaway» -> «Death Stranding»."""
    title = formatting.clean_text(raw or "")
    title = re.sub(r"\s*\((?:[^)]*(?:steam|epic|gog|pc|key)[^)]*)\)", "", title, flags=re.I)
    title = re.sub(r"\s*[-–—:]?\s*giveaway\s*$", "", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip(" -–—:") or "Бесплатная игра"


def _iso_ts(value: Optional[str]) -> Optional[float]:
    """«2026-10-30T15:00:00.000Z» или «2026-10-30 23:59:00» (время UTC) -> unix-время."""
    if not value or str(value).strip().upper() == "N/A":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _store_post_text(
    cfg: Config,
    store_name: str,
    title: str,
    description: str,
    regular_price: str,
    until_ts: Optional[float],
    store_url: str,
) -> str:
    """Текст поста про раздачу (Epic и запасной вариант). Структура — как у остальных постов канала."""
    n = formatting.esc(title)
    pre = formatting.line_prefix(cfg)
    middle: List[str] = []
    if until_ts:
        left = formatting.time_left_phrase(until_ts)
        left_txt = f" ({left})" if left else ""
        middle.append(
            f"{pre}Забрать игру бесплатно можно до {formatting.ru_date_from_ts(until_ts, cfg.timezone)}{left_txt}."
        )
    else:
        middle.append(f"{pre}Забрать игру можно бесплатно, пока идёт акция.")
    if regular_price:
        middle.append(f"{pre}Обычная цена — {formatting.esc(regular_price)}.")

    title_line = (
        f"{formatting.rubric_line(cfg, '🎁 ХАЛЯВА')}"
        f"{formatting._prefix(cfg, formatting.title_emoji(cfg, 'free'))}"
        f"<b>В {formatting.esc(store_name)} началась раздача {n}</b>"
    )
    tail = [f"{pre}Заберите, пока акция идёт: потом игра вернётся к обычной цене."]
    if cfg.show_steam_link and store_url:
        tail.append(f'{pre}<a href="{formatting.esc_attr(store_url)}">Страница в {formatting.esc(store_name)}</a>')
    question = formatting.engagement_html(cfg, "free")
    if question:
        tail.append(question)
    tag = "#epicgames" if "epic" in store_name.lower() else "#steam"
    hashtags = f"#раздача #халява #игры {tag}" if cfg.enable_hashtags else ""
    return formatting._assemble(cfg, title_line, description, middle, tail, with_slot=False, hashtags=hashtags)


# ---------------------------------------------------------------- Epic Games Store
def _epic_free_window(element: dict) -> Optional[dict]:
    """Возвращает окно акции, если игра раздаётся бесплатно прямо сейчас (скидка 100%)."""
    groups = ((element.get("promotions") or {}).get("promotionalOffers")) or []
    for group in groups:
        for offer in group.get("promotionalOffers") or []:
            setting = offer.get("discountSetting") or {}
            # В Epic «discountPercentage: 0» означает «платишь 0%», то есть бесплатно.
            if setting.get("discountPercentage") == 0:
                return offer
    return None


def _epic_slug(element: dict) -> str:
    for mapping in element.get("offerMappings") or []:
        slug = mapping.get("pageSlug")
        if slug:
            return slug
    for mapping in ((element.get("catalogNs") or {}).get("mappings")) or []:
        slug = mapping.get("pageSlug")
        if slug:
            return slug
    slug = element.get("productSlug") or element.get("urlSlug") or ""
    return slug.split("/")[0] if slug and not slug.startswith("[") else ""


def _epic_image(element: dict) -> Optional[str]:
    images = element.get("keyImages") or []
    for wanted in ("OfferImageWide", "DieselStoreFrontWide", "Thumbnail", "OfferImageTall", "DieselStoreFrontTall"):
        for img in images:
            if img.get("type") == wanted and img.get("url"):
                return img["url"]
    return next((img.get("url") for img in images if img.get("url")), None)


def _epic_price_text(element: dict) -> str:
    total = ((element.get("price") or {}).get("totalPrice")) or {}
    original = total.get("originalPrice")
    currency = total.get("currencyCode")
    if not original or not currency:
        return ""
    decimals = int(((total.get("currencyInfo") or {}).get("decimals")) or 2)
    cents = int(round(int(original) / (10 ** decimals) * 100))
    return formatting.format_price(cents, currency)


def fetch_epic() -> List[dict]:
    """Текущие бесплатные игры Epic. Бросает исключение, если витрина недоступна."""
    resp = net.get(
        EPIC_PROMO_URL,
        params={"locale": "ru", "country": "RU", "allowCountries": "RU"},
        timeout=20,
    )
    elements = (((resp.json().get("data") or {}).get("Catalog") or {}).get("searchStore") or {}).get("elements") or []
    found: List[dict] = []
    for el in elements:
        window = _epic_free_window(el)
        if not window:
            continue
        title = (el.get("title") or "").strip()
        total = ((el.get("price") or {}).get("totalPrice")) or {}
        if not title or title.lower().startswith("mystery game"):
            continue                                   # «загадочная игра» — пока нечего показывать
        if total.get("discountPrice") not in (0, None) or not total.get("originalPrice"):
            continue                                   # на деле не бесплатна или бесплатна всегда
        if any((c.get("path") == "addons") for c in el.get("categories") or []):
            continue                                   # дополнения нам не нужны — только игры
        found.append({"element": el, "window": window})
    return found


def _epic_candidate(cfg: Config, storage: Storage, item: dict) -> Optional[Candidate]:
    el, window = item["element"], item["window"]
    title = (el.get("title") or "").strip()
    start = window.get("startDate") or ""
    key = f"epic:{el.get('id') or _norm(title)}:{start[:10]}"
    if storage.is_known(key):
        return None
    slug = _epic_slug(el)
    url = EPIC_STORE_URL.format(slug) if slug else EPIC_FREE_PAGE
    until_ts = _iso_ts(window.get("endDate"))
    if until_ts and until_ts < time.time():
        return None

    def build() -> Post:
        description = formatting.shorten(formatting.clean_text(el.get("description") or ""), 230)
        text = _store_post_text(cfg, "Epic Games Store", title, description, _epic_price_text(el), until_ts, url)
        image = _epic_image(el)
        return Post(
            text=text,
            photos=[image] if image else [],
            link=url,
            button_text="🎁 Забрать бесплатно в Epic",
            button_url=url,
        )

    return Candidate(key=key, kind="free", title=title, score=8.4, build=build)


# ---------------------------------------------------------------- GamerPower (Steam и запасной вариант для Epic)
def fetch_gamerpower(platform: str) -> List[dict]:
    """Активные раздачи игр на платформе: steam или epic-games-store."""
    try:
        data = net.get(GAMERPOWER_API, params={"platform": platform, "type": "game"}, timeout=15).json()
    except Exception as exc:
        log.warning("GamerPower (%s) недоступен: %s", platform, exc)
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _is_active(item: dict) -> bool:
    status = (item.get("status") or "").lower()
    return not status or status == "active"


def _resolve_steam_appid(storage: Storage, item: dict, budget: List[int]) -> Optional[int]:
    """Идёт по ссылке раздачи и смотрит, куда она ведёт. Нужен именно магазин Steam (страница игры)."""
    gid = item.get("id")
    cache_key = f"gp_steam:{gid}"
    cached = storage.meta_get(cache_key)
    if cached is not None:
        return int(cached) if cached.isdigit() else None
    url = item.get("open_giveaway_url") or item.get("gamerpower_url")
    if not url or budget[0] <= 0:
        return None
    budget[0] -= 1
    try:
        resp = net.get(url, timeout=15, retries=2, stream=True)
        final = resp.url
        resp.close()
    except Exception as exc:
        log.debug("Ссылка раздачи %s не открылась: %s", gid, exc)
        return None                                    # временный сбой — попробуем в следующий раз
    match = _APP_RE.search(final or "")
    storage.meta_set(cache_key, match.group(1) if match else "no")
    if not match:
        log.info("Раздача «%s» ведёт не в Steam (%s) — пропускаю", item.get("title"), (final or "")[:60])
    return int(match.group(1)) if match else None


def _steam_giveaway_candidate(cfg: Config, storage: Storage, item: dict, appid: int) -> Optional[Candidate]:
    now = time.time()
    if storage.posted_with_prefix_since(f"steam:{appid}:free:", now - 30 * 86400):
        return None
    key = f"steam:{appid}:free:gp{item.get('id')}"
    if storage.is_known(key):
        return None
    title = clean_title(item.get("title") or "")
    until_ts = _iso_ts(item.get("end_date"))
    if until_ts and until_ts < now:
        return None
    ctx = {"category": "giveaway", "pct": 100, "expiry": int(until_ts) if until_ts else None, "name": title}
    return Candidate(
        key=key,
        kind="free",
        title=title,
        score=7.6,
        build=lambda a=appid, c=ctx: steam.build_post(cfg, a, "free", c),
    )


def _gamerpower_epic_candidate(cfg: Config, storage: Storage, item: dict) -> Optional[Candidate]:
    """Запасной вариант: Epic-раздача по данным GamerPower."""
    url = item.get("open_giveaway_url") or item.get("gamerpower_url") or EPIC_FREE_PAGE
    title = clean_title(item.get("title") or "")
    key = f"epic:gp{item.get('id')}"
    if storage.is_known(key):
        return None
    until_ts = _iso_ts(item.get("end_date"))
    if until_ts and until_ts < time.time():
        return None
    worth = (item.get("worth") or "").strip()
    price_text = worth if worth and worth.upper() != "N/A" else ""

    def build() -> Post:
        description = formatting.shorten(formatting.clean_text(item.get("description") or ""), 230)
        text = _store_post_text(cfg, "Epic Games Store", title, description, price_text, until_ts, url)
        image = item.get("image") or item.get("thumbnail")
        return Post(
            text=text,
            photos=[image] if image else [],
            link=url,
            button_text="🎁 Забрать бесплатно в Epic",
            button_url=url,
        )

    return Candidate(key=key, kind="free", title=title, score=8.0, build=build)


# ---------------------------------------------------------------- точка входа
def list_candidates(cfg: Config, storage: Storage) -> List[Candidate]:
    """Раздачи Steam и Epic, которые ещё не публиковались."""
    candidates: List[Candidate] = []
    seen_names = set()

    # --- Epic: сначала официальная витрина ---
    epic_ok = True
    try:
        for item in fetch_epic():
            cand = _epic_candidate(cfg, storage, item)
            if cand and _norm(cand.title) not in seen_names:
                seen_names.add(_norm(cand.title))
                candidates.append(cand)
    except Exception as exc:
        epic_ok = False
        log.warning("Витрина Epic недоступна (%s) — беру раздачи Epic из GamerPower", exc)

    if not epic_ok:
        for item in fetch_gamerpower("epic-games-store"):
            if not _is_active(item):
                continue
            cand = _gamerpower_epic_candidate(cfg, storage, item)
            if cand and _norm(cand.title) not in seen_names:
                seen_names.add(_norm(cand.title))
                candidates.append(cand)

    # --- Steam: через GamerPower, но только настоящие раздачи в магазине Steam ---
    budget = [MAX_RESOLVE_PER_RUN]
    for item in fetch_gamerpower("steam"):
        if not _is_active(item):
            continue
        if "steam" not in (item.get("platforms") or "").lower():
            continue
        appid = _resolve_steam_appid(storage, item, budget)
        if not appid:
            continue
        cand = _steam_giveaway_candidate(cfg, storage, item, appid)
        if cand and _norm(cand.title) not in seen_names:
            seen_names.add(_norm(cand.title))
            candidates.append(cand)

    log.info("Раздачи (Steam + Epic): найдено кандидатов — %d", len(candidates))
    return candidates
