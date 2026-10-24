"""Оценка «интересности» материала.

Идея простая: у каждой темы есть вес. Если в заголовке встретилось что-то
крупное (GTA, Valve, раздача, отмена, скандал), материал получает бонус —
и бот может опубликовать его сразу, не дожидаясь планового поста.
"""
from __future__ import annotations

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

# Бонус не может быть больше этого значения, иначе один заголовок
# с кучей слов перевесит всё остальное.
MAX_BONUS = 2.5

# Какие типы материалов вообще считаются «горячими».
HOT_KINDS = ("news", "free")


def bonus(title: str) -> float:
    """Суммарный бонус за упоминания важных тем в заголовке."""
    low = (title or "").lower()
    total = sum(weight for word, weight in _WEIGHTS if word in low)
    return min(MAX_BONUS, total)


def score(candidate: Candidate) -> float:
    """Итоговая «интересность»: базовый балл плюс тематический бонус."""
    if candidate.kind == "news":
        return candidate.score + bonus(candidate.title)
    return candidate.score


def is_hot(candidate: Candidate, threshold: float) -> bool:
    return candidate.kind in HOT_KINDS and score(candidate) >= threshold


def describe(candidate: Candidate) -> str:
    """Короткое пояснение для лога: какие темы сработали."""
    low = (candidate.title or "").lower()
    hits = [word for word, _ in _WEIGHTS if word in low]
    return ", ".join(hits[:4]) if hits else "без ключевых слов"
