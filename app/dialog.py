
"""Логика диалога. Возвращает (текст, кнопки); кнопки — словари в формате aiogram InlineKeyboardButton."""

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone

from . import analyze, db, llm, recs, reminders, search
from .config import settings
from .util import is_url, link

log = logging.getLogger(__name__)

Reply = tuple[str, list[list[dict]] | None]

HISTORY: dict[int, deque] = defaultdict(lambda: deque(maxlen=6))
CLARIFIED: set[int] = set()

STAGE_RU = {
    "idea": "думает об открытии",
    "launch": "запускает дело",
    "running": "бизнес работает",
    "growing": "развивает бизнес",
}

REGIME_RU = {
    "usn": "УСН",
    "npd": "НПД (самозанятый)",
    "osno": "ОСНО",
    "patent": "патент",
    "other": "другой",
}

ANSWER_SYSTEM = """Ты — ассистент образовательной платформы «Альфа-Курс» для предпринимателей. Отвечай ТОЛЬКО на основе приведённых фрагментов базы знаний.
Правила:
- Если во фрагментах нет ответа на вопрос, выведи ровно НЕТ_ОТВЕТА и ничего больше.
- Ничего не выдумывай: цифры, сроки и ставки бери только из фрагментов.
- Пиши по-русски, просто, 3–6 предложений; допускается короткий список шагов.
- Учитывай профиль пользователя, если он известен, но не пересказывай его.
- Не вставляй ссылки и названия платных курсов: рекомендации добавит система.
- Закончи ответ одной строкой «Следующий шаг: …» с конкретным действием."""


def profile_text(profile: dict) -> str:
    parts = []

    if profile.get("stage"):
        parts.append(
            f"стадия: {STAGE_RU.get(profile['stage'], profile['stage'])}"
        )

    if profile.get("area"):
        parts.append(f"сфера: {profile['area']}")

    if profile.get("tax_regime"):
        parts.append(
            f"налогообложение: "
            f"{REGIME_RU.get(profile['tax_regime'], profile['tax_regime'])}"
        )

    if profile.get("experience"):
        parts.append(f"опыт: {profile['experience']}")

    if profile.get("goal"):
        parts.append(f"задача: {profile['goal']}")

    return "; ".join(parts) or "неизвестен"


def _mat_lines(mats: list[dict]) -> str:
    lines = []

    for m in mats:
        tag = "💳 платный" if m["is_paid"] else "🆓 бесплатный"
        meta = " · ".join(
            x for x in (m.get("format"), m.get("level")) if x
        )

        line = f"• {m['title']} — {tag}"

        if meta:
            line += f", {meta}"

        if m["is_paid"]:
            if m.get("price"):
                line += f", {m['price']}"

            if m.get("topic"):
                line += (
                    f"\n  Польза: системно разобраться "
                    f"в теме «{m['topic']}»"
                )

        elif m.get("description"):
            line += f"\n  {m['description'][:140].strip()}…"

        if m.get("reasons"):
            line += f"\n  Почему: {'; '.join(m['reasons'])}"

        lines.append(line)

    return "\n".join(lines)


def _mat_buttons(mats: list[dict]) -> list[list[dict]]:
    rows = []

    for m in mats:
        if is_url(m.get("url")):
            prefix = "💳 " if m["is_paid"] else "🆓 "

            rows.append([
                {
                    "text": (prefix + m["title"])[:50],
                    "url": link(m["url"]),
                }
            ])

    if mats:
        rows.append([
            {
                "text": "🔔 Напомнить вернуться к первому материалу",
                "callback_data": f"learn:{mats[0]['id']}",
            }
        ])

    return rows


def _no_answer(mats: list[dict]) -> Reply:
    text = (
        "В базе знаний «Альфа-Курса» я не нашёл надёжного ответа "
        "на этот вопрос, и гадать не буду. "
        "Попробуйте уточнить задачу: тема, стадия бизнеса, "
        "режим налогообложения."
    )

    if mats:
        text += (
            "\n\nПо близкой теме могут пригодиться материалы:\n"
            + _mat_lines(mats)
        )

    return text, _mat_buttons(mats) or None


async def _answer(
    user_id: int,
    text: str,
    a: dict,
    profile: dict,
) -> Reply:
    q = a["search_query"]

    chunks = await search.kb(q)
    mats = recs.rank(await search.materials(q), profile)

    best = chunks[0]["score"] if chunks else 0.0

    if best < settings.kb_min_score:
        await db.log_event(
            user_id,
            "no_answer",
            {
                "q": text[:200],
                "best": round(best, 3),
            },
        )
        return _no_answer(mats)

    context = "\n\n".join(
        f"[{i + 1}] {c['title'] or ''}: {c['text']}"
        for i, c in enumerate(chunks[:4])
    )

    prompt = (
        f"Профиль пользователя: {profile_text(profile)}\n"
        f"Вопрос: {text}\n\n"
        f"Фрагменты базы знаний:\n{context}"
    )

    answer = (
        await llm.chat(ANSWER_SYSTEM, prompt, max_tokens=600)
    ).strip()

    if "НЕТ_ОТВЕТА" in answer or not answer:
        await db.log_event(
            user_id,
            "no_answer",
            {
                "q": text[:200],
                "best": round(best, 3),
                "by": "llm",
            },
        )
        return _no_answer(mats)

    out = answer

    if mats:
        out += "\n\n📚 Подходящие материалы:\n" + _mat_lines(mats)

    await db.log_event(
        user_id,
        "answer",
        {
            "q": text[:200],
            "best": round(best, 3),
            "materials": [m["id"] for m in mats],
        },
    )

    return out, _mat_buttons(mats) or None


