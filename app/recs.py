"""Ранжирование материалов: релевантность запросу + соответствие профилю.

Для каждого материала собираем список причин («почему рекомендуем»), чтобы
рекомендацию можно было объяснить пользователю.
"""
from . import db
from .config import settings

# желаемый уровень материала по стадии бизнеса: 0 — базовый, 1 — средний, 2 — продвинутый
WANT_LEVEL = {"idea": 0, "launch": 0, "running": 1, "growing": 2}
EXPERIENCE_LEVEL = {"novice": 0, "some": 1, "experienced": 2}


def _level(text: str | None) -> int | None:
    if not text:
        return None
    t = text.lower()
    if any(k in t for k in ("нач", "баз", "basic", "beginner", "нов", "intro", "вводн")):
        return 0
    if any(k in t for k in ("сред", "medium", "intermediate")):
        return 1
    if any(k in t for k in ("продв", "углуб", "advanced", "эксп", "pro")):
        return 2
    return None


def _want(profile: dict) -> int | None:
    if profile.get("experience") in EXPERIENCE_LEVEL:
        return EXPERIENCE_LEVEL[profile["experience"]]
    return WANT_LEVEL.get(profile.get("stage"))


def rank(found: list[dict], profile: dict, top: int = 3) -> list[dict]:
    want = _want(profile)
    area = (profile.get("area") or "").lower()
    result = []
    for m in found:
        if m["score"] < settings.mat_min_score:
            continue
        reasons = []
        bonus = 0.0
        if m["score"] >= 0.5:
            reasons.append("хорошо совпадает с вашим запросом")
        else:
            reasons.append("связан с темой вашего запроса")

        lvl = _level(m.get("level"))
        if lvl is not None and want is not None:
            if lvl == want:
                bonus += 0.15
                reasons.append("уровень подходит вашей стадии")
            elif abs(lvl - want) >= 2:
                bonus -= 0.10
        if area:
            hay = f"{m.get('topic') or ''} {m.get('title') or ''}".lower()
            if area in hay:
                bonus += 0.10
                reasons.append("относится к вашей сфере")
        if want == 0 and not m["is_paid"]:
            bonus += 0.05
            reasons.append("бесплатный старт без затрат")

        result.append({**m, "final": m["score"] + bonus, "reasons": reasons})
    result.sort(key=lambda x: x["final"], reverse=True)
    return result[:top]


async def cold_start(limit: int = 3) -> list[dict]:
    """Стартовая подборка опубликованных бесплатных материалов из каталога Альфа-Курса."""
    rows = await db.pool().fetch(
        "SELECT id::text AS id, title, description, coalesce(array_to_string(topics, ', '), category) AS topic, "
        "kind AS format, NULL::text AS level, false AS is_paid, NULL::text AS price, "
        "CASE WHEN url_confirmed IS TRUE THEN url ELSE NULL END AS url "
        "FROM kb.materials WHERE access='free' AND is_recommendable AND is_closed IS NOT TRUE "
        "ORDER BY published_at DESC NULLS LAST, title LIMIT $1", limit,
    )
    return [
        {**dict(r), "score": 0.0, "final": 0.0,
         "reasons": ["бесплатный опубликованный материал из каталога Альфа-Курса"]}
        for r in rows
    ]
