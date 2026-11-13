
"""
Ранжирование материалов:
- учитываем релевантность поискового запроса;
- учитываем стадию бизнеса и сферу пользователя;
- учитываем бесплатность материала;
- не допускаем дублирования;
- объясняем пользователю, почему рекомендуем материал.

Важно: score — исходная оценка PostgreSQL из kb.search().
"""

from __future__ import annotations

import re
from typing import Any

from . import db


# Желаемый уровень материала по стадии бизнеса:
# 0 — базовый, 1 — средний, 2 — продвинутый.
WANT_LEVEL = {
    "idea": 0,
    "launch": 0,
    "running": 1,
    "growing": 2,
}

EXPERIENCE_LEVEL = {
    "novice": 0,
    "some": 1,
    "experienced": 2,
}

# Минимальная исходная оценка PostgreSQL.
# Это технический фильтр от нулевых и практически пустых совпадений,
# а не оценка качества материала.
MIN_RAW_SCORE = 0.005


def _raw_score(material: dict) -> float:
    """Получает исходную оценку поиска без искусственного усиления."""
    value = material.get("raw_score")

    if value is None:
        value = material.get("score")

    try:
        score = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

    return max(score, 0.0)


def _normalize(text: Any) -> str:
    """Нормализует текст для сравнения."""
    if not text:
        return ""

    return re.sub(r"\s+", " ", str(text).lower()).strip()


def _level(text: str | None) -> int | None:
    if not text:
        return None

    value = _normalize(text)

    if any(
        key in value
        for key in (
            "нач",
            "баз",
            "basic",
            "beginner",
            "нов",
            "intro",
            "вводн",
        )
    ):
        return 0

    if any(
        key in value
        for key in (
            "сред",
            "medium",
            "intermediate",
        )
    ):
        return 1

    if any(
        key in value
        for key in (
            "продв",
            "углуб",
            "advanced",
            "эксп",
            "pro",
        )
    ):
        return 2

    return None


def _want(profile: dict) -> int | None:
    experience = profile.get("experience")

    if experience in EXPERIENCE_LEVEL:
        return EXPERIENCE_LEVEL[experience]

    return WANT_LEVEL.get(profile.get("stage"))


def _is_paid(material: dict) -> bool:
    if material.get("is_paid") is not None:
        return bool(material["is_paid"])

    return material.get("access") in ("paid", "subscription")


def _material_key(material: dict) -> str:
    """Создаёт ключ для удаления повторов."""
    material_id = material.get("id")

    if material_id:
        return str(material_id)

    title = _normalize(material.get("title"))
    return title


def rank(
    found: list[dict],
    profile: dict,
    top: int = 3,
) -> list[dict]:
    """
    Возвращает лучшие материалы с объяснением причин рекомендации.

    Релевантность поиска является основой оценки.
    Персонализация влияет на порядок, но не заменяет релевантность.
    """

    if not found:
        return []

    profile = profile or {}
    want = _want(profile)
    area = _normalize(profile.get("area"))

    result = []
    seen = set()

    for material in found:
        material = dict(material)

        raw_score = _raw_score(material)

        # Не рекомендуем материалы с нулевым или слишком слабым
        # совпадением по исходной оценке поиска.
        if raw_score < MIN_RAW_SCORE:
            continue

        # Убираем дубли по ID или названию.
        key = _material_key(material)

        if not key or key in seen:
            continue

        seen.add(key)

        reasons = []
        bonus = 0.0

        # Релевантность запроса.
        if raw_score >= 0.08:
            reasons.append("хорошо совпадает с вашим запросом")
        else:
            reasons.append("найден по теме вашего запроса")

        # Соответствие уровню пользователя.
        material_level = _level(material.get("level"))

        if material_level is not None and want is not None:
            difference = abs(material_level - want)

            if difference == 0:
                bonus += 0.15
                reasons.append("подходит по уровню")

            elif difference == 1:
                bonus += 0.03
                reasons.append("может помочь перейти на следующий уровень")

            else:
                bonus -= 0.05

        # Соответствие сфере бизнеса.
        if area:
            haystack = _normalize(
                f"{material.get('topic') or ''} "
                f"{material.get('title') or ''} "
                f"{material.get('description') or ''}"
            )

            if area in haystack:
                bonus += 0.10
                reasons.append("относится к вашей сфере")

        # Бесплатные материалы полезны для начинающих.
        if want == 0 and not _is_paid(material):
            bonus += 0.05
            reasons.append("бесплатный материал для старта")

        # Персонализация не должна перевешивать релевантность.
        # Ограничиваем влияние бонусов относительно исходного score.
        max_bonus = raw_score * 0.25
        bonus = max(-max_bonus, min(bonus, max_bonus))

        final_score = max(0.0, raw_score + bonus)

        material["raw_score"] = raw_score
        material["score"] = raw_score
        material["final"] = final_score
        material["reasons"] = reasons

        result.append(material)

    result.sort(
        key=lambda item: (
            item["final"],
            item["raw_score"],
        ),
        reverse=True,
    )

    return result[:max(1, int(top))]


async def cold_start(limit: int = 3) -> list[dict]:
    """
    Стартовая подборка бесплатных материалов из каталога.

    Не является заменой поиску по запросу:
    использовать только тогда, когда пользователь действительно
    просит общую стартовую подборку без конкретной темы.
    """

    limit = max(1, min(int(limit), 10))

    rows = await db.pool().fetch(
        """
        SELECT
            id::text AS id,
            title,
            description,
            COALESCE(
                array_to_string(topics, ', '),
                category
            ) AS topic,
            kind AS format,
            NULL::text AS level,
            false AS is_paid,
            NULL::text AS price,
            CASE
                WHEN url_confirmed IS TRUE THEN url
                ELSE NULL
            END AS url
        FROM kb.materials
        WHERE
            access = 'free'
            AND is_recommendable IS TRUE
            AND is_closed IS NOT TRUE
        ORDER BY
            published_at DESC NULLS LAST,
            title
        LIMIT $1
        """,
        limit,
    )

    return [
        {
            **dict(row),
            "score": 0.0,
            "raw_score": 0.0,
            "final": 0.0,
            "reasons": [
                "бесплатный опубликованный материал из каталога Альфа-Курса"
            ],
        }
        for row in rows
    ]
