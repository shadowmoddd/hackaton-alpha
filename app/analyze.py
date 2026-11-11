"""Разбор сообщения пользователя: намерение, сведения о бизнесе, параметры напоминания.

Во внешнюю LLM уходит только текст сообщения, последние реплики диалога и
обезличенные поля профиля (стадия, сфера, режим налогообложения). Имени и id
пользователя в запросах нет.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from . import llm
from .config import settings

INTENTS = {"question", "recommend", "reminder", "profile", "chitchat"}
STAGES = {"idea", "launch", "running", "growing"}
REGIMES = {"usn", "npd", "osno", "patent", "other"}
EXPERIENCE = {"novice", "some", "experienced"}
REPEATS = {"none", "daily", "weekly", "monthly", "every_n_days"}

SYSTEM = """Ты — модуль разбора сообщений для ассистента образовательной платформы «Альфа-Курс» (малый бизнес, начинающие предприниматели, соло-фаундеры).
Верни ТОЛЬКО JSON без пояснений:
{
 "intent": "question" | "recommend" | "reminder" | "profile" | "chitchat",
 "search_query": "переформулированный запрос для поиска по базе знаний, по-русски, без местоимений",
 "clarify": null | "один короткий уточняющий вопрос",
 "profile": {"stage": "idea|launch|running|growing|null", "area": "сфера бизнеса или null", "tax_regime": "usn|npd|osno|patent|other|null", "experience": "novice|some|experienced|null", "goal": "текущая задача или null"},
 "reminder": null | {"text": "о чём напомнить", "when": "ISO 8601 с часовым поясом или null", "repeat": "none|daily|weekly|monthly|every_n_days", "n": число или null, "missing": null | "что нужно уточнить"}
}
Правила:
- intent=reminder, если человек просит напомнить или поставить напоминание.
- intent=recommend, если просит посоветовать, с чего начать, что изучить, какой курс или шаблон выбрать.
- intent=profile, если он только сообщает сведения о себе или бизнесе, ничего не спрашивая.
- intent=chitchat — приветствия и не относящиеся к бизнесу реплики.
- В profile записывай только то, что человек явно сообщил, иначе null. stage: idea — только думает об открытии; launch — запускает; running — бизнес работает; growing — развивает или масштабирует.
- clarify задавай, только если без уточнения нельзя дать полезный ответ (например, вопрос про налоги без режима налогообложения, слишком общий вопрос). Если ответить можно — clarify=null. Не больше одного вопроса.
- Для reminder: если время не названо — when=null и missing="Когда напомнить?". Относительное время («завтра в 10», «через 3 дня») считай от текущего времени пользователя.
- Учитывай предыдущие реплики диалога: ответ на твой уточняющий вопрос — часть того же запроса.
"""


def _clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in {"", "null", "none", "unknown", "n/a"} else s


def _enum(v, allowed):
    v = _clean(v)
    return v.lower() if v and v.lower() in allowed else None


async def run(text: str, profile: dict, history: list[tuple[str, str]]) -> dict:
    tz = profile.get("tz") or settings.default_tz
    now = datetime.now(ZoneInfo(tz)).isoformat(timespec="minutes")
    known = {k: profile.get(k) for k in ("stage", "area", "tax_regime", "experience", "goal") if profile.get(k)}
    prompt = (
        f"Текущее время пользователя: {now} (часовой пояс {tz})\n"
        f"Уже известно о пользователе: {json.dumps(known, ensure_ascii=False)}\n"
        f"Сообщение: {text}"
    )
    try:
        raw = await llm.chat(SYSTEM, prompt, history=history, json_mode=True, temperature=0, max_tokens=500)
        d = llm.parse_json(raw)
    except Exception:
        d = {}

    intent = d.get("intent") if d.get("intent") in INTENTS else "question"
    p = d.get("profile") if isinstance(d.get("profile"), dict) else {}
    profile_update = {
        "stage": _enum(p.get("stage"), STAGES),
        "tax_regime": _enum(p.get("tax_regime"), REGIMES),
        "experience": _enum(p.get("experience"), EXPERIENCE),
        "area": (_clean(p.get("area")) or "")[:60] or None,
        "goal": (_clean(p.get("goal")) or "")[:100] or None,
    }

    reminder = None
    r = d.get("reminder")
    if intent == "reminder" and isinstance(r, dict):
        repeat = r.get("repeat") if r.get("repeat") in REPEATS else "none"
        try:
            n = int(r.get("n")) if r.get("n") else None
        except (TypeError, ValueError):
            n = None
        reminder = {
            "text": (_clean(r.get("text")) or "Напоминание")[:300],
            "when": _clean(r.get("when")),
            "repeat": repeat,
            "n": n,
            "missing": _clean(r.get("missing")),
        }

    return {
        "intent": intent,
        "search_query": _clean(d.get("search_query")) or text,
        "clarify": _clean(d.get("clarify")),
        "profile": profile_update,
        "reminder": reminder,
    }
