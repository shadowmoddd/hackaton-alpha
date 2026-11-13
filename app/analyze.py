
"""Разбор сообщений пользователя: намерение, профиль, поиск и напоминания."""

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import llm
from .config import settings


INTENTS = {"question", "recommend", "reminder", "profile", "chitchat"}
STAGES = {"idea", "launch", "running", "growing"}
REGIMES = {"usn", "npd", "osno", "patent", "other"}
EXPERIENCE = {"novice", "some", "experienced"}
REPEATS = {"none", "daily", "weekly", "monthly", "every_n_days"}

# Фразы, которые сами по себе не являются темой для поиска.
GENERIC_RECOMMEND_RE = re.compile(
    r"^\s*(?:"
    r"подбери|подберите|найди|найдите|порекомендуй|порекомендуйте"
    r")\s+(?:мне\s+)?(?:"
    r"материалы|курсы|обучение|инструкции|ресурсы"
    r")\s*(?:под\s+(?:мою|эту|нашу)\s+задачу)?[.!?]*\s*$",
    re.IGNORECASE,
)

GENERIC_PHRASES = {
    "подбери материалы под мою задачу",
    "подберите материалы под мою задачу",
    "подбери материалы",
    "подберите материалы",
    "найди материалы",
    "найдите материалы",
    "порекомендуй материалы",
    "порекомендуйте материалы",
    "подбери под это",
    "подберите под это",
    "расскажи подробнее",
    "помоги мне",
    "да",
    "нет",
}


SYSTEM = """
Ты — модуль понимания сообщений пользователя образовательной платформы
«Альфа-Курс» для предпринимателей.

Твоя задача — понять смысл сообщения с учётом истории диалога и вернуть
строго один JSON-объект без Markdown и пояснений.

Формат:
{
  "intent": "question",
  "search_query": "короткий поисковый запрос по существу",
  "clarify": null,
  "profile": {
    "stage": null,
    "area": null,
    "tax_regime": null,
    "experience": null,
    "goal": null
  },
  "reminder": null
}

Допустимые intent:
- question — вопрос, на который нужно ответить;
- recommend — просьба подобрать материалы, курс, инструкцию или план обучения;
- reminder — просьба создать напоминание;
- profile — пользователь сообщает сведения о себе или бизнесе;
- chitchat — приветствие или разговор не по теме.

Правила понимания диалога:
1. Всегда учитывай предыдущие сообщения.
2. Если пользователь отвечает на уточняющий вопрос, объедини его ответ
   с первоначальной задачей.
3. Не воспринимай фразы «да», «нет», «для моего бизнеса», «подбери под это»
   как самостоятельные поисковые запросы.
4. Если пользователь просит подобрать материалы под свою задачу,
   восстанови конкретную задачу из последних сообщений диалога.
5. Если задачи в истории нет, используй основную цель пользователя из профиля.
6. Если нет ни задачи в диалоге, ни цели в профиле — задай один конкретный
   уточняющий вопрос.
7. Не меняй намерение пользователя без причины.
8. Не задавай повторно вопрос, на который пользователь уже ответил.
9. Не придумывай сведения о пользователе.

Правила search_query:
- Только конкретная тема или задача пользователя.
- Пиши по-русски.
- Убирай местоимения и вводные фразы.
- Не добавляй сферу бизнеса и цель из профиля, если они не относятся
  к текущему вопросу.
- Для продолжения диалога учитывай первоначальную задачу.
- Не используй в качестве запроса фразы «помоги мне», «подбери материалы»,
  «расскажи подробнее» без восстановления темы из истории.

Правила профиля:
Записывай только сведения, которые пользователь явно сообщил
в текущем сообщении.
Если поле не упоминалось, возвращай null.
Не перезаписывай известные сведения догадками.

Допустимые значения:
stage:
- idea — только думает об открытии бизнеса;
- launch — запускает бизнес;
- running — действующий бизнес;
- growing — развивает или масштабирует бизнес.

tax_regime:
- usn — УСН;
- npd — НПД / самозанятость;
- osno — ОСНО;
- patent — патент;
- other — другое.

experience:
- novice — начинающий;
- some — есть некоторый опыт;
- experienced — опытный.

Правила уточнения:
- clarify — null, если можно ответить.
- Если без уточнения невозможно дать полезный ответ, задай один короткий
  конкретный вопрос.
- Если в истории уже задавался такой вопрос, не повторяй его автоматически.

Правила напоминаний:
Если пользователь просит напомнить, intent должен быть reminder.

Формат reminder:
{
  "text": "о чём напомнить",
  "when": "ISO 8601 с часовым поясом или null",
  "repeat": "none",
  "n": null,
  "missing": null
}

Время рассчитывай относительно текущего времени пользователя.
Если время не указано, when=null, missing="Когда напомнить?".
Не выдумывай дату и время.
"""


def _clean(value):
    if value is None:
        return None

    value = str(value).strip()

    if value.lower() in {"", "null", "none", "unknown", "n/a"}:
        return None

    return value


