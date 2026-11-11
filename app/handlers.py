import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from . import db, dialog, reminders
from .config import settings

log = logging.getLogger(__name__)
router = Router()

HELP = (
    "Что я умею:\n"
    "• отвечаю на вопросы по базе знаний «Альфа-Курса» (учёт, налоги, маркетинг, продажи);\n"
    "• подбираю бесплатные и платные материалы под вашу задачу;\n"
    "• напоминаю об учёбе и налоговых сроках.\n\n"
    "Команды:\n"
    "/profile — что я знаю о вашем бизнесе\n"
    "/reminders — ваши напоминания (изменить частоту, отключить)\n"
    "/tax — напоминания о налоговых сроках\n"
    "/tz Europe/Moscow — часовой пояс\n\n"
    "Примеры: «Как выбрать систему налогообложения для кофейни?», "
    "«С чего начать, если хочу открыть интернет-магазин?», "
    "«Напоминай каждую неделю в понедельник в 9:00 про курс по маркетингу»."
)


def markup(rows):
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(**b) for b in row] for row in rows])


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


@router.message(CommandStart())
async def cmd_start(m: Message):
    await db.upsert_user(m.from_user.id, m.chat.id)
    rows = [[_btn("Рассказать о бизнесе", "onb:start")], [_btn("Пропустить", "onb:skip")]]
    if settings.miniapp_url:
        await m.answer(
            "Привет! Я помощник «Альфа-Курса»: отвечаю на вопросы по бизнесу, подбираю материалы "
            "и напоминаю о важных сроках.\n\nОткройте учебный кабинет или задайте вопрос прямо здесь.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Открыть учебный кабинет", web_app=WebAppInfo(url=settings.miniapp_url))],
                [InlineKeyboardButton(text="Рассказать о бизнесе", callback_data="onb:start")],
                [InlineKeyboardButton(text="Пропустить", callback_data="onb:skip")],
            ]),
        )
    else:
        await m.answer(
            "Привет! Я помощник «Альфа-Курса»: отвечаю на вопросы по бизнесу, подбираю материалы "
            "и напоминаю о важных сроках.\n\n"
            "Могу задать два коротких вопроса о вашем бизнесе, чтобы подсказывать точнее. Это необязательно.",
            reply_markup=markup(rows),
        )


@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HELP)


@router.message(Command("profile"))
async def cmd_profile(m: Message):
    await db.upsert_user(m.from_user.id, m.chat.id)
    p = await db.get_profile(m.from_user.id)
    await m.answer(f"Что я знаю о вашем бизнесе: {dialog.profile_text(p)}.\n"
                   "Можно просто написать мне новые сведения, я обновлю.")


@router.message(Command("tz"))
async def cmd_tz(m: Message):
    await db.upsert_user(m.from_user.id, m.chat.id)
    name = (m.text or "").split(maxsplit=1)[1:] or [""]
    try:
        ZoneInfo(name[0])
    except Exception:
        await m.answer("Укажите часовой пояс, например: /tz Europe/Moscow или /tz Asia/Yekaterinburg")
        return
    await db.update_profile(m.from_user.id, tz=name[0])
    await m.answer(f"Часовой пояс: {name[0]}.")


@router.message(Command("reminders"))
async def cmd_reminders(m: Message):
    await db.upsert_user(m.from_user.id, m.chat.id)
    uid = m.from_user.id
    tz = (await db.get_profile(uid)).get("tz")
    rows = await db.pool().fetch(
        "SELECT id, text, next_at, repeat_rule FROM reminders WHERE user_id=$1 AND active ORDER BY next_at",
        uid,
    )
    if not rows:
        await m.answer("Активных напоминаний нет. Напишите, например: "
                       "«Напомни завтра в 10:00 продолжить курс по маркетингу» или используйте /tax.")
        return
    for r in rows:
        await m.answer(reminders.describe(r, tz), reply_markup=reminders.keyboard(r))


@router.message(Command("tax"))
async def cmd_tax(m: Message):
    await db.upsert_user(m.from_user.id, m.chat.id)
    rows = [[_btn(label, f"tax:set:{key}")] for key, label in reminders.TAX_LABELS.items()]
    await m.answer(
        "Для какого режима напоминать о сроках уплаты? Напомню за 3 дня до срока. "
        "Календарь примерный, точные даты сверяйте с ФНС.",
        reply_markup=markup(rows),
    )


# ---------- онбординг ----------

