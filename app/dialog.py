
"""Логика диалога Skilly / Альфа-Курс."""

import logging
from datetime import datetime, timezone

from . import analyze, db, llm, recs, reminders, search
from .config import settings
from .util import is_url, link


log = logging.getLogger(__name__)

Reply = tuple[str, list[list[dict]] | None]


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

EXPERIENCE_RU = {
    "novice": "начинающий",
    "some": "есть некоторый опыт",
    "experienced": "опытный",
}


ANSWER_SYSTEM = """
Ты — образовательный ИИ-помощник платформы «Альфа-Курс»
для начинающих предпринимателей и владельцев малого бизнеса.

Отвечай на вопрос пользователя ТОЛЬКО на основании переданных фрагментов
базы знаний.

Правила:
1. Не используй знания из собственной памяти вместо базы знаний.
2. Не выдумывай факты, цифры, суммы, сроки, ставки и требования закона.
3. Если фрагменты не содержат ответа, верни ровно НЕТ_ОТВЕТА.
4. Не делай выводов, которые не подтверждаются фрагментами.
5. Отвечай непосредственно на вопрос, а не пересказывай все фрагменты.
6. Пиши по-русски, понятно и без канцелярита.
7. Обычно достаточно 3–6 предложений.
8. Если вопрос предполагает инструкцию, используй короткие шаги.
9. Не добавляй ссылки и не рекламируй курсы.
10. Учитывай профиль пользователя, если он относится к вопросу.
11. Не повторяй вопрос пользователя.
12. В конце добавь одну строку:
Следующий шаг: конкретное действие пользователя.

Если информации недостаточно, верни НЕТ_ОТВЕТА.
"""


def profile_text(profile: dict) -> str:
    parts = []

    stage = profile.get("stage")
    if stage:
        parts.append(f"стадия: {STAGE_RU.get(stage, stage)}")

    area = profile.get("area")
    if area:
        parts.append(f"сфера: {area}")

    regime = profile.get("tax_regime")
    if regime:
        parts.append(f"налогообложение: {REGIME_RU.get(regime, regime)}")

    experience = profile.get("experience")
    if experience:
        parts.append(
            f"опыт: {EXPERIENCE_RU.get(experience, experience)}"
        )

    goal = profile.get("goal")
    if goal:
        parts.append(f"задача: {goal}")

    return "; ".join(parts) if parts else "неизвестен"


def _mat_lines(mats: list[dict]) -> str:
    lines = []

    for material in mats:
        title = material.get("title") or "Материал без названия"
        is_paid = bool(material.get("is_paid"))

        tag = "💳 платный" if is_paid else "🆓 бесплатный"

        meta = [
            value
            for value in (
                material.get("format"),
                material.get("level"),
            )
            if value
        ]

        line = f"• {title} — {tag}"

        if meta:
            line += ", " + " · ".join(meta)

        if is_paid and material.get("price"):
            line += f", {material['price']}"

        description = material.get("description")
        if description and not is_paid:
            line += f"\n  {str(description).strip()[:180]}"

        reasons = material.get("reasons")
        if reasons:
            line += "\n  Почему подходит: " + "; ".join(reasons)

        lines.append(line)

    return "\n".join(lines)


def _mat_buttons(mats: list[dict]) -> list[list[dict]]:
    rows = []

    for material in mats:
        url = material.get("url")
        title = material.get("title") or "Открыть материал"

        if is_url(url):
            prefix = "💳 " if material.get("is_paid") else "🆓 "

            rows.append([
                {
                    "text": (prefix + title)[:50],
                    "url": link(url),
                }
            ])

    # Кнопку напоминания добавляем только при наличии корректного ID.
    if mats and mats[0].get("id"):
        rows.append([
            {
                "text": "🔔 Напомнить вернуться к первому материалу",
                "callback_data": f"learn:{mats[0]['id']}",
            }
        ])

    return rows


def _no_answer() -> Reply:
    return (
        "Я пока не нашёл в базе знаний достаточно точной информации, "
        "чтобы уверенно ответить на этот вопрос. Не хочу вводить вас "
        "в заблуждение.\n\n"
        "Попробуйте уточнить тему или сформулировать вопрос конкретнее. "
        "Например: «Как выбрать систему налогообложения для самозанятого?»",
        None,
    )


