"""Логика диалога Skilly / Альфа-Курс."""

import html
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from . import analyze, db, llm, recs, reminders, search
from .config import settings
from .util import is_url, link

log = logging.getLogger(__name__)
Reply = tuple[str, list[list[dict]] | None]

STAGE_RU = {"idea": "думает об открытии", "launch": "запускает дело", "running": "бизнес работает", "growing": "развивает бизнес"}
REGIME_RU = {"usn": "УСН", "npd": "НПД (самозанятый)", "osno": "ОСНО", "patent": "патент", "other": "другой"}
EXPERIENCE_RU = {"novice": "начинающий", "some": "есть некоторый опыт", "experienced": "опытный"}

ANSWER_SYSTEM = """
Ты — практичный ИИ-помощник платформы «Альфа-Курс» для предпринимателей.
Отвечай строго по вопросу пользователя и по переданным фрагментам базы знаний.
Не выдумывай факты, цифры, ставки, сроки, нормы закона и ссылки.
Пиши по-русски, просто и естественно. Сначала дай прямой ответ.
Обычно достаточно 2–5 коротких предложений.
Не повторяй вопрос, не начинай с общих фраз, не добавляй обязательные заголовки,
чек-листы, выводы или фразу «Следующий шаг».
Список используй только если он действительно упрощает инструкцию.
Не используй эмодзи, декоративные символы, HTML, Markdown, таблицы и формулы.
Не добавляй ссылки на материалы: они будут показаны отдельно.
Учитывай профиль только если он относится к вопросу.
Если контекст не отвечает на вопрос, верни ровно НЕТ_ОТВЕТА.
Если важной детали не хватает, задай один короткий уточняющий вопрос.
Для налоговых, юридических и финансовых тем не делай категоричных выводов,
если переданные фрагменты не подтверждают их явно.
"""

class _PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        value = data.strip()
        if value:
            self.parts.append(value)

def plain_text(value, limit=None):
    if value is None:
        return ""
    raw = html.unescape(str(value))
    parser = _PlainTextParser()
    try:
        parser.feed(raw)
        parser.close()
        result = " ".join(parser.parts)
    except Exception:
        result = re.sub(r"<[^>]*>", " ", raw)
    result = re.sub(r"\s+", " ", html.unescape(result)).strip()
    return result[:limit].rstrip() if limit else result

