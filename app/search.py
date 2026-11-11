"""Поиск по импортированной базе знаний kb: только опубликованный контент.
Платные/закрытые фрагменты исключаются представлением kb.answerable_chunks.
"""
from . import db


def _score(value: float | None) -> float:
    # PostgreSQL ts_rank_cd обычно даёт небольшие значения; приводим к 0..1.
    raw = max(float(value or 0.0), 0.0)
    return raw / (raw + 0.02) if raw else 0.0


async def kb(query: str, limit: int = 6) -> list[dict]:
    if not query.strip():
        return []
    rows = await db.pool().fetch(
        "SELECT chunk_id AS id, material_id, kind, title, url, text, score "
        "FROM kb.search_chunks($1,$2)", query[:500], limit,
    )
    return [{**dict(r), "score": _score(r["score"])} for r in rows]


async def materials(query: str, limit: int = 10) -> list[dict]:
    if not query.strip():
        return []
    rows = await db.pool().fetch(
        "SELECT s.material_id::text AS id, m.title, "
        "CASE WHEN m.access IN ('paid','subscription') THEN NULL ELSE m.description END AS description, "
        "coalesce(array_to_string(m.topics, ', '), m.category) AS topic, "
        "m.kind AS format, NULL::text AS level, "
        "(m.access IN ('paid','subscription')) AS is_paid, m.access, "
        "CASE WHEN m.price_discount_rub IS NOT NULL THEN m.price_discount_rub "
        "     WHEN m.price_rub IS NOT NULL THEN m.price_rub END::text || "
        "CASE WHEN m.price_rub IS NOT NULL OR m.price_discount_rub IS NOT NULL THEN ' ₽' ELSE '' END AS price, "
        "CASE WHEN m.access IN ('paid','subscription') THEN s.buy_url "
        "     WHEN m.url_confirmed IS TRUE THEN s.url ELSE NULL END AS url, "
        "s.buy_url, s.score "
        "FROM kb.search($1,$2) s JOIN kb.materials m ON m.id=s.material_id "
        "WHERE m.is_recommendable AND m.is_closed IS NOT TRUE "
        "ORDER BY s.score DESC", query[:500], limit,
    )
    return [{**dict(r), "score": _score(r["score"])} for r in rows]