def _enum(value, allowed):
    value = _clean(value)

    if not value:
        return None

    value = value.lower()
    return value if value in allowed else None


def _timezone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo(settings.default_tz)


def _profile_update(data):
    if not isinstance(data, dict):
        data = {}

    area = _clean(data.get("area"))
    goal = _clean(data.get("goal"))

    return {
        "stage": _enum(data.get("stage"), STAGES),
        "area": area[:60] if area else None,
        "tax_regime": _enum(data.get("tax_regime"), REGIMES),
        "experience": _enum(data.get("experience"), EXPERIENCE),
        "goal": goal[:100] if goal else None,
    }


def _reminder_data(data):
    if not isinstance(data, dict):
        return None

    repeat = data.get("repeat")
    if repeat not in REPEATS:
        repeat = "none"

    try:
        n = int(data["n"]) if data.get("n") is not None else None
    except (TypeError, ValueError):
        n = None

    text = _clean(data.get("text"))
    when = _clean(data.get("when"))
    missing = _clean(data.get("missing"))

    return {
        "text": (text or "Напоминание")[:300],
        "when": when,
        "repeat": repeat,
        "n": n,
        "missing": missing,
    }


def _history_task(history, current_text):
    """Ищет последнюю конкретную задачу пользователя в истории."""

    if not isinstance(history, list):
        return None

    current_normalized = current_text.strip().lower()

    for item in reversed(history):
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue

        role, content = item[0], item[1]

        if role not in {"user", "human"}:
            continue

        content = _clean(content)
        if not content:
            continue

        normalized = content.lower()

        if normalized == current_normalized:
            continue

        if normalized in GENERIC_PHRASES:
            continue

        if GENERIC_RECOMMEND_RE.match(content):
            continue

        # Не используем короткие ответы и приветствия как задачу.
        if len(content) < 12:
            continue

        return content[:500]

    return None


def _is_generic_recommendation(text):
    normalized = re.sub(r"\s+", " ", text.strip().lower())

    return (
        normalized in GENERIC_PHRASES
        or bool(GENERIC_RECOMMEND_RE.match(text))
    )


def _resolve_recommendation_query(text, profile, history):
    """Восстанавливает тему рекомендаций из диалога или профиля."""

    task = _history_task(history, text)

    if task:
        return task, None

    goal = _clean(profile.get("goal")) if isinstance(profile, dict) else None

    if goal:
        return goal, None

    return None, (
        "Какую задачу вы хотите решить? Например: найти первых клиентов, "
        "увеличить продажи или разобраться с налогами."
    )


async def run(
    text: str,
    profile: dict,
    history: list[tuple[str, str]] | None = None,
) -> dict:
    """Анализирует сообщение и возвращает нормализованную структуру."""

    history = history or []
    profile = profile or {}

    tz_name = profile.get("tz") or settings.default_tz
    tz = _timezone(tz_name)
    now = datetime.now(tz).isoformat(timespec="minutes")

    known = {
        key: profile.get(key)
        for key in ("stage", "area", "tax_regime", "experience", "goal")
        if profile.get(key)
    }

    prompt = (
        f"Текущее местное время пользователя: {now}\n"
        f"Часовой пояс: {tz_name}\n"
        f"Известный профиль пользователя: "
        f"{json.dumps(known, ensure_ascii=False)}\n\n"
        f"Новое сообщение пользователя:\n{text[:2000]}\n\n"
        "Проанализируй новое сообщение с учётом истории диалога. "
        "Верни JSON строго по заданной схеме."
    )

    try:
        raw = await llm.chat(
            SYSTEM,
            prompt,
            history=history[-6:],
            json_mode=True,
            temperature=0,
            max_tokens=600,
        )
        data = llm.parse_json(raw)
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    intent = data.get("intent")
    if intent not in INTENTS:
        intent = "question"

    # Явная просьба о подборе материалов имеет приоритет
    # над случайной классификацией модели.
    if _is_generic_recommendation(text):
        intent = "recommend"

    profile_update = _profile_update(data.get("profile"))

    reminder = None
    if intent == "reminder":
        reminder = _reminder_data(data.get("reminder"))

    search_query = _clean(data.get("search_query"))
    clarify = _clean(data.get("clarify"))

    if intent == "recommend" and _is_generic_recommendation(text):
        search_query, context_clarify = _resolve_recommendation_query(
            text,
            profile,
            history,
        )

        if context_clarify:
            clarify = context_clarify

    elif not search_query:
        search_query = text.strip()

    # Не отправляем общие фразы в поисковый индекс.
    if intent == "recommend" and search_query:
        if _is_generic_recommendation(search_query):
            search_query = None

    return {
        "intent": intent,
        "search_query": search_query[:500] if search_query else None,
        "clarify": clarify[:300] if clarify else None,
        "profile": profile_update,
        "reminder": reminder,
    }
