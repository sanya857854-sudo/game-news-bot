"""Оформление постов: заголовок, короткий текст, цены, подпись — как в образце."""
from __future__ import annotations

import html
import random
import re
from datetime import date, datetime
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

from .config import Config
from .models import TRAILER_SLOT

CAPTION_LIMIT = 1024        # лимит Telegram на подпись к фото/видео
TRAILER_RESERVE = 130       # запас под строку «Смотреть трейлер»

MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

CURRENCY_SYMBOLS = {
    "RUB": "₽", "KZT": "₸", "UAH": "₴", "EUR": "€", "USD": "$", "GBP": "£",
    "PLN": "zł", "TRY": "₺", "BRL": "R$", "INR": "₹", "CNY": "¥", "JPY": "¥",
    "KRW": "₩", "CAD": "CA$", "AUD": "A$", "CHF": "CHF",
}


# ---------------------------------------------------------------- текст
def esc(text: str) -> str:
    """Экранирование для HTML-режима Telegram."""
    return html.escape(text or "", quote=False)


def esc_attr(text: str) -> str:
    return html.escape(text or "", quote=True)


def clean_text(raw: str) -> str:
    """Убирает HTML-теги и лишние пробелы."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_russian(text: str) -> bool:
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text or "")
    if not letters:
        return False
    cyr = sum(1 for ch in letters if "А" <= ch <= "я" or ch in "Ёё")
    return cyr / len(letters) >= 0.5


def shorten(text: str, limit: int, max_sentences: int = 2) -> str:
    """Оставляет 1–2 первых предложения, не длиннее limit символов."""
    text = (text or "").strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    out = ""
    for sentence in sentences[:max_sentences]:
        candidate = f"{out} {sentence}".strip()
        if len(candidate) <= limit:
            out = candidate
        else:
            break
    if out:
        return out
    if limit < 20:
        return ""
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:—- ")
    return cut + "…"


def ru_date(value: date, today: Optional[date] = None) -> str:
    today = today or date.today()
    text = f"{value.day} {MONTHS_GEN[value.month - 1]}"
    if value.year != today.year:
        text += f" {value.year}"
    return text


def ru_date_from_ts(timestamp: float, tz: str) -> str:
    moment = datetime.fromtimestamp(timestamp, ZoneInfo(tz))
    return ru_date(moment.date(), datetime.now(ZoneInfo(tz)).date())


def format_price(cents: int, currency: str) -> str:
    """1299 копеек -> «12,99$»; целые суммы — без копеек: «1300₽»."""
    amount = cents / 100
    if abs(amount - round(amount)) < 0.005:
        number = str(int(round(amount)))
    else:
        number = f"{amount:.2f}".replace(".", ",")
    symbol = CURRENCY_SYMBOLS.get(currency.upper())
    return f"{number}{symbol}" if symbol else f"{number} {currency.upper()}"


# ---------------------------------------------------------------- подпись и эмодзи
def _style(cfg: Config) -> str:
    """Стиль постов: prepod (как «ПРЕПОД STEAM»), stars (как «PlayStation Stars»),
    platforma (как «Платформа»)."""
    return getattr(cfg, "post_style", "prepod") or "prepod"


def _signature_link(sig: str) -> str:
    if sig.startswith("@") and re.fullmatch(r"@[A-Za-z0-9_]{4,}", sig):
        return f'<a href="https://t.me/{sig[1:]}">{sig}</a>'
    return esc(sig)


def signature_html(cfg: Config) -> str:
    """Подпись под постом — оформляется под выбранный стиль канала."""
    sig = cfg.signature.strip()
    if not sig:
        return ""
    inner = _signature_link(sig)
    style = _style(cfg)
    if style == "stars":
        return f"🌟 {inner} ✨"
    if style == "platforma":
        return f"{inner}🤩" if cfg.use_emoji else inner
    return inner


def line_prefix(cfg: Config) -> str:
    """Начало строки-пункта: у «PlayStation Stars» — «•», у «Платформы» — 🤩, у «ПРЕПОД STEAM» — пусто."""
    style = _style(cfg)
    if style == "stars":
        return "• "
    if style == "platforma":
        return _prefix(cfg, "🤩")
    return ""


_KEYWORD_EMOJI = [
    (r"студи|разработ|индустри|valve|rockstar|sony|xbox|blizzard|ubisoft|cd\s*projekt", "🏢"),
    (r"трейлер|тизер|trailer|teaser", "🎬"),
    (r"патч|обновлен|апдейт|update|patch|hotfix", "🛠"),
    (r"раздач|бесплатн|free", "🎁"),
    (r"скидк|распродаж|sale|discount", "💸"),
    (r"анонс|announce|представил", "📢"),
    (r"dlc|дополнени|expansion", "🧩"),
    (r"релиз|вышл|вышла|вышел|выход|release|launch", "🎮"),
    (r"playstation|ps5|xbox|nintendo|switch", "🕹"),
]
_KIND_EMOJI = {"release": "🎮", "upcoming": "🗓", "free": "🎁", "discount": "🔥", "news": "📰"}

_STUDIO_PATTERNS = [
    r"\b(valve|rockstar|cd\s*projekt|cdpr|naughty\s*dog|insomniac|bethesda|activision|blizzard|ubisoft|bioware|respawn|dice|capcom|fromsoftware|square\s*enix|epic\s*games|larian|remedy|bungie|kojima|sega|atlus|konami|gsc|mundfish|take-two|saber|krafton|supercell|warner\s*bros|electronic\s*arts)\b",
    r"(гейб\s*ньюэлл|кодзима|рокстар|близзард|юбисофт|капком|лариан|сони|майкрософт|эпик\s*геймс|миязаки|миядзаки|бетесда|наути\s*дог)",
    r"\b(playstation|xbox|nintendo|sony|microsoft)\b",
    r"(игров(ая|ой|ую|ые|ых)\s*студи|разработчик|увольнен|поглощен|закрыти[ея]\s*студи|отчет\s*компани|приобретен|финансов(ый|ые)\s*отчет)",
]


def pick_emoji(cfg: Config, kind: str, title: str = "") -> str:
    """Один тематический эмодзи для заголовка (или пусто, если эмодзи выключены)."""
    if not cfg.use_emoji:
        return ""
    if kind == "news":
        for pattern, emoji in _KEYWORD_EMOJI:
            if re.search(pattern, title, flags=re.I):
                return emoji
    return _KIND_EMOJI.get(kind, "📰")


def _prefix(cfg: Config, emoji: str) -> str:
    return f"{emoji} " if (cfg.use_emoji and emoji) else ""


def title_emoji(cfg: Config, kind: str, title: str = "") -> str:
    """Эмодзи перед заголовком: только у стиля «Платформа» (остальные — строгие)."""
    if _style(cfg) != "platforma":
        return ""
    return pick_emoji(cfg, kind, title)


_ENGAGEMENT_QUESTIONS = {
    "release": ["Будете играть?", "Ждали релиз?", "Попробуете новинку?", "Как вам игра?"],
    "upcoming": ["Ждёте игру?", "Добавили в вишлист?", "Как вам анонс?", "Будете ждать релиз?"],
    "free": ["Уже забрали в библиотеку?", "Успели забрать?", "Будете играть или на потом?"],
    "discount": ["Берём по скидке?", "Как вам предложение?", "Стоит брать?"],
    "news": ["Что думаете об этом?", "Как вам такие новости?", "Делитесь мнением в комментариях!"]
}

_THEME_HASHTAGS = (
    (r"gta|grand theft auto", "#gta"),
    (r"half-life|half life|valve", "#valve"),
    (r"witcher|ведьмак", "#witcher"),
    (r"cyberpunk|киберпанк", "#cyberpunk"),
    (r"stalker|сталкер", "#stalker"),
    (r"playstation|ps5|ps4|sony", "#playstation"),
    (r"xbox|microsoft", "#xbox"),
    (r"nintendo|switch", "#nintendo"),
    (r"elden ring|fromsoftware|dark souls|bloodborne", "#souls"),
    (r"resident evil", "#residentevil"),
    (r"silent hill", "#silenthill"),
    (r"fallout", "#fallout"),
    (r"doom", "#doom"),
    (r"space marine|warhammer", "#warhammer"),
    (r"valve|рокстар|rockstar|cd projekt|cdpr|blizzard|ubisoft|bethesda|bioware|larian|remedy|capcom", "#студии"),
    (r"индустри|разработчик|продаж|отчет|увольнен|поглощен", "#индустрия"),
)

_KIND_HASHTAGS = {
    "release": ["#релиз", "#игры", "#steam"],
    "upcoming": ["#анонс", "#скоро", "#steam"],
    "free": ["#раздача", "#халява", "#steam"],
    "discount": ["#скидки", "#распродажа", "#steam"],
    "news": ["#новости", "#игры"],
}


def generate_hashtags(cfg: Config, kind: str, text_for_themes: str = "") -> str:
    """Генерирует рубрикаторные хэштеги для навигации по каналу."""
    if not getattr(cfg, "enable_hashtags", True):
        return ""
    tags = list(_KIND_HASHTAGS.get(kind, ["#игры"]))
    low = text_for_themes.lower()
    for pattern, tag in _THEME_HASHTAGS:
        if tag not in tags and re.search(pattern, low):
            tags.append(tag)
            if len(tags) >= 5:
                break
    return " ".join(tags)


def engagement_html(cfg: Config, kind: str) -> str:
    """Вопрос читателям в конце поста — для вовлечения, реакций и комментариев."""
    if not cfg.post_question:
        return ""
    # В стиле prepod оставляем только лаконичные вопросы
    options = _ENGAGEMENT_QUESTIONS.get(kind, ["Что думаете?"])
    return esc(random.choice(options))


def _format_body(cfg: Config, body: str) -> str:
    """Оформляет текст под стиль: у «Stars» и «Платформы» факты идут пунктами."""
    body = (body or "").strip()
    if not body:
        return ""
    style = _style(cfg)
    if style in ("stars", "platforma"):
        sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", body) if s.strip()]
        if len(sentences) >= 2:
            marker = "• " if style == "stars" else _prefix(cfg, "🤩")
            return "\n".join(marker + esc(s) for s in sentences)
    return esc(body)


# ---------------------------------------------------------------- сборка
def _assemble(
    cfg: Config,
    title_line: str,
    body: str,
    middle: Iterable[str],
    tail: Iterable[str],
    with_slot: bool,
    hashtags: str = "",
) -> str:
    """Склеивает абзацы и следит, чтобы подпись влезла в лимит Telegram."""
    middle = [m for m in middle if m]
    tail = [t for t in tail if t]
    signature = signature_html(cfg)
    fixed_parts = [title_line, *middle, *tail]
    if hashtags:
        fixed_parts.append(hashtags)
    if signature:
        fixed_parts.append(signature)
    fixed_len = len("\n\n".join(fixed_parts)) + 2
    budget = CAPTION_LIMIT - fixed_len - (TRAILER_RESERVE if with_slot else 0)

    body_html = ""
    if body and budget >= 50:
        raw = body if len(body) <= budget else shorten(body, budget)
        body_html = _format_body(cfg, raw)
        if len(body_html) > budget:          # пункты списка занимают чуть больше места
            body_html = _format_body(cfg, shorten(body, max(50, budget - 6)))

    paragraphs = [title_line]
    if body_html:
        paragraphs.append(body_html)
    paragraphs.extend(middle)
    paragraphs.extend(tail)
    if with_slot:
        paragraphs.append(TRAILER_SLOT)
    if hashtags:
        paragraphs.append(hashtags)
    if signature:
        paragraphs.append(signature)
    return "\n\n".join(paragraphs)


def steam_post_text(
    cfg: Config,
    kind: str,
    name: str,
    description: str,
    prices: List[str],
    *,
    release_date: Optional[date] = None,
    until_ts: Optional[int] = None,
    discount: int = 0,
    free_to_play: bool = False,
    store_url: Optional[str] = None,
    metacritic_score: Optional[int] = None,
    recommendations: Optional[int] = None,
    player_count: Optional[int] = None,
) -> str:
    """Пост про игру из Steam. Структура как в образце:
    жирный заголовок → короткое описание → строка с ценами → оценки/онлайн → подпись канала."""
    n = esc(name)
    slash_prices = "/".join(prices)
    middle: List[str] = []

    if kind == "release":
        title = f"{n} вышла в Steam"
        if free_to_play:
            middle.append(f"{line_prefix(cfg)}Играть можно бесплатно.")
        elif prices:
            middle.append(f"{line_prefix(cfg)}Купить игру можно за {esc(slash_prices)}.")
    elif kind == "upcoming":
        if release_date:
            title = f"{n} выйдет в Steam {ru_date(release_date)}"
        else:
            title = f"{n} скоро выйдет в Steam"
        if free_to_play:
            middle.append(f"{line_prefix(cfg)}Игра будет бесплатной.")
        elif prices:
            middle.append(f"{line_prefix(cfg)}Цена в Steam: {esc(slash_prices)}.")
    elif kind == "free":
        title = f"В Steam началась раздача {n}"
        if until_ts:
            middle.append(
                f"{line_prefix(cfg)}Забрать игру бесплатно можно до {ru_date_from_ts(until_ts, cfg.timezone)}."
            )
        else:
            middle.append(f"{line_prefix(cfg)}Забрать игру можно бесплатно, пока идёт акция.")
        if prices:
            middle.append(f"{line_prefix(cfg)}Обычная цена — {esc(slash_prices)}.")
    else:  # discount
        title = f"Скидка {discount}% на {n} в Steam"
        if prices:
            middle.append(f"{line_prefix(cfg)}Сейчас игра стоит {esc(slash_prices)}.")

    # Дополнительная полезная статистика для игроков
    if metacritic_score and metacritic_score > 0:
        middle.append(f"{line_prefix(cfg)}🏆 Оценка Metacritic: <b>{metacritic_score}/100</b>")
    if recommendations and recommendations >= 500:
        rec_str = f"{recommendations:,}".replace(",", " ")
        middle.append(f"{line_prefix(cfg)}👍 Рекомендаций игроков: <b>{rec_str}</b>")
    if player_count and player_count >= 1000:
        cnt_str = f"{player_count:,}".replace(",", " ")
        middle.append(f"{line_prefix(cfg)}👥 Онлайн прямо сейчас: <b>{cnt_str}</b> игроков")

    title_line = f"{_prefix(cfg, title_emoji(cfg, kind))}<b>{title}</b>"
    tail = []
    if cfg.show_steam_link and store_url:
        tail.append(f'{line_prefix(cfg)}<a href="{esc_attr(store_url)}">Страница в Steam</a>')
    question = engagement_html(cfg, kind)
    if question:
        tail.append(question)
    hashtags = generate_hashtags(cfg, kind, name)
    return _assemble(cfg, title_line, description, middle, tail, with_slot=True, hashtags=hashtags)


def news_post_text(
    cfg: Config,
    title: str,
    summary: str,
    source_name: str,
    link: str,
    with_slot: bool = True,
) -> str:
    full_blob = f"{title} {summary}"
    is_studio = any(re.search(pat, full_blob, re.I) for pat in _STUDIO_PATTERNS)
    badge = "🏢 <b>Студии & Индустрия</b>\n" if is_studio else ""
    title_line = f"{badge}{_prefix(cfg, title_emoji(cfg, 'news', title))}<b>{esc(title)}</b>"
    tail = []
    if cfg.show_source_link and link:
        tail.append(f'{line_prefix(cfg)}<a href="{esc_attr(link)}">Источник: {esc(source_name)}</a>')
    question = engagement_html(cfg, "news")
    if question:
        tail.append(question)
    hashtags = generate_hashtags(cfg, "news", full_blob)
    return _assemble(cfg, title_line, summary, [], tail, with_slot=with_slot, hashtags=hashtags)


def fill_trailer_slot(text: str, trailer_line: str) -> str:
    """Подставляет строку про трейлер или аккуратно убирает пустое место."""
    if trailer_line:
        return text.replace(TRAILER_SLOT, trailer_line)
    return text.replace("\n\n" + TRAILER_SLOT, "").replace(TRAILER_SLOT, "")


def trailer_line_html(cfg: Config, url: str) -> str:
    return f'{line_prefix(cfg)}<a href="{esc_attr(url)}">Смотреть трейлер</a>'


# ---------------------------------------------------------------- похожие заголовки
def _tokens(title: str) -> set:
    return {w for w in re.findall(r"\w+", title.lower()) if len(w) >= 3}


def is_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return a.strip().lower() == b.strip().lower()
    return len(ta & tb) / len(ta | tb) >= threshold


def is_similar_to_any(title: str, others: Iterable[str], threshold: float = 0.6) -> bool:
    return any(is_similar(title, other, threshold=threshold) for other in others)