def _no_recommendations() -> Reply:
    return (
        "Пока не нашёл в каталоге материалов достаточно точных совпадений "
        "с вашей задачей.\n\n"
        "Попробуйте описать, что именно хотите сделать или чему научиться. "
        "Например: «Хочу найти первых клиентов для кофейни».",
        None,
    )


async def _answer(
    user_id: int,
    text: str,
    analysis: dict,
    profile: dict,
) -> Reply:
    query = analysis.get("search_query") or text

    try:
        chunks = await search.kb(query, limit=6)
    except Exception:
        log.exception("Knowledge search failed for user %s", user_id)
        await db.log_event(
            user_id,
            "search_error",
            {"query": query[:200]},
        )
        return _no_answer()

    best_score = (
        float(chunks[0].get("score") or 0)
        if chunks
        else 0.0
    )

    if not chunks or best_score < settings.kb_min_score:
        await db.log_event(
            user_id,
            "no_answer",
            {
                "query": text[:200],
                "search_query": query[:200],
                "best_score": round(best_score, 4),
            },
        )
        return _no_answer()

    context_parts = []

    for index, chunk in enumerate(chunks[:4], start=1):
        title = chunk.get("title") or "Без названия"
        content = chunk.get("text") or ""

        if content.strip():
            context_parts.append(
                f"[Фрагмент {index}]\n"
                f"Материал: {title}\n"
                f"Текст: {content[:3500]}"
            )

    if not context_parts:
        return _no_answer()

    context = "\n\n".join(context_parts)

    prompt = (
        f"Профиль пользователя: {profile_text(profile)}\n\n"
        f"Вопрос пользователя: {text[:2000]}\n\n"
        f"Фрагменты базы знаний:\n{context}\n\n"
        "Ответь на вопрос строго по этим фрагментам."
    )

    try:
        answer = await llm.chat(
            ANSWER_SYSTEM,
            prompt,
            temperature=0.1,
            max_tokens=700,
        )
    except Exception:
        log.exception("Answer generation failed for user %s", user_id)
        await db.log_event(
            user_id,
            "answer_generation_error",
            {"query": text[:200]},
        )
        return _no_answer()

    answer = (answer or "").strip()

    # Проверяем точный маркер, а не любое вхождение фразы.
    if not answer or answer.strip().upper() == "НЕТ_ОТВЕТА":
        await db.log_event(
            user_id,
            "no_answer",
            {
                "query": text[:200],
                "best_score": round(best_score, 4),
                "reason": "llm_no_answer",
            },
        )
        return _no_answer()

    await db.log_event(
        user_id,
        "answer",
        {
            "query": text[:200],
            "best_score": round(best_score, 4),
            "chunks": len(context_parts),
        },
    )

    # В обычном ответе не добавляем материалы автоматически.
    # Пользователь может отдельно попросить их подобрать.
    return answer, None


async def _recommend(
    user_id: int,
    text: str,
    analysis: dict,
    profile: dict,
) -> Reply:
    query = (analysis.get("search_query") or "").strip()

    if not query:
        return _no_recommendations()

    try:
        found = await search.materials(query, limit=20)
        materials = recs.rank(found, profile, top=3)
    except Exception:
        log.exception("Recommendation search failed for user %s", user_id)
        await db.log_event(
            user_id,
            "recommendation_error",
            {"query": text[:200]},
        )
        return _no_recommendations()

    if not materials:
        await db.log_event(
            user_id,
            "no_recommendations",
            {
                "query": text[:200],
                "search_query": query[:200],
            },
        )
        return _no_recommendations()

    output = (
        "Подобрал материалы под вашу задачу:\n\n"
        + _mat_lines(materials)
        + "\n\nСледующий шаг: начните с первого материала."
    )

    if not any(
        profile.get(key)
        for key in ("stage", "area", "goal", "experience")
    ):
        output += (
            "\n\nЕсли расскажете о стадии и сфере бизнеса, "
            "смогу точнее учитывать ваш контекст в следующих рекомендациях."
        )

    await db.log_event(
        user_id,
        "recommend",
        {
            "query": text[:200],
            "search_query": query[:200],
            "materials": [
                material["id"]
                for material in materials
                if material.get("id")
            ],
        },
    )

    return output, _mat_buttons(materials) or None