@router.callback_query(F.data.startswith("onb:"))
async def cb_onboarding(c: CallbackQuery):
    uid = c.from_user.id
    await db.upsert_user(uid, c.message.chat.id)
    parts = c.data.split(":")
    step = parts[1]
    await c.message.edit_reply_markup(reply_markup=None)

    if step == "skip":
        await c.message.answer("Хорошо. Просто задайте вопрос или напишите /help.")
    elif step == "start":
        await c.message.answer("На какой стадии ваш бизнес?", reply_markup=markup([
            [_btn("Только думаю об открытии", "onb:stage:idea")],
            [_btn("Запускаю", "onb:stage:launch")],
            [_btn("Работает", "onb:stage:running")],
            [_btn("Развиваю, масштабирую", "onb:stage:growing")],
            [_btn("Пропустить", "onb:skip")],
        ]))
    elif step == "stage":
        await db.update_profile(uid, stage=parts[2])
        if parts[2] == "idea":
            await c.message.answer("Принято. Спросите, например: «С чего начать, если хочу открыть кофейню?»")
        else:
            await c.message.answer("Какой у вас режим налогообложения?", reply_markup=markup([
                [_btn("УСН", "onb:regime:usn"), _btn("Самозанятый (НПД)", "onb:regime:npd")],
                [_btn("ОСНО", "onb:regime:osno"), _btn("Патент", "onb:regime:patent")],
                [_btn("Пока не знаю", "onb:skip")],
            ]))
    elif step == "regime":
        await db.update_profile(uid, tax_regime=parts[2])
        rows = []
        if parts[2] in ("usn", "npd"):
            rows = [[_btn("🔔 Напоминать о налоговых сроках", f"tax:set:{parts[2]}")]]
        await c.message.answer(
            "Готово, буду учитывать это в ответах. Спросите, например: «Какие расходы можно учесть на УСН?»",
            reply_markup=markup(rows),
        )
    await c.answer()


# ---------- напоминания ----------

@router.callback_query(F.data.startswith("tax:set:"))
async def cb_tax(c: CallbackQuery):
    uid = c.from_user.id
    regime = c.data.split(":")[2]
    await db.upsert_user(uid, c.message.chat.id)
    tz = (await db.get_profile(uid)).get("tz")
    n = await reminders.create_tax(uid, regime, tz)
    await c.message.edit_reply_markup(reply_markup=None)
    await c.message.answer(
        f"Включил напоминания о сроках ({reminders.TAX_LABELS.get(regime, regime)}): "
        f"напомню за 3 дня до каждого срока ({n} шт.). Управлять ими можно в /reminders. "
        "Даты примерные: сверяйте с ФНС."
    )
    await c.answer()


@router.callback_query(F.data.startswith("rem:"))
async def cb_reminder(c: CallbackQuery):
    _, action, rid = c.data.split(":")
    msg = await reminders.apply_action(c.from_user.id, int(rid), action)
    await c.message.edit_reply_markup(reply_markup=None)
    await c.message.answer(msg)
    await c.answer()


@router.callback_query(F.data.startswith("learn:"))
async def cb_learn(c: CallbackQuery):
    uid = c.from_user.id
    await db.upsert_user(uid, c.message.chat.id)
    mid = c.data.split(":", 1)[1]
    mat = await db.pool().fetchrow("SELECT title FROM kb.materials WHERE id=$1::uuid", mid)
    if not mat:
        await c.answer("Материал не найден")
        return
    tz = reminders.tz_of((await db.get_profile(uid)).get("tz"))
    when = (datetime.now(tz) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    rid = await reminders.create(uid, f"Вернуться к материалу «{mat['title']}»", when.astimezone(timezone.utc))
    await db.log_event(uid, "learn_reminder", {"material_id": mid})
    await c.answer("Напомню через 2 дня" if rid else "Слишком много напоминаний, см. /reminders", show_alert=False)


# ---------- свободный текст ----------

@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    await db.upsert_user(m.from_user.id, m.chat.id)
    await m.bot.send_chat_action(m.chat.id, "typing")
    try:
        text, rows = await dialog.respond(m.from_user.id, m.text)
    except Exception:
        log.exception("Ошибка обработки сообщения")
        text, rows = ("Что-то пошло не так на моей стороне. Повторите вопрос чуть позже "
                      "или переформулируйте его.", None)
    await m.answer(text[:4000], reply_markup=markup(rows))
