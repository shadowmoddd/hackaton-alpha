
"""Понимание сообщений пользователя: намерение, профиль, поиск и напоминания."""

from __future__ import annotations

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

GENERIC_RECOMMEND_RE = re.compile(
    r"^\s*(?:подбери|подберите|найди|найдите|порекомендуй|порекомендуйте)"
    r"\s+(?:мне\s+)?(?:материалы|курсы|обучение|инструкции|ресурсы)"
    r"\s*(?:под\s+(?:мою|эту|нашу)\s+задачу)?[.!?]*\s*$",
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
    "расскажите подробнее",
    "помоги мне",
    "помогите мне",
    "да",
    "нет",
    "ага",
    "ок",
    "хорошо",
}

TYPO_REPLACEMENTS = {
    "клиаентов": "клиентов",
    "клиентв": "клиентов",
    "предпренимателей": "предпринимателей",
    "предприниматлей": "предпринимателей",
    "предприниматль": "предприниматель",
    "бизнесс": "бизнес",
    "бизнeс": "бизнес",
    "поставшиков": "поставщиков",
    "поставщеков": "поставщиков",
    "налогооблажение": "налогообложение",
    "налогооблажения": "налогообложения",
    "маркетенг": "маркетинг",
    "продвиженее": "продвижение",
}