async def _reminder(
    user_id: int,
    analysis: dict,
    profile: dict,
) -> Reply:
    reminder = analysis.get("reminder")

    if reminder is None:
        return (
            "Что именно нужно напомнить? Например: "
            "«Напомни завтра в 10:00 проверить рекламу».",
            None,
        )

    tz = reminders.tz_of(profile.get("tz"))

    if reminder.get("missing") or not reminder.get("when"):
        return (
            reminder.get("missing") or "Когда напомнить? Например: завтра в 10:00.",
            None,
        )

    try:
        dt = datetime.fromisoformat(
            reminder["when"].replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return (
            "Не удалось разобрать дату и время. Напишите, например: "
            "«завтра в 10:00».",
            None,
        )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)

    if dt <= datetime.now(timezone.utc):
        return (
            "Это время уже прошло. На когда поставить напоминание?",
            None,
        )

    rule = reminders.rule_from(
        reminder.get("repeat", "none"),
        reminder.get("n"),
    )

    reminder_id = await reminders.create(
        user_id,
        reminder.get("text") or "Напоминание",
        dt.astimezone(timezone.utc),
        rule,
    )

    if reminder_id is None:
        return (
            f"У вас уже {settings.max_reminders_per_user} активных "
            "напоминаний. Отключите ненужные в /reminders и повторите.",
            None,
        )

    await db.log_event(
        user_id,
        "reminder_created",
        {
            "id": reminder_id,
            "rule": rule,
        },
    )

    local_dt = dt.astimezone(tz)

    return (
        f"Готово. Напомню: «{reminder.get('text') or 'Напоминание'}»\n"
        f"Когда: {local_dt:%d.%m.%Y в %H:%M} "
        f"({reminders.human_rule(rule)}).\n"
        "Изменить частоту или отключить напоминание можно в /reminders.",
        None,
    )


async def respond(user_id: int, text: str) -> Reply:
    """Обрабатывает сообщение пользователя и возвращает текст с кнопками."""

    text = (text or "").strip()

    if not text:
        return "Напишите вопрос или опишите задачу — я постараюсь помочь.", None

    profile = await db.get_profile(user_id)
    history = await db.get_history(user_id, 6)

    analysis = await analyze.run(
        text,
        profile,
        history,
    )

    # db.update_profile игнорирует None, поэтому существующие значения
    # не стираются, если пользователь не сообщил новое значение.
    await db.update_profile(
        user_id,
        **analysis["profile"],
    )

    profile = await db.get_profile(user_id)

    intent = analysis["intent"]
    clarify = analysis.get("clarify")

    if intent == "reminder":
        reply = await _reminder(
            user_id,
            analysis,
            profile,
        )

    elif intent == "chitchat":
        reply = (
            "Я — Skilly, помощник для предпринимателей. "
            "Могу отвечать на вопросы по маркетингу, продажам, "
            "налогам и организации бизнеса, подбирать материалы "
            "«Альфа-Курса» и создавать напоминания.\n\n"
            "Напишите, с какой задачей хотите разобраться.",
            None,
        )

    elif intent == "profile":
        reply = (
            "Записал. Буду учитывать эту информацию при ответах "
            "и подборе материалов.\n\n"
            "Можете задать вопрос или попросить подобрать материалы "
            "под конкретную задачу.",
            None,
        )

    elif clarify:
        # Сохраняем уточнение в истории. Следующее сообщение пользователя
        # будет передано анализатору вместе с этой репликой.
        reply = (clarify, None)

    elif intent == "recommend":
        reply = await _recommend(
            user_id,
            text,
            analysis,
            profile,
        )

    else:
        reply = await _answer(
            user_id,
            text,
            analysis,
            profile,
        )

    await db.save_message(user_id, "user", text)
    await db.save_message(user_id, "assistant", reply[0])

    return reply
