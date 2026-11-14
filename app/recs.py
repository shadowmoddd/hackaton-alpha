"""
Ранжирование материалов по релевантности поискового запроса и профилю.
Слабые совпадения отбрасываются, дубли удаляются.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from . import db

WANT_LEVEL = {"idea": 0, "launch": 0, "running": 1, "growing": 2}
EXPERIENCE_LEVEL = {"novice": 0, "some": 1, "experienced": 2}
MIN_RAW_SCORE = 0.005

class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        value = data.strip()
        if value:
            self.parts.append(value)

def _plain(value: Any) -> str:
    if value is None:
        return ""
    raw = html.unescape(str(value))
    parser = _TextParser()
    try:
        parser.feed(raw)
        parser.close()
        result = " ".join(parser.parts)
    except Exception:
        result = re.sub(r"<[^>]*>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(result)).strip()

def _raw_score(material: dict) -> float:
    value = material.get("raw_score")
    if value is None:
        value = material.get("score")
    try:
        score = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(score, 0.0)

def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", _plain(text).lower()).strip() if text else ""

def _level(text: str | None) -> int | None:
    if not text:
        return None
    value = _normalize(text)
    if any(k in value for k in ("нач", "баз", "basic", "beginner", "нов", "intro", "вводн")):
        return 0
    if any(k in value for k in ("сред", "medium", "intermediate")):
        return 1
    if any(k in value for k in ("продв", "углуб", "advanced", "эксп", "pro")):
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
    material_id = material.get("id")
    return str(material_id) if material_id else _normalize(material.get("title"))

def rank(found: list[dict], profile: dict, top: int = 2) -> list[dict]:
    if not found:
        return []
    profile = profile or {}
    want = _want(profile)
    area = _normalize(profile.get("area"))
    result, seen = [], set()

    for source in found:
        material = dict(source)
        material["title"] = _plain(material.get("title"))
        material["description"] = _plain(material.get("description"))
        material["topic"] = _plain(material.get("topic"))
        raw_score = _raw_score(material)
        if raw_score < MIN_RAW_SCORE:
            continue
        key = _material_key(material)
        if not key or key in seen:
            continue
        seen.add(key)

        bonus = 0.0
        reasons = ["соответствует поисковому запросу"]
        material_level = _level(material.get("level"))
        if material_level is not None and want is not None:
            difference = abs(material_level - want)
            if difference == 0:
                bonus += 0.15
                reasons.append("подходит по уровню")
            elif difference == 1:
                bonus += 0.03
            else:
                bonus -= 0.05

        if area:
            haystack = _normalize(f"{material.get('topic', '')} {material.get('title', '')} {material.get('description', '')}")
            if area in haystack:
                bonus += 0.10
                reasons.append("относится к вашей сфере")
        if want == 0 and not _is_paid(material):
            bonus += 0.05

        max_bonus = raw_score * 0.25
        bonus = max(-max_bonus, min(bonus, max_bonus))
        material["raw_score"] = raw_score
        material["score"] = raw_score
        material["final"] = max(0.0, raw_score + bonus)
        material["reasons"] = reasons
        result.append(material)

    result.sort(key=lambda item: (item["final"], item["raw_score"]), reverse=True)
    return result[:max(1, min(int(top), 2))]

async def cold_start(limit: int = 3) -> list[dict]:
    limit = max(1, min(int(limit), 10))
    rows = await db.pool().fetch(
        """
        SELECT
            id::text AS id,
            title,
            description,
            COALESCE(array_to_string(topics, ', '), category) AS topic,
            kind AS format,
            NULL::text AS level,
            false AS is_paid,
            NULL::text AS price,
            CASE WHEN url_confirmed IS TRUE THEN url ELSE NULL END AS url
        FROM kb.materials
        WHERE access = 'free'
          AND is_recommendable IS TRUE
          AND is_closed IS NOT TRUE
        ORDER BY published_at DESC NULLS LAST, title
        LIMIT $1
        """,
        limit,
    )
    result = []
    for row in rows:
        item = dict(row)
        item["title"] = _plain(item.get("title"))
        item["description"] = _plain(item.get("description"))
        item["topic"] = _plain(item.get("topic"))
        item.update({"score": 0.0, "raw_score": 0.0, "final": 0.0, "reasons": []})
        result.append(item)
    return result