async def _recommend(
    user_id: int,
    text: str,
    a: dict,
    profile: dict,
) -> Reply:
    query = " ".join(
        part
        for part in (
            a.get("search_query", ""),
            profile.get("area") or "",
            profile.get("goal") or "",
        )
        if part
    ).strip()

    found = await search.materials(query)
    mats = recs.rank(found, profile)

    if not mats:
        await db.log_event(
            user_id,
            "no_recommendations",
            {"q": text[:200]},
        )

        return (
            "Пока не нашёл в каталоге материалов, которые достаточно "
            "точно подходят под вашу задачу. Попробуйте уточнить тему "
            "или описать свою цель подробнее.",
            None,
        )

    out = (
        "Подобрал материалы под вашу задачу:\n\n"
        + _mat_lines(mats)
        + "\n\nСледующий шаг: начните с первого материала."
    )

    if not any(
        profile.get(k)
        for k in ("stage", "area", "goal")
    ):
        out += (
            "\nЕсли расскажете о стадии и сфере бизнеса, "
            "подберу материалы точнее."
        )

    await db.log_event(
        user_id,
        "recommend",
        {
            "q": text[:200],
            "materials": [m["id"] for m in mats],
            "general": False,
        },
    )

    return out, _mat_buttons(mats) or None


async def _reminder(
    user_id: int,
    a: dict,
    profile: dict,
) -> Reply:
    r = a["reminder"]

    if r is None:
        return (
            "Что и когда напомнить? Например: "
            "«Напомни завтра в 10:00 продолжить курс по маркетингу».",
            None,
        )

    tz = reminders.tz_of(profile.get("tz"))

    if r["missing"] or not r["when"]:
        return (
            r["missing"]
            or "Когда напомнить? Например: «завтра в 10:00».",
            None,
        )

    try:
        dt = datetime.fromisoformat(
            r["when"].replace("Z", "+00:00")
        )
    except ValueError:
        return (
            "Не разобрал время. Напишите, например: "
            "«завтра в 10:00» или «каждый понедельник в 9:00».",
            None,
        )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)

    if dt <= datetime.now(timezone.utc):
        return (
            "Это время уже прошло. На когда поставить напоминание?",
            None,
        )

    rule = reminders.rule_from(r["repeat"], r["n"])

    rid = await reminders.create(
        user_id,
        r["text"],
        dt.astimezone(timezone.utc),
        rule,
    )

    if rid is None:
        return (
            f"У вас уже {settings.max_reminders_per_user} "
            "активных напоминаний. "
            "Отключите ненужные в /reminders и повторите.",
            None,
        )

    await db.log_event(
        user_id,
        "reminder_created",
        {"id": rid, "rule": rule},
    )

    local = dt.astimezone(tz)

    return (
        f"Готово. Напомню: «{r['text']}»\n"
        f"Когда: {local:%d.%m.%Y в %H:%M} "
        f"({reminders.human_rule(rule)}).\n"
        "Изменить частоту или отключить можно в /reminders.",
        None,
    )


async def respond(user_id: int, text: str) -> Reply:
    profile = await db.get_profile(user_id)
    history = await db.get_history(user_id, 6)

    a = await analyze.run(text, profile, history)

    await db.update_profile(user_id, **a["profile"])
    profile = await db.get_profile(user_id)

    intent = a["intent"]

    if intent == "reminder":
        CLARIFIED.discard(user_id)
        reply = await _reminder(user_id, a, profile)

    elif (
        a["clarify"]
        and intent in ("question", "recommend")
        and user_id not in CLARIFIED
    ):
        CLARIFIED.add(user_id)
        reply = (a["clarify"], None)

    else:
        CLARIFIED.discard(user_id)

        if intent == "chitchat":
            reply = (
                "Я помогаю предпринимателям: отвечаю на вопросы "
                "по учёту, налогам, маркетингу и продажам, "
                "подбираю материалы «Альфа-Курса» и напоминаю "
                "о важных сроках. Задайте вопрос или напишите /help.",
                None,
            )

        elif intent == "profile":
            reply = (
                "Записал. Теперь буду учитывать это в ответах. "
                "Чем помочь: вопрос по бизнесу, подбор материалов "
                "или напоминание?",
                None,
            )

        elif intent == "recommend":
            reply = await _recommend(
                user_id,
                text,
                a,
                profile,
            )

        else:
            reply = await _answer(
                user_id,
                text,
                a,
                profile,
            )

    await db.save_message(user_id, "user", text)
    await db.save_message(user_id, "assistant", reply[0])

    HISTORY[user_id].append(("user", text[:300]))
    HISTORY[user_id].append(("assistant", reply[0][:300]))

    return reply
