"""Загрузка настроек из файла .env и переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class ConfigError(Exception):
    """Ошибка в настройках — сообщение написано для человека."""


def _get(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _bool(name: str, default: bool) -> bool:
    value = _get(name)
    if value == "":
        return default
    return value.lower() in ("1", "true", "yes", "y", "on", "да", "вкл")


def _int(name: str, default: int, minimum: Optional[int] = None) -> int:
    value = _get(name)
    if value == "":
        return default
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"Параметр {name} должен быть целым числом, а сейчас там: {value!r}") from None
    if minimum is not None and number < minimum:
        raise ConfigError(f"Параметр {name} не может быть меньше {minimum} (сейчас {number})")
    return number


def _float(name: str, default: float, minimum: Optional[float] = None) -> float:
    value = _get(name)
    if value == "":
        return default
    try:
        number = float(value.replace(",", "."))
    except ValueError:
        raise ConfigError(f"Параметр {name} должен быть числом, а сейчас там: {value!r}") from None
    if minimum is not None and number < minimum:
        raise ConfigError(f"Параметр {name} не может быть меньше {minimum} (сейчас {number})")
    return number


def _list(name: str, default: str) -> List[str]:
    value = _get(name, default)
    return [part.strip().lower() for part in value.split(",") if part.strip()]


_WEEKDAYS = {
    "пн": 0, "пон": 0, "mon": 0, "вт": 1, "tue": 1, "ср": 2, "wed": 2, "чт": 3, "thu": 3,
    "пт": 4, "пят": 4, "fri": 4, "сб": 5, "sat": 5, "вс": 6, "sun": 6,
}


def _weekday(name: str, default: int) -> int:
    value = _get(name).lower()
    if value == "":
        return default
    if value.isdigit() and 0 <= int(value) <= 6:
        return int(value)
    day = _WEEKDAYS.get(value[:3]) if value[:3] in _WEEKDAYS else _WEEKDAYS.get(value[:2])
    if day is None:
        raise ConfigError(f"{name} должен быть днём недели (пятница, fri или число 0-6), а сейчас: {value!r}")
    return day


def _quiet_hours(value: str) -> Optional[Tuple[int, int]]:
    if not value:
        return None
    try:
        start_s, end_s = value.replace(" ", "").split("-")
        start, end = int(start_s), int(end_s)
    except ValueError:
        raise ConfigError(f"QUIET_HOURS нужно писать так: 0-8 (часы), а сейчас: {value!r}") from None
    if not (0 <= start <= 23 and 0 <= end <= 24) or start == end:
        raise ConfigError("QUIET_HOURS: часы должны быть от 0 до 23, начало и конец не должны совпадать")
    return start, end


@dataclass(frozen=True)
class Config:
    # --- Telegram ---
    bot_token: str
    channel_id: str
    signature: str
    api_base: str

    # --- расписание ---
    timezone: str
    post_interval_minutes: int
    retry_minutes: int
    quiet_hours: Optional[Tuple[int, int]]
    max_posts_per_day: int

    # --- оформление ---
    use_emoji: bool
    show_source_link: bool
    show_steam_link: bool
    enable_hashtags: bool
    rubric_labels: bool

    # --- пятничный дайджест скидок ---
    digest_enabled: bool
    digest_weekday: int
    digest_hour: int
    digest_min_discount: int
    digest_min_reviews: int
    digest_max_items: int

    # --- рост канала ---
    subscribe_button: bool
    subscribe_button_text: str
    track_joins: bool

    # --- стиль постов ---
    post_style: str
    post_question: bool

    # --- горячие новости ---
    hot_news: bool
    hot_min_score: float
    hot_min_gap_minutes: int
    poll_seconds: int

    # --- источники ---
    enable_steam: bool
    enable_rss: bool
    feeds_file: Path
    rss_max_age_hours: int
    rss_exclude_keywords: List[str]

    # --- Steam ---
    steam_list_cc: str
    steam_lang: str
    steam_kinds: List[str]
    price_regions: List[str]
    min_discount: int
    release_max_age_days: int
    skip_adult: bool

    # --- медиа ---
    max_screenshots: int
    send_trailers: bool
    use_ffmpeg: bool
    max_video_seconds: int

    # --- необязательные ключи ---
    ai_base_url: str
    ai_api_key: str
    ai_model: str
    youtube_api_key: str

    # --- служебное ---
    db_path: Path
    log_dir: Path
    log_level: str


def load_config(require_telegram: bool = True) -> Config:
    token = _get("TELEGRAM_BOT_TOKEN")
    channel = _get("TELEGRAM_CHANNEL_ID")
    if require_telegram:
        if not token or token.startswith("123456:"):
            raise ConfigError("Не заполнен TELEGRAM_BOT_TOKEN в файле .env (токен выдаёт @BotFather).")
        if not channel:
            raise ConfigError("Не заполнен TELEGRAM_CHANNEL_ID в файле .env (например @my_channel).")

    raw_signature = os.getenv("CHANNEL_SIGNATURE")
    if raw_signature is None:
        signature = channel if channel.startswith("@") else ""
    else:
        signature = raw_signature.strip()

    timezone = _get("TIMEZONE", "Europe/Moscow")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(
            f"Неизвестный часовой пояс TIMEZONE={timezone!r}. Примеры: Europe/Moscow, Europe/Kyiv, Europe/Berlin."
        ) from None

    def path(name: str, default: str) -> Path:
        p = Path(_get(name, default))
        return p if p.is_absolute() else BASE_DIR / p

    post_style = _get("POST_STYLE", "prepod").lower()
    if post_style not in ("prepod", "stars", "platforma"):
        raise ConfigError(
            f"POST_STYLE={post_style!r} не поддерживается. Выберите prepod, stars или platforma."
        )

    default_db = "data/bot.db"
    # Если запущены в Amvera или Docker с примонтированным разделом /data
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        default_db = "/data/bot.db"

    return Config(
        bot_token=token,
        channel_id=channel,
        signature=signature,
        api_base=_get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
        timezone=timezone,
        post_interval_minutes=_int("POST_INTERVAL_MINUTES", 30, minimum=5),
        retry_minutes=_int("RETRY_MINUTES", 20, minimum=1),
        quiet_hours=_quiet_hours(_get("QUIET_HOURS", "0-8")),
        max_posts_per_day=_int("MAX_POSTS_PER_DAY", 24, minimum=1),
        use_emoji=_bool("USE_EMOJI", True),
        show_source_link=_bool("SHOW_SOURCE_LINK", True),
        show_steam_link=_bool("SHOW_STEAM_LINK", True),
        enable_hashtags=_bool("ENABLE_HASHTAGS", True),
        rubric_labels=_bool("RUBRIC_LABELS", True),
        digest_enabled=_bool("DIGEST_ENABLED", True),
        digest_weekday=_weekday("DIGEST_WEEKDAY", 4),
        digest_hour=_int("DIGEST_HOUR", 18, minimum=0),
        digest_min_discount=_int("DIGEST_MIN_DISCOUNT", 30, minimum=1),
        digest_min_reviews=_int("DIGEST_MIN_REVIEWS", 20000, minimum=0),
        digest_max_items=_int("DIGEST_MAX_ITEMS", 12, minimum=3),
        subscribe_button=_bool("SUBSCRIBE_BUTTON", True),
        subscribe_button_text=_get("SUBSCRIBE_BUTTON_TEXT", "📣 Подписаться на канал"),
        track_joins=_bool("TRACK_JOINS", True),
        post_style=post_style,
        post_question=_bool("POST_QUESTION", True),
        hot_news=_bool("HOT_NEWS", True),
        hot_min_score=_float("HOT_MIN_SCORE", 5.0, minimum=0.0),
        hot_min_gap_minutes=_int("HOT_MIN_GAP_MINUTES", 10, minimum=5),
        poll_seconds=_int("POLL_SECONDS", 300, minimum=60),
        enable_steam=_bool("ENABLE_STEAM", True),
        enable_rss=_bool("ENABLE_RSS", True),
        feeds_file=path("FEEDS_FILE", "feeds.txt"),
        rss_max_age_hours=_int("RSS_MAX_AGE_HOURS", 36, minimum=1),
        rss_exclude_keywords=_list(
            "RSS_EXCLUDE_KEYWORDS",
            "косплей,cosplay,сериал,фильм,кино,netflix,комикс,фигурка,видеокарта,процессор,железо",
        ),
        steam_list_cc=_get("STEAM_LIST_CC", "us").lower(),
        steam_lang=_get("STEAM_LANG", "russian").lower(),
        steam_kinds=_list("STEAM_KINDS", "release,free,discount,upcoming"),
        price_regions=_list("PRICE_REGIONS", "ru,kz,ua,de,us"),
        min_discount=_int("MIN_DISCOUNT", 60, minimum=1),
        release_max_age_days=_int("RELEASE_MAX_AGE_DAYS", 10, minimum=1),
        skip_adult=_bool("SKIP_ADULT", True),
        max_screenshots=_int("MAX_SCREENSHOTS", 2, minimum=0),
        send_trailers=_bool("SEND_TRAILERS", False),
        use_ffmpeg=_bool("USE_FFMPEG", False),
        max_video_seconds=_int("MAX_VIDEO_SECONDS", 150, minimum=10),
        ai_base_url=_get("AI_BASE_URL"),
        ai_api_key=_get("AI_API_KEY"),
        ai_model=_get("AI_MODEL"),
        youtube_api_key=_get("YOUTUBE_API_KEY"),
        db_path=Path(default_db) if default_db.startswith("/") else path("DB_PATH", default_db),
        log_dir=path("LOG_DIR", "logs"),
        log_level=_get("LOG_LEVEL", "INFO").upper(),
    )
