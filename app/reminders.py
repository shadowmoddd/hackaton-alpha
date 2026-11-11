"""Напоминания: разовые и повторяющиеся, налоговые сроки, планировщик.

Защита от навязчивости:
  * «тихие часы» (QUIET_START..QUIET_END по часовому поясу пользователя): сообщения переносятся на утро;
  * не более MAX_REMINDERS_PER_DAY сообщений-напоминаний в сутки на пользователя;
  * не более MAX_REMINDERS_PER_USER активных пользовательских напоминаний;
  * в каждом напоминании кнопки «Отложить», «Реже/Чаще», «Отключить».

Налоговый календарь — ПРИМЕРНЫЙ (прототип): сроки сдвигаются при выходных и могут
меняться, их нужно сверять с актуальными данными ФНС.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dateutil.relativedelta import relativedelta

from . import db
from .config import settings
from .util import is_url, link

log = logging.getLogger(__name__)

# (периодичность, месяц, день срока, описание)
TAX_RULES = {
    "usn": [
        ("yearly", 3, 28, "УСН: годовой налог (для организаций)"),
        ("yearly", 4, 28, "УСН: аванс за 1 квартал (ИП — также годовой налог за прошлый год)"),
        ("yearly", 7, 28, "УСН: аванс за 2 квартал"),
        ("yearly", 10, 28, "УСН: аванс за 3 квартал"),
    ],
    "npd": [
        ("monthly", None, 28, "НПД (самозанятые): налог за прошлый месяц"),
    ],
    "ip_fixed": [
        ("yearly", 12, 31, "ИП: фиксированные страховые взносы за год"),
        ("yearly", 7, 1, "ИП: взнос 1% с дохода свыше 300 тыс. ₽ за прошлый год"),
    ],
}
TAX_LABELS = {"usn": "УСН", "npd": "самозанятый (НПД)", "ip_fixed": "ИП: страховые взносы"}
REMIND_DAYS_BEFORE = 3


def tz_of(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or settings.default_tz)
    except Exception:
        return ZoneInfo(settings.default_tz)


def rule_from(repeat: str, n: int | None) -> str | None:
    if repeat == "daily":
        return "days:1"
    if repeat == "weekly":
        return "days:7"
    if repeat == "monthly":
        return "monthly"
    if repeat == "every_n_days" and n:
        return f"days:{max(1, min(int(n), 365))}"
    return None


def human_rule(rule: str | None) -> str:
    if not rule:
        return "разово"
    if rule == "monthly":
        return "каждый месяц"
    if rule == "yearly":
        return "раз в год"
    n = int(rule.split(":")[1])
    if n == 1:
        return "каждый день"
    if n == 7:
        return "каждую неделю"
    return f"каждые {n} дн."


def advance(dt: datetime, rule: str) -> datetime:
    if rule == "monthly":
        return dt + relativedelta(months=1)
    if rule == "yearly":
        return dt + relativedelta(years=1)
    return dt + timedelta(days=int(rule.split(":")[1]))


async def create(user_id: int, text: str, next_at: datetime, rule: str | None = None,
                 tax_key: str | None = None) -> int | None:
    if tax_key is None:
        n = await db.pool().fetchval(
            "SELECT count(*) FROM reminders WHERE user_id=$1 AND active AND tax_key IS NULL", user_id
        )
        if n >= settings.max_reminders_per_user:
            return None
    return await db.pool().fetchval(
        "INSERT INTO reminders(user_id, text, next_at, repeat_rule, tax_key) "
        "VALUES($1,$2,$3,$4,$5) RETURNING id",
        user_id, text[:300], next_at, rule, tax_key,
    )


async def create_tax(user_id: int, regime: str, tz_name: str | None) -> int:
    rules = TAX_RULES.get(regime)
    if not rules:
        return 0
    now = datetime.now(tz_of(tz_name))
    await db.pool().execute(
        "DELETE FROM reminders WHERE user_id=$1 AND tax_key LIKE $2", user_id, f"{regime}:%"
    )
    for i, (kind, month, day, title) in enumerate(rules):
        base = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if kind == "monthly":
            d = base.replace(day=day) - timedelta(days=REMIND_DAYS_BEFORE)
            if d <= now:
                d += relativedelta(months=1)
            rule, when = "monthly", f"{day}-го числа"
        else:
            d = base.replace(month=month, day=day) - timedelta(days=REMIND_DAYS_BEFORE)
            if d <= now:
                d += relativedelta(years=1)
            rule, when = "yearly", f"{day:02d}.{month:02d}"
        await create(
            user_id, f"Через {REMIND_DAYS_BEFORE} дня срок ({when}): {title}",
            d.astimezone(timezone.utc), rule, f"{regime}:{i}",
        )
    return len(rules)


def describe(r, tz_name: str | None) -> str:
    local = r["next_at"].astimezone(tz_of(tz_name))
    return (
        f"🔔 {r['text']}\n"
        f"Следующее: {local:%d.%m.%Y %H:%M}\n"
        f"Повтор: {human_rule(r['repeat_rule'])}"
    )


def keyboard(r) -> InlineKeyboardMarkup:
    rid = r["id"]
    rows = [[
        InlineKeyboardButton(text="⏰ Отложить на день", callback_data=f"rem:snooze:{rid}"),
        InlineKeyboardButton(text="🔕 Отключить", callback_data=f"rem:off:{rid}"),
    ]]
    rule = r["repeat_rule"]
    if rule and rule.startswith("days:"):
        rows.append([
            InlineKeyboardButton(text="Реже", callback_data=f"rem:less:{rid}"),
            InlineKeyboardButton(text="Чаще", callback_data=f"rem:more:{rid}"),
        ])
    if is_url(settings.platform_url):
        rows.append([InlineKeyboardButton(text="📚 Открыть Альфа-Курс", url=link(settings.platform_url))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def apply_action(user_id: int, rid: int, action: str) -> str:
    pool = db.pool()
    r = await pool.fetchrow("SELECT * FROM reminders WHERE id=$1 AND user_id=$2", rid, user_id)
    if not r:
        return "Напоминание не найдено."
    if action == "off":
        await pool.execute("UPDATE reminders SET active=false WHERE id=$1", rid)
        return "Напоминание отключено. Чтобы создать новое, просто напишите мне."
    if action == "del":
        await pool.execute("DELETE FROM reminders WHERE id=$1", rid)
        return "Напоминание удалено."
    if action == "snooze":
        later = datetime.now(timezone.utc) + timedelta(days=1)
        await pool.execute("UPDATE reminders SET active=true, next_at=$2 WHERE id=$1", rid, later)
        return "Напомню завтра."
    if action in ("less", "more"):
        rule = r["repeat_rule"]
        if not rule or not rule.startswith("days:"):
            return "Частоту этого напоминания изменить нельзя, но его можно отключить."
        n = int(rule.split(":")[1])
        n = min(n * 2, 60) if action == "less" else max(n // 2, 1)
        await pool.execute("UPDATE reminders SET repeat_rule=$2 WHERE id=$1", rid, f"days:{n}")
        return f"Теперь напоминаю {human_rule(f'days:{n}')}."
    return "Неизвестное действие."


def _is_quiet(local: datetime) -> bool:
    return local.hour >= settings.quiet_start or local.hour < settings.quiet_end


def _quiet_end(local: datetime) -> datetime:
    end = local.replace(hour=settings.quiet_end, minute=0, second=0, microsecond=0)
    if local.hour >= settings.quiet_start:
        end += timedelta(days=1)
    return end.astimezone(timezone.utc)


async def _tick(bot) -> None:
    now = datetime.now(timezone.utc)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT r.*, u.chat_id, p.tz FROM reminders r "
                "JOIN users u ON u.id = r.user_id "
                "LEFT JOIN profiles p ON p.user_id = r.user_id "
                "WHERE r.active AND r.next_at <= $1 "
                "ORDER BY r.next_at LIMIT 50 FOR UPDATE OF r SKIP LOCKED",
                now,
            )
            for r in rows:
                local = now.astimezone(tz_of(r["tz"]))
                if _is_quiet(local):
                    await conn.execute("UPDATE reminders SET next_at=$2 WHERE id=$1", r["id"], _quiet_end(local))
                    continue
                day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
                sent_today = await conn.fetchval(
                    "SELECT count(*) FROM events WHERE user_id=$1 AND type='reminder_sent' AND created_at >= $2",
                    r["user_id"], day_start,
                )
                if sent_today >= settings.max_reminders_per_day:
                    tomorrow = (day_start + timedelta(days=1)).replace(hour=settings.quiet_end)
                    await conn.execute("UPDATE reminders SET next_at=$2 WHERE id=$1",
                                       r["id"], tomorrow.astimezone(timezone.utc))
                    continue
                try:
                    await bot.send_message(r["chat_id"], f"🔔 Напоминание: {r['text']}",
                                           reply_markup=keyboard(r))
                except TelegramForbiddenError:
                    await conn.execute("UPDATE reminders SET active=false WHERE user_id=$1", r["user_id"])
                    continue
                except Exception:
                    log.exception("Не удалось отправить напоминание %s", r["id"])
                    continue
                await conn.execute(
                    "INSERT INTO events(user_id, type, payload) VALUES($1,'reminder_sent',$2::jsonb)",
                    r["user_id"], json.dumps({"reminder_id": r["id"]}),
                )
                if r["repeat_rule"]:
                    nxt = advance(r["next_at"], r["repeat_rule"])
                    while nxt <= now:
                        nxt = advance(nxt, r["repeat_rule"])
                    await conn.execute("UPDATE reminders SET next_at=$2 WHERE id=$1", r["id"], nxt)
                else:
                    await conn.execute("UPDATE reminders SET active=false WHERE id=$1", r["id"])


async def loop(bot) -> None:
    while True:
        try:
            await _tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка цикла напоминаний")
        await asyncio.sleep(30)
