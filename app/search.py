
from __future__ import annotations

from typing import Any

from . import db


def _raw_score(value: Any) -> float:
    """Возвращает исходную оценку PostgreSQL без искусственного усиления."""
    try:
        score = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

    if score < 0:
        return 0.0

    return score


async def kb(query: str, limit: int = 6) -> list[dict]:
    """
    Ищет фрагменты базы знаний для ответа на вопрос.

    score и raw_score содержат исходную оценку PostgreSQL.
    """
    query = (query or "").strip()
    if not query:
        return []

    limit = max(1, min(int(limit), 20))

    rows = await db.pool().fetch(
        """
        SELECT
            chunk_id AS id,
            material_id,
            kind,
            title,
            url,
            text,
            score
        FROM kb.search_chunks($1, $2)
        """,
        query[:500],
        limit,
    )

    results = []

    for row in rows:
        item = dict(row)
        raw = _raw_score(item.get("score"))

        item["raw_score"] = raw
        item["score"] = raw

        results.append(item)

    return results


async def materials(query: str, limit: int = 10) -> list[dict]:
    """
    Ищет материалы, которые разрешено рекомендовать пользователю.

    Не усиливает исходную оценку поиска.
    Платные материалы не получают ссылку на бесплатный просмотр.
    """
    query = (query or "").strip()
    if not query:
        return []

    limit = max(1, min(int(limit), 30))

    rows = await db.pool().fetch(
        """
        SELECT
            s.material_id::text AS id,
            m.title,

            CASE
                WHEN m.access IN ('paid', 'subscription')
                    THEN NULL
                ELSE m.description
            END AS description,

            COALESCE(
                array_to_string(m.topics, ', '),
                m.category
            ) AS topic,

            m.kind AS format,
            NULL::text AS level,

            (m.access IN ('paid', 'subscription')) AS is_paid,
            m.access,

            CASE
                WHEN m.price_discount_rub IS NOT NULL
                    THEN m.price_discount_rub
                WHEN m.price_rub IS NOT NULL
                    THEN m.price_rub
                ELSE NULL
            END::text
            ||
            CASE
                WHEN m.price_rub IS NOT NULL
                  OR m.price_discount_rub IS NOT NULL
                    THEN ' ₽'
                ELSE ''
            END AS price,

            CASE
                WHEN m.access IN ('paid', 'subscription')
                    THEN s.buy_url
                WHEN m.url_confirmed IS TRUE
                    THEN s.url
                ELSE NULL
            END AS url,

            s.buy_url,
            s.score AS raw_score

        FROM kb.search($1, $2) AS s
        JOIN kb.materials AS m
            ON m.id = s.material_id

        WHERE
            m.is_recommendable IS TRUE
            AND m.is_closed IS NOT TRUE
            AND (
                m.access IN ('paid', 'subscription')
                OR m.url_confirmed IS TRUE
            )

        ORDER BY s.score DESC
        LIMIT $2
        """,
        query[:500],
        limit,
    )

    results = []

    for row in rows:
        item = dict(row)
        raw = _raw_score(item.get("raw_score"))

        # Пока сохраняем оба значения:
        # score — для совместимости с текущим recs.py;
        # raw_score — для нового ранжирования без искажения.
        item["raw_score"] = raw
        item["score"] = raw

        results.append(item)

    return results
