"""Оценка «интересности» материала.

Идея простая: у каждой темы есть вес. Если в заголовке встретилось что-то
крупное (GTA, Valve, раздача, отмена, скандал), материал получает бонус —
и бот может опубликовать его сразу, не дожидаясь планового поста.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import Candidate

# Пары «подстрока в заголовке (в нижнем регистре)» -> вес.
# Порядок не важен, веса суммируются и ограничиваются сверху.
_WEIGHTS = (
    # --- очень горячие темы ---
    ("gta 6", 1.8),
    ("gta", 1.6),
    ("half-life", 1.5),
    ("half life", 1.5),
    ("stalker 2", 1.5),
    ("сталкер 2", 1.5),
    ("stalker", 1.2),
    ("сталкер", 1.2),
    ("silksong", 1.4),
    ("hollow knight", 1.2),
    ("witcher 4", 1.3),
    ("ведьмак 4", 1.3),
    ("witcher", 1.1),
    ("ведьмак", 1.1),
    ("elden ring", 1.2),
    ("bloodborne", 1.2),
    ("resident evil", 1.2),
    ("silent hill", 1.2),
    ("space marine", 1.1),
    ("kingdom come", 1.1),
    ("death stranding", 1.1),
    ("unreal engine", 1.0),
    ("switch 2", 1.3),
    ("red dead", 1.2),
    ("last of us", 1.0),
    ("god of war", 1.0),
    ("mass effect", 1.0),
    ("fallout", 0.9),
    ("doom", 0.9),
    # --- крупные студии и платформы ---
    ("rockstar", 1.0),
    ("valve", 0.9),
    ("fromsoftware", 0.9),
    ("cd projekt", 0.9),
    ("cyberpunk", 0.8),
    ("nintendo", 0.9),
    ("playstation", 0.8),
    ("xbox", 0.7),
    ("bethesda", 0.8),
    ("blizzard", 0.7),
    ("ubisoft", 0.6),
    ("electronic arts", 0.6),
    ("steam deck", 0.9),
    ("nvidia", 0.6),
    ("sony", 0.6),
    ("microsoft", 0.6),
    # --- события и форматы ---
    ("раздач", 1.0),
    ("бесплатн", 0.9),
    ("free", 0.8),
    ("скидк", 0.7),
    ("релиз", 0.7),
    ("анонс", 0.8),
    ("утечк", 0.9),
    ("слух", 0.5),
    ("отмен", 0.9),
    ("закры", 0.8),
    ("суд", 0.7),
    ("скандал", 0.8),
    ("рекорд", 0.8),
    ("трейлер", 0.7),
    ("геймплей", 0.8),
    ("обновлен", 0.6),
    ("dlc", 0.6),
    ("патч", 0.5),
    ("ремастер", 0.6),
    ("ремейк", 0.6),
    ("киберспорт", 0.6),
    ("турнир", 0.5),
    ("мод", 0.4),
    ("steam", 0.3),
)

# ---------------------------------------------------------------- рубрики важных новостей
# Это «инфоповоды», которые канал хочет публиковать сразу: новый трейлер, выход игры,
# новости студий и индустрии. По заголовку бот понимает, к какой рубрике относится новость.
_TRAILER_RE = re.compile(
    r"трейлер|тизер|геймплейн\w*\s+(?:ролик|показ|видео|трейлер)|игровой\s+процесс\s+показ|"
    r"\btrailer\b|\bteaser\b|gameplay\s+(?:reveal|trailer)",
    re.I,
)
_RELEASE_RE = re.compile(
    r"\b(?:вышл[аио]|вышел)\b(?!\s+(?:патч|обновлен|апдейт|трейлер|тизер|ролик|геймплей|дополнени|dlc|мод|новый\s+патч|"
    r"новый\s+трейлер|первый\s+трейлер))|"
    r"состоялся\s+релиз|релиз(?:нулась|нулся|овалась|овался)|уже\s+в\s+продаже|"
    r"доступн[аы]\s+(?:в|на)\s+(?:steam|ps5|ps4|xbox|switch|pc|пк)|стартовал[аи]?\s+(?:релиз|продаж)",
    re.I,
)
_STUDIO_COMPANY_RE = re.compile(
    r"\b(?:valve|rockstar|cd\s*projekt|cdpr|naughty\s*dog|insomniac|bethesda|activision|blizzard|ubisoft|bioware|"
    r"respawn|dice|capcom|fromsoftware|square\s*enix|epic\s*games|larian|remedy|bungie|sega|atlus|konami|gsc|"
    r"take-two|krafton|supercell|warner\s*bros|electronic\s*arts|ea|playstation|xbox|nintendo|sony|microsoft|"
    r"embracer|tencent|netease|bandai\s*namco|paradox|sweet\s*baby|obsidian|id\s*software)\b|"
    r"гейб\s*ньюэлл|кодзима|рокстар|близзард|юбисофт|капком|лариан|сони|майкрософт|миядзаки|миязаки|бетесда|"
    r"игров\w+\s+студи|разработчик\w*|издател\w+",
    re.I,
)
_STUDIO_EVENT_RE = re.compile(
    r"закры\w+|увол\w+|сокращ\w+|поглощ\w+|приобре\w+|купил[аи]?\b|покупк|слиян|банкрот|забастов|выручк|"
    r"финансов\w+\s+отч[её]т|отч[её]т\w*\s+за|инвестиц|основал|основател|покинул|ушл[аи]?\b|ушёл|уход\b|"
    r"судебн|\bиск\b|лицензи|реорганиз|перезапуск\s+студи|открыл\w*\s+студи|нов(?:ая|ую)\s+студи",
    re.I,
)

# Метки рубрик (их печатает formatting над заголовком поста).
RUBRIC_LABELS = {
    "trailer": "🎬 НОВЫЙ ТРЕЙЛЕР",
    "release": "🚀 РЕЛИЗ",
    "studio": "🏢 СТУДИИ И ИНДУСТРИЯ",
}

# Насколько рубрика поднимает «интересность» новости (чтобы такие новости шли вне очереди).
RUBRIC_BONUS = {"trailer": 0.8, "release": 0.9, "studio": 0.8}


def rubric(title: str, text: str = "") -> Optional[str]:
    """Определяет рубрику новости по заголовку (и тексту): trailer / release / studio / None."""
    head = title or ""
    blob = f"{head} {text or ''}"
    if _TRAILER_RE.search(head):
        return "trailer"
    if _RELEASE_RE.search(head):
        return "release"
    if _STUDIO_COMPANY_RE.search(blob) and _STUDIO_EVENT_RE.search(head):
        return "studio"
    return None


# Бонус не может быть больше этого значения, иначе один заголовок
# с кучей слов перевесит всё остальное.
MAX_BONUS = 2.5

# Какие типы материалов вообще считаются «горячими».
HOT_KINDS = ("discount", "news", "free", "release")


def bonus(title: str) -> float:
    """Суммарный бонус за упоминания важных тем в заголовке."""
    low = (title or "").lower()
    total = sum(weight for word, weight in _WEIGHTS if word in low)
    return min(MAX_BONUS, total)


def score(candidate: Candidate) -> float:
    """Итоговая «интересность»: базовый балл плюс тематический бонус."""
    b = bonus(candidate.title)
    if candidate.kind == "discount":
        # Крупные скидки (-80%, -90%) и хиты получают высокий приоритет
        extra = 1.5 if ("-8" in candidate.title or "-9" in candidate.title) else 0.5
        return candidate.score + b + extra
    if candidate.kind == "news":
        return candidate.score + b + RUBRIC_BONUS.get(rubric(candidate.title) or "", 0.0)
    if candidate.kind == "release":
        return candidate.score + b + 0.5
    return candidate.score + b


def is_hot(candidate: Candidate, threshold: float) -> bool:
    """«Горячее» публикуется вне очереди. Раздачи Steam/Epic горячие всегда: у них есть срок."""
    if candidate.kind == "free":
        return True
    return candidate.kind in HOT_KINDS and score(candidate) >= threshold


def describe(candidate: Candidate) -> str:
    """Короткое пояснение для лога: какие темы сработали."""
    low = (candidate.title or "").lower()
    hits = [word for word, _ in _WEIGHTS if word in low]
    if candidate.kind == "news":
        rub = rubric(candidate.title)
        if rub:
            hits.insert(0, "рубрика: " + rub)
    return ", ".join(hits[:4]) if hits else "без ключевых слов"
