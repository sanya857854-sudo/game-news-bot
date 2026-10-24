"""Источник: публичные эндпоинты магазина Steam (ключ не нужен).

Из них бот берёт релизы, скорые анонсы, раздачи и скидки, а также описание на русском,
цены в нескольких валютах, скриншоты и трейлеры — то есть всё для поста «как в образце».
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from . import ai, formatting, net
from .config import Config
from .models import Candidate, Post
from .storage import Storage

log = logging.getLogger(__name__)

FEATURED_URL = "https://store.steampowered.com/api/featuredcategories"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"
STORE_APP_URL = "https://store.steampowered.com/app/{}"

REQUEST_PAUSE = 0.5  # пауза между запросами к Steam, чтобы не злить лимиты

_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# ---------------------------------------------------------------- даты
def parse_steam_date(text: str) -> Optional[date]:
    """Понимает «15 окт. 2026 г.», «15 Oct, 2026», «Oct 15, 2026». Иначе None."""
    if not text:
        return None
    text = text.strip().lower()
    m = re.search(r"(\d{1,2})\s+([a-zа-яё]+)\.?,?\s+(\d{4})", text)
    if m:
        day, month_word, year = int(m.group(1)), m.group(2), int(m.group(3))
    else:
        m = re.search(r"([a-zа-яё]+)\.?\s+(\d{1,2}),?\s+(\d{4})", text)
        if not m:
            return None
        month_word, day, year = m.group(1), int(m.group(2)), int(m.group(3))
    month = _MONTHS.get(month_word[:3])
    if not month:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ---------------------------------------------------------------- запросы
def fetch_details(appid: int, cfg: Config) -> Optional[dict]:
    resp = net.get(DETAILS_URL, params={"appids": appid, "cc": cfg.steam_list_cc, "l": cfg.steam_lang})
    time.sleep(REQUEST_PAUSE)
    payload = resp.json().get(str(appid)) or {}
    data = payload.get("data")
    if not payload.get("success") or not isinstance(data, dict):
        return None
    return data


def fetch_player_count(appid: int) -> Optional[int]:
    """Получает текущее число игроков онлайн в Steam."""
    try:
        url = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
        resp = net.get(url, params={"appid": appid}, timeout=10, retries=1)
        data = resp.json().get("response") or {}
        cnt = data.get("player_count")
        return int(cnt) if cnt is not None else None
    except Exception:
        return None


def fetch_prices(appid: int, regions: List[str], field: str = "final") -> List[str]:
    """Цены по регионам: ['1300₽', '8360₸', ...]. field='initial' — цена без скидки."""
    prices: List[str] = []
    for region in regions:
        try:
            resp = net.get(
                DETAILS_URL,
                params={"appids": appid, "cc": region, "filters": "price_overview"},
                retries=2,
            )
            data = (resp.json().get(str(appid)) or {}).get("data")
            overview = data.get("price_overview") if isinstance(data, dict) else None
            if overview and overview.get(field) is not None and overview.get("currency"):
                prices.append(formatting.format_price(int(overview[field]), overview["currency"]))
        except Exception as exc:  # одна страна не ответила — не страшно
            log.debug("Цена для региона %s (app %s) не получена: %s", region, appid, exc)
        time.sleep(0.3)
    return prices


# ---------------------------------------------------------------- кандидаты
def list_candidates(cfg: Config, storage: Storage) -> List[Candidate]:
    """Быстро собирает список возможных постов по витрине Steam (без тяжёлых запросов)."""
    data = net.get(FEATURED_URL, params={"cc": cfg.steam_list_cc, "l": cfg.steam_lang}).json()
    kinds = set(cfg.steam_kinds)
    candidates: List[Candidate] = []
    used_keys = set()
    used_pairs = set()

    def add(key: str, kind: str, name: str, score: float, appid: int, ctx: dict) -> None:
        if key in used_keys or storage.is_known(key):
            return
        if kind in ("free", "discount"):
            # Одна и та же акция может мелькнуть в разных списках с разными данными —
            # поэтому не повторяем акцию на ту же игру в течение 30 дней.
            if (appid, kind) in used_pairs:
                return
            if storage.posted_with_prefix_since(f"steam:{appid}:{kind}:", time.time() - 30 * 86400):
                return
            used_pairs.add((appid, kind))
        used_keys.add(key)
        candidates.append(
            Candidate(
                key=key,
                kind=kind,
                title=name,
                score=score,
                build=lambda a=appid, k=kind, c=ctx: build_post(cfg, a, k, c),
            )
        )

    month_tag = datetime.now().strftime("%Y%m")
    for category in ("new_releases", "top_sellers", "specials", "coming_soon"):
        items = (data.get(category) or {}).get("items") or []
        for rank, item in enumerate(items):
            appid, name = item.get("id"), item.get("name")
            if not appid or not name:
                continue
            pct = int(item.get("discount_percent") or 0)
            discounted = bool(item.get("discounted")) and pct > 0
            expiry = item.get("discount_expiration")
            period = str(expiry) if expiry else month_tag
            ctx = {"category": category, "pct": pct, "expiry": expiry, "name": name}
            rank_penalty = rank * 0.05

            if discounted and (pct >= 100 or item.get("final_price") == 0):
                if "free" in kinds:
                    add(f"steam:{appid}:free:{period}", "free", name, 6.0 - rank_penalty, appid, ctx)
            elif discounted and pct >= cfg.min_discount and "discount" in kinds:
                add(
                    f"steam:{appid}:discount:{pct}:{period}",
                    "discount", name, 2.0 + pct / 50 - rank_penalty, appid, ctx,
                )

            if category in ("new_releases", "top_sellers") and "release" in kinds:
                bonus = 0.8 if category == "top_sellers" else 0.0
                add(f"steam:{appid}:release", "release", name, 4.0 + bonus - rank_penalty, appid, ctx)

            if category == "coming_soon" and "upcoming" in kinds:
                add(f"steam:{appid}:upcoming", "upcoming", name, 1.8 - rank_penalty, appid, ctx)

    log.info("Steam: найдено кандидатов — %d", len(candidates))
    return candidates


# ---------------------------------------------------------------- сборка поста
def _is_adult(details: dict) -> bool:
    ids = {str(i) for i in (details.get("content_descriptors") or {}).get("ids") or []}
    if ids & {"3", "4"}:  # «только для взрослых» / «частая нагота и сексуальный контент»
        return True
    genre_ids = {str(g.get("id")) for g in details.get("genres") or []}
    return bool(genre_ids & {"71", "72"})  # Sexual Content / Nudity


def _describe(cfg: Config, details: dict, name: str) -> str:
    raw = formatting.clean_text(details.get("short_description") or "")
    if raw and formatting.is_russian(raw):
        return formatting.shorten(raw, 230)
    if raw and ai.enabled(cfg):
        translated = ai.translate_description(cfg, name, raw)
        if translated:
            return formatting.shorten(translated, 230)
    # Запасной вариант без перевода: коротко и по-русски из данных Steam.
    genres = [g.get("description") for g in details.get("genres") or [] if g.get("description")][:3]
    devs = details.get("developers") or []
    parts = []
    if genres:
        parts.append("Жанр: " + ", ".join(genres) + ".")
    if devs:
        parts.append(f"Разработчик: {devs[0]}.")
    return " ".join(parts)


def _movie_sources(movie: dict) -> Tuple[Optional[str], Optional[str]]:
    """Возвращает (прямая ссылка на mp4, ссылка на HLS-плейлист)."""
    direct = None
    mp4 = movie.get("mp4")
    if isinstance(mp4, dict):
        direct = mp4.get("480") or mp4.get("max")
    elif isinstance(mp4, str):
        direct = mp4
    hls = movie.get("hls_h264")
    if isinstance(hls, dict):
        hls = hls.get("url") or hls.get("max")

    def secure(url: Optional[str]) -> Optional[str]:
        # старые ссылки Steam бывают с http:// — для надёжности используем https://
        if isinstance(url, str) and url.startswith("http://") and ("steamstatic" in url or "akamai" in url):
            return "https://" + url[len("http://"):]
        return url if isinstance(url, str) else None

    return secure(direct), secure(hls)


def _pick_trailer(movies: List[dict]) -> Tuple[Optional[str], Optional[str]]:
    ordered = sorted(movies, key=lambda m: 0 if m.get("highlight") else 1)
    for movie in ordered:
        direct, hls = _movie_sources(movie)
        if direct or hls:
            return direct, hls
    return None, None


def build_post(cfg: Config, appid: int, kind: str, ctx: dict) -> Optional[Post]:
    """Собирает готовый пост. None — материал не подходит (его пропустят навсегда)."""
    details = fetch_details(appid, cfg)
    if not details:
        log.info("Steam %s: нет данных о приложении", appid)
        return None
    if details.get("type") != "game":
        return None
    if cfg.skip_adult and _is_adult(details):
        log.info("Steam %s: пропущено (контент 18+)", appid)
        return None

    name = details.get("name") or ctx.get("name") or str(appid)
    release = details.get("release_date") or {}
    coming_soon = bool(release.get("coming_soon"))
    release_date = parse_steam_date(release.get("date", ""))
    today = date.today()
    free_to_play = bool(details.get("is_free"))

    if kind == "release":
        if coming_soon:
            return None
        if release_date is None:
            if ctx.get("category") != "new_releases":
                return None
        elif (today - release_date).days > cfg.release_max_age_days or release_date > today + timedelta(days=1):
            log.info("Steam %s: релиз %s не свежий — пропуск", appid, release_date)
            return None
    elif kind == "upcoming":
        if not coming_soon:
            return None
    else:  # free / discount
        if coming_soon or free_to_play:
            return None

    description = _describe(cfg, details, name)

    prices: List[str] = []
    if not free_to_play:
        prices = fetch_prices(appid, cfg.price_regions, "initial" if kind == "free" else "final")
        if kind == "discount" and not prices:
            return None

    screenshots = [
        s.get("path_full") or s.get("path_thumbnail")
        for s in (details.get("screenshots") or [])
    ]
    photos = [u for u in screenshots if u][: cfg.max_screenshots]
    if not photos and details.get("header_image"):
        photos = [details["header_image"]]

    video_url, hls_url = _pick_trailer(details.get("movies") or []) if cfg.send_trailers else (None, None)

    # Оценки, рекомендации и онлайн
    meta = details.get("metacritic") or {}
    metacritic_score = meta.get("score") if isinstance(meta, dict) else None
    recs = details.get("recommendations") or {}
    recommendations_count = recs.get("total") if isinstance(recs, dict) else None
    player_count = fetch_player_count(appid) if kind in ("release", "discount") else None

    # Интерактивный опрос для релизов и скидок
    poll_question = None
    poll_options = []
    if kind == "release":
        poll_question = f"Будете играть в {name}?"
        poll_options = ["🔥 Уже играю / куплю", "👀 Подожду скидок / отзывов", "❌ Мимо / не моё"]
    elif kind == "discount" and ctx.get("pct", 0) >= 50:
        poll_question = f"Стоит ли брать {name} со скидкой {ctx.get('pct')}%?"
        poll_options = ["👍 Однозначно брать!", "🤔 На любителя", "👎 Не стоит брать"]

    store_url = STORE_APP_URL.format(appid)
    text = formatting.steam_post_text(
        cfg,
        kind,
        name,
        description,
        prices,
        release_date=release_date if (kind == "upcoming" and release_date and release_date >= today) else None,
        until_ts=ctx.get("expiry"),
        discount=ctx.get("pct", 0),
        free_to_play=free_to_play,
        store_url=store_url,
        metacritic_score=metacritic_score,
        recommendations=recommendations_count,
        player_count=player_count,
    )
    return Post(
        text=text,
        photos=photos,
        video_url=video_url,
        hls_url=hls_url,
        trailer_query=name,
        link=store_url,
        button_text="🎮 Открыть страницу в Steam",
        button_url=store_url,
        poll_question=poll_question,
        poll_options=poll_options,
    )