SYSTEM = """
Ты — модуль понимания сообщений пользователя платформы «Альфа-Курс»
для предпринимателей.

Верни строго один JSON-объект без Markdown и пояснений.

Формат:
{
  "intent": "question",
  "search_query": "конкретная тема для поиска",
  "clarify": null,
  "profile": {
    "stage": null,
    "area": null,
    "business_type": null,
    "tax_regime": null,
    "experience": null,
    "goal": null
  },
  "reminder": null
}

Допустимые intent:
- question — вопрос о бизнесе;
- recommend — пользователь просит подобрать материалы;
- reminder — просьба создать напоминание;
- profile — пользователь сообщает сведения о себе или бизнесе;
- chitchat — приветствие или разговор не по теме.

Правила определения намерения:
1. Учитывай историю диалога.
2. Если пользователь отвечает на уточняющий вопрос, учитывай предыдущую задачу.
3. Короткие ответы «да», «нет», «ага», «хорошо» не являются самостоятельными поисковыми запросами.
4. Просьба подобрать материалы означает recommend.
5. Обычный вопрос о бизнесе означает question, даже если пользователь хочет открыть бизнес.
6. Не путай вопрос «как открыть бизнес?» с просьбой подобрать курсы.
7. Если пользователь сообщает сведения о своём бизнесе, сохрани их в profile.
8. Не придумывай сведения о пользователе.
9. Не классифицируй обычный вопрос о бизнесе как chitchat.
10. Не задавай уточнение, если можно дать полезный общий ответ.

Правила search_query:
- Короткая и конкретная тема.
- Пиши по-русски.
- Исправляй очевидные опечатки.
- Убирай вводные слова и местоимения.
- Не добавляй сведения из профиля, не относящиеся к вопросу.
- Для «первые клиенты» используй тему привлечения первых клиентов.
- Для «открыть цветочный бизнес» используй тему открытия цветочного бизнеса.
- Для «ошибки предпринимателей» используй тему типичных ошибок начинающих предпринимателей.
- Не возвращай общие фразы без восстановления темы из истории.

Профиль:
Записывай только то, что пользователь явно сообщил.
Не перезаписывай известные сведения догадками.

stage:
- idea — только планирует бизнес;
- launch — запускает бизнес;
- running — бизнес уже работает;
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

Уточнение:
- clarify=null, если можно ответить.
- Если без детали действительно невозможно ответить, задай один конкретный вопрос.
- Не повторяй вопрос, на который пользователь уже ответил.

Напоминания:
Если пользователь просит напомнить, intent=reminder.

Формат reminder:
{
  "text": "о чём напомнить",
  "when": "ISO 8601 с часовым поясом или null",
  "repeat": "none",
  "n": null,
  "missing": null
}

Время рассчитывай относительно указанного местного времени.
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


def _normalize_text(value):
    """Нормализует пробелы и исправляет известные опечатки."""
    value = re.sub(r"\s+", " ", str(value or "").strip())
    words = value.split()

    corrected = []

    for word in words:
        match = re.match(r"^([^\w]*)(.*?)([^\w]*)$", word, re.UNICODE)

        if not match:
            corrected.append(word)
            continue

        prefix, core, suffix = match.groups()
        replacement = TYPO_REPLACEMENTS.get(core.lower())

        if replacement:
            if core[:1].isupper():
                replacement = replacement.capitalize()

            corrected.append(prefix + replacement + suffix)
        else:
            corrected.append(word)

    return " ".join(corrected)


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
    business_type = _clean(data.get("business_type"))

    return {
        "stage": _enum(data.get("stage"), STAGES),
        "area": area[:60] if area else None,
        "business_type": business_type[:100] if business_type else None,
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

    if n is not None:
        n = max(1, min(n, 365))

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


def _is_short_reply(text):
    normalized = re.sub(r"[.!?,;:]+$", "", text.strip().lower())
    return normalized in GENERIC_PHRASES


def _history_task(history, current_text):
    """Ищет последнюю конкретную задачу пользователя в истории."""
    if not isinstance(history, list):
        return None

    current_normalized = _normalize_text(current_text).lower()

    for item in reversed(history):
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue

        role, content = item[0], item[1]

        if role not in {"user", "human"}:
            continue

        content = _clean(content)

        if not content:
            continue

        normalized = _normalize_text(content).lower()

        if normalized == current_normalized:
            continue

        if _is_short_reply(normalized):
            continue

        if GENERIC_RECOMMEND_RE.match(content):
            continue

        if len(content) < 12:
            continue

        return _normalize_text(content)[:500]

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


def _fallback_intent(text):
    """Резервное определение намерения, если LLM не вернула JSON."""
    value = _normalize_text(text).lower()

    if re.search(
        r"\b(напомни|напомните|поставь напоминание|создай напоминание)\b",
        value,
    ):
        return "reminder"

    if re.search(
        r"\b(привет|здравствуй|добрый день|доброе утро|добрый вечер)\b",
        value,
    ):
        return "chitchat"

    if _is_generic_recommendation(value):
        return "recommend"

    if re.search(
        r"\b(подбери|подберите|порекомендуй|порекомендуйте|"
        r"найди материалы|найдите материалы)\b",
        value,
    ):
        return "recommend"

    return "question"


def _fallback_query(text):
    """Создаёт поисковую тему без зависимости от ответа LLM."""
    value = _normalize_text(text)
    lower = value.lower()

    replacements = (
        (r"^(?:я хочу|хочу|планирую|собираюсь)\s+", ""),
        (
            r"^(?:подскажи|подскажите|расскажи|расскажите|"
            r"объясни|объясните|помоги|помогите|можешь рассказать)\s+",
            "",
        ),
        (r"^(?:мне нужно|мне бы хотелось)\s+", ""),
    )

    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)

    value = re.sub(r"\s+", " ", value).strip(" .,!?:;")

    special = (
        (r"\bпервые клиенты\b", "привлечение первых клиентов"),
        (r"\bцветочн\w*\s+бизнес\b", "открытие цветочного бизнеса"),
        (
            r"\bошибк\w*\s+начинающ\w*\s+предпринимател\w*\b",
            "типичные ошибки начинающих предпринимателей",
        ),
        (
            r"\bошибк\w*\s+предпринимател\w*\b",
            "типичные ошибки предпринимателей",
        ),
        (
            r"\bнадёжн\w*\s+поставщик\w*\b",
            "поиск надёжных поставщиков",
        ),
        (
            r"\bнадежн\w*\s+поставщик\w*\b",
            "поиск надёжных поставщиков",
        ),
    )

    for pattern, replacement in special:
        if re.search(pattern, lower):
            return replacement

    return value or _normalize_text(text)


def _normalize_search_query(value, fallback):
    """Отсекает пустые и общие поисковые формулировки."""
    query = _clean(value)

    if not query:
        return fallback

    query = _normalize_text(query).strip(" .,!?:;")

    if len(query) < 3 or _is_generic_recommendation(query):
        return fallback

    return query[:500]


async def run(
    text: str,
    profile: dict,
    history: list[tuple[str, str]] | None = None,
) -> dict:
    """Анализирует сообщение и возвращает нормализованную структуру."""
    text = _normalize_text(text)

    history = history or []
    profile = profile or {}

    tz_name = profile.get("tz") or settings.default_tz
    tz = _timezone(tz_name)
    now = datetime.now(tz).isoformat(timespec="minutes")

    known = {
        key: profile.get(key)
        for key in (
            "stage",
            "area",
            "business_type",
            "tax_regime",
            "experience",
            "goal",
        )
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
            max_tokens=700,
        )
        data = llm.parse_json(raw)
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    intent = data.get("intent")

    if intent not in INTENTS:
        intent = _fallback_intent(text)

    # Явная просьба подобрать материалы имеет приоритет над ответом LLM.
    if _is_generic_recommendation(text):
        intent = "recommend"
    elif re.search(
        r"\b(подбери|подберите|порекомендуй|порекомендуйте)\s+"
        r"(?:мне\s+)?(?:материалы|курсы|обучение|инструкции)\b",
        text.lower(),
    ):
        intent = "recommend"

    # Не превращаем короткий ответ в самостоятельный поисковый запрос.
    if _is_short_reply(text) and intent in {"question", "recommend"}:
        previous_task = _history_task(history, text)

        if previous_task:
            intent = "recommend" if data.get("intent") == "recommend" else "question"

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

    elif intent in {"question", "recommend"}:
        fallback = _fallback_query(text)

        if _is_short_reply(text):
            fallback = _history_task(history, text)

        search_query = _normalize_search_query(search_query, fallback)

    else:
        search_query = None

    # Понятный вопрос не должен блокироваться случайным уточнением LLM.
    if intent == "question" and search_query and len(search_query) >= 5:
        clarify = None

    # Если тема не определена, пробуем восстановить её из истории.
    if not search_query and intent in {"question", "recommend"}:
        search_query = _history_task(history, text)

    return {
        "intent": intent,
        "search_query": search_query[:500] if search_query else None,
        "clarify": clarify[:300] if clarify else None,
        "profile": profile_update,
        "reminder": reminder,
    }