def clean_answer(value):
    value = plain_text(value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", value)
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
    value = re.sub(r"__(.*?)__", r"\1", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"[📚🔗🆓💳⚠️🚀👉✅❗️⭐️]+", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()

def profile_text(profile):
    parts = []
    for key, label, mapping in (
        ("stage", "стадия", STAGE_RU),
        ("area", "сфера", {}),
        ("tax_regime", "налогообложение", REGIME_RU),
        ("experience", "опыт", EXPERIENCE_RU),
        ("goal", "задача", {}),
    ):
        value = profile.get(key)
        if value:
            parts.append(f"{label}: {mapping.get(value, value)}")
    return "; ".join(parts) if parts else "неизвестен"

def _eligible_linked_materials(mats):
    result, seen = [], set()
    for material in mats:
        url = material.get("url")
        title = plain_text(material.get("title"), 120)
        if not title or not is_url(url):
            continue
        key = str(material.get("id") or url)
        if key in seen:
            continue
        seen.add(key)
        item = dict(material)
        item["title"] = title
        item["description"] = plain_text(item.get("description"), 240)
        result.append(item)
        if len(result) == 2:
            break
    return result

def _mat_lines(mats):
    return "\n\n".join(
        f"{plain_text(m.get('title'), 120)}\n{m['url']}"
        for m in _eligible_linked_materials(mats)
    )

def _mat_buttons(mats):
    rows = []
    for material in _eligible_linked_materials(mats):
        rows.append([{"text": plain_text(material.get("title"), 45) or "Открыть материал", "url": link(material["url"])}])
    return rows or None

def _no_answer():
    return ("В доступной базе знаний нет точной информации для ответа. Уточните, пожалуйста, какой именно аспект вас интересует.", None)

def _no_recommendations():
    return ("Не нашёл в каталоге материалов, которые достаточно точно подходят под эту задачу.", None)

async def _answer(user_id, text, analysis, profile):
    query = (analysis.get("search_query") or text).strip()
    try:
        chunks = await search.kb(query, limit=8)
    except Exception:
        log.exception("Knowledge search failed for user %s", user_id)
        await db.log_event(user_id, "search_error", {"query": query[:200]})
        return _no_answer()

    best_score = float(chunks[0].get("score") or 0) if chunks else 0.0
    if not chunks or best_score < settings.kb_min_score:
        await db.log_event(user_id, "no_answer", {"query": text[:200], "search_query": query[:200], "best_score": round(best_score, 4)})
        return _no_answer()

    context_parts = []
    for index, chunk in enumerate(chunks[:5], start=1):
        title = plain_text(chunk.get("title")) or "Без названия"
        content = plain_text(chunk.get("text"))
        if content:
            context_parts.append(f"[Фрагмент {index}]\nМатериал: {title}\nТекст: {content[:3500]}")
    if not context_parts:
        return _no_answer()

    prompt = (
        f"Профиль пользователя: {profile_text(profile)}\n\n"
        f"Вопрос пользователя: {text[:2000]}\n\n"
        f"Фрагменты базы знаний:\n{chr(10).join(context_parts)}\n\n"
        "Дай прямой и короткий ответ именно на вопрос. Используй только факты "
        "из фрагментов. Не добавляй общие советы, которых пользователь не просил. "
        "Не добавляй следующий шаг, вступление или заключение. Если фрагменты "
        "не отвечают на вопрос, верни НЕТ_ОТВЕТА."
    )
    try:
        answer = await llm.chat(ANSWER_SYSTEM, prompt, temperature=0.1, max_tokens=500)
    except Exception:
        log.exception("Answer generation failed for user %s", user_id)
        await db.log_event(user_id, "answer_generation_error", {"query": text[:200]})
        return _no_answer()

    answer = clean_answer(answer or "")
    if not answer or answer.upper().strip(" .!`") == "НЕТ_ОТВЕТА":
        await db.log_event(user_id, "no_answer", {"query": text[:200], "best_score": round(best_score, 4), "reason": "llm_no_answer"})
        return _no_answer()

    materials = []
    try:
        found = await search.materials(query, limit=20)
        materials = _eligible_linked_materials(recs.rank(found, profile, top=2))
    except Exception:
        log.exception("Material search failed for user %s", user_id)

    output = answer
    if materials:
        output += "\n\nМатериалы:\n" + _mat_lines(materials)
    await db.log_event(user_id, "answer", {"query": text[:200], "best_score": round(best_score, 4), "chunks": len(context_parts), "materials": len(materials)})
    return output, _mat_buttons(materials)

async def _recommend(user_id, text, analysis, profile):
    query = (analysis.get("search_query") or "").strip()
    if not query:
        return _no_recommendations()
    try:
        found = await search.materials(query, limit=20)
        materials = _eligible_linked_materials(recs.rank(found, profile, top=2))
    except Exception:
        log.exception("Recommendation search failed for user %s", user_id)
        await db.log_event(user_id, "recommendation_error", {"query": text[:200]})
        return _no_recommendations()
    if not materials:
        await db.log_event(user_id, "no_recommendations", {"query": text[:200], "search_query": query[:200]})
        return _no_recommendations()
    output = "Нашёл подходящие материалы:\n\n" + _mat_lines(materials)
    await db.log_event(user_id, "recommend", {"query": text[:200], "search_query": query[:200], "materials": [m["id"] for m in materials if m.get("id")]})
    return output, _mat_buttons(materials)

async def _reminder(user_id, analysis, profile):
    reminder = analysis.get("reminder")
    if reminder is None:
        return "Что именно нужно напомнить и когда?", None
    tz = reminders.tz_of(profile.get("tz"))
    if reminder.get("missing") or not reminder.get("when"):
        return reminder.get("missing") or "Когда напомнить?", None
    try:
        dt = datetime.fromisoformat(reminder["when"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "Не удалось распознать дату и время. Укажите их, например: завтра в 10:00.", None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    if dt <= datetime.now(timezone.utc):
        return "Указанное время уже прошло. На когда поставить напоминание?", None
    rule = reminders.rule_from(reminder.get("repeat", "none"), reminder.get("n"))
    reminder_id = await reminders.create(user_id, reminder.get("text") or "Напоминание", dt.astimezone(timezone.utc), rule)
    if reminder_id is None:
        return f"У вас уже {settings.max_reminders_per_user} активных напоминаний. Отключите ненужное и повторите.", None
    await db.log_event(user_id, "reminder_created", {"id": reminder_id, "rule": rule})
    local_dt = dt.astimezone(tz)
    return (f"Напоминание создано: {reminder.get('text') or 'Напоминание'}.\nКогда: {local_dt:%d.%m.%Y в %H:%M} ({reminders.human_rule(rule)}).", None)

async def respond(user_id, text):
    text = (text or "").strip()
    if not text:
        return "Напишите вопрос или опишите задачу.", None
    profile = await db.get_profile(user_id)
    history = await db.get_history(user_id, 6)
    analysis = await analyze.run(text, profile, history)
    await db.update_profile(user_id, **analysis["profile"])
    profile = await db.get_profile(user_id)

    intent = analysis["intent"]
    clarify = analysis.get("clarify")
    if intent == "reminder":
        reply = await _reminder(user_id, analysis, profile)
    elif intent == "chitchat":
        reply = ("Я помогаю предпринимателям разбираться с вопросами бизнеса и находить полезные материалы. Напишите, с какой задачей помочь.", None)
    elif intent == "profile":
        reply = ("Информацию сохранил и буду учитывать её в следующих ответах.", None)
    elif clarify:
        reply = (plain_text(clarify), None)
    elif intent == "recommend":
        reply = await _recommend(user_id, text, analysis, profile)
    else:
        reply = await _answer(user_id, text, analysis, profile)

    await db.save_message(user_id, "user", text)
    await db.save_message(user_id, "assistant", reply[0])
    return reply
