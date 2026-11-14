"""Планировщик пользовательских и налоговых напоминаний."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dateutil.relativedelta import relativedelta

from . import db
from .config import settings
from .util import is_url, link

log = logging.getLogger(__name__)

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

TAX_LABELS = {
    "usn": "УСН",
    "npd": "самозанятый (НПД)",
    "ip_fixed": "ИП: страховые взносы",
}

REMIND_DAYS_BEFORE = 3
CHECK_INTERVAL_SECONDS = 30
MAX_BATCH_SIZE = 50


def tz_of(name: str | None) -> ZoneInfo:
    """Возвращает часовой пояс пользователя или часовой пояс по умолчанию."""
    fallback = settings.default_tz

    try:
        return ZoneInfo(name or fallback)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        log.warning("Неизвестный часовой пояс %r. Используется %s", name, fallback)
        return ZoneInfo(fallback)


def rule_from(repeat: str, n: int | None) -> str | None:
    """Преобразует периодичность в формат хранения в БД."""
    if repeat == "daily":
        return "days:1"

    if repeat == "weekly":
        return "days:7"

    if repeat == "monthly":
        return "monthly"

    if repeat == "yearly":
        return "yearly"

    if repeat == "every_n_days" and n is not None:
        try:
            days = int(n)
        except (TypeError, ValueError):
            return None

        if 1 <= days <= 365:
            return f"days:{days}"

    return None


def human_rule(rule: str | None) -> str:
    """Человекочитаемое описание повторения."""
    if not rule:
        return "разово"

    if rule == "monthly":
        return "каждый месяц"

    if rule == "yearly":
        return "каждый год"

    if rule.startswith("days:"):
        try:
            days = int(rule.split(":", 1)[1])
        except (ValueError, IndexError):
            return "периодически"

        if days == 1:
            return "каждый день"

        if days == 7:
            return "каждую неделю"

        return f"каждые {days} дн."

    return "периодически"


def advance(dt: datetime, rule: str) -> datetime:
    """Вычисляет следующее срабатывание повторяющегося напоминания."""
    if rule == "monthly":
        return dt + relativedelta(months=1)

    if rule == "yearly":
        return dt + relativedelta(years=1)

    if rule.startswith("days:"):
        days = int(rule.split(":", 1)[1])

        if days < 1:
            raise ValueError("Интервал повторения должен быть положительным")

        return dt + timedelta(days=days)

    raise ValueError(f"Неизвестное правило повторения: {rule}")


async def create(
    user_id: int,
    text: str,
    next_at: datetime,
    rule: str | None = None,
    tax_key: str | None = None,
) -> int | None:
    """Создаёт напоминание. Возвращает его ID или None при достижении лимита."""
    reminder_text = text.strip()

    if not reminder_text:
        raise ValueError("Текст напоминания не может быть пустым")

    if len(reminder_text) > 300:
        raise ValueError("Текст напоминания слишком длинный")

    if next_at.tzinfo is None:
        raise ValueError("Дата напоминания должна содержать часовой пояс")

    if rule is not None:
        valid_rule = rule == "monthly" or rule == "yearly"

        if rule.startswith("days:"):
            try:
                days = int(rule.split(":", 1)[1])
                valid_rule = 1 <= days <= 365
            except (ValueError, IndexError):
                valid_rule = False

        if not valid_rule:
            raise ValueError("Некорректное правило повторения")

    pool = db.pool()

    if tax_key is None:
        count = await pool.fetchval(
            """
            SELECT count(*)
            FROM reminders
            WHERE user_id = $1
              AND active = TRUE
              AND tax_key IS NULL
            """,
            user_id,
        )

        if count >= settings.max_reminders_per_user:
            return None

    return await pool.fetchval(
        """
        INSERT INTO reminders (
            user_id,
            text,
            next_at,
            repeat_rule,
            tax_key
        )
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        user_id,
        reminder_text,
        next_at.astimezone(timezone.utc),
        rule,
        tax_key,
    )


async def create_tax(
    user_id: int,
    regime: str,
    tz_name: str | None,
) -> int:
    """Создаёт примерные налоговые напоминания для выбранного режима."""
    rules = TAX_RULES.get(regime)

    if not rules:
        return 0

    tz = tz_of(tz_name)
    now = datetime.now(tz)

    await db.pool().execute(
        "DELETE FROM reminders WHERE user_id=$1 AND tax_key LIKE $2",
        user_id,
        f"{regime}:%",
    )

    created = 0

    for index, (kind, month, day, title) in enumerate(rules):
        base = now.replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        )

        if kind == "monthly":
            candidate = base.replace(day=day) - timedelta(
                days=REMIND_DAYS_BEFORE
            )

            if candidate <= now:
                candidate += relativedelta(months=1)

            rule = "monthly"
            when = f"{day}-го числа"

        else:
            candidate = base.replace(
                month=month,
                day=day,
            ) - timedelta(days=REMIND_DAYS_BEFORE)

            if candidate <= now:
                candidate += relativedelta(years=1)

            rule = "yearly"
            when = f"{day:02d}.{month:02d}"

        reminder_id = await create(
            user_id=user_id,
            text=(
                f"Через {REMIND_DAYS_BEFORE} дня срок "
                f"({when}): {title}"
            ),
            next_at=candidate.astimezone(timezone.utc),
            rule=rule,
            tax_key=f"{regime}:{index}",
        )

        if reminder_id is not None:
            created += 1

    return created


def describe(reminder, tz_name: str | None) -> str:
    """Формирует описание напоминания для пользователя."""
    local = reminder["next_at"].astimezone(tz_of(tz_name))

    return (
        f"🔔 {reminder['text']}\n"
        f"Следующее: {local:%d.%m.%Y %H:%M}\n"
        f"Повтор: {human_rule(reminder['repeat_rule'])}"
    )


def keyboard(reminder) -> InlineKeyboardMarkup:
    """Создаёт кнопки управления напоминанием."""
    reminder_id = reminder["id"]

    rows = [
        [
            InlineKeyboardButton(
                text="⏰ Отложить на день",
                callback_data=f"rem:snooze:{reminder_id}",
            ),
            InlineKeyboardButton(
                text="🔕 Отключить",
                callback_data=f"rem:off:{reminder_id}",
            ),
        ]
    ]

    rule = reminder["repeat_rule"]

    if rule and rule.startswith("days:"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="Реже",
                    callback_data=f"rem:less:{reminder_id}",
                ),
                InlineKeyboardButton(
                    text="Чаще",
                    callback_data=f"rem:more:{reminder_id}",
                ),
            ]
        )

    if is_url(settings.platform_url):
        rows.append(
            [
                InlineKeyboardButton(
                    text="📚 Открыть Альфа-Курс",
                    url=link(settings.platform_url),
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def apply_action(
    user_id: int,
    reminder_id: int,
    action: str,
) -> str:
    """Обрабатывает действия пользователя с напоминанием."""
    pool = db.pool()

    reminder = await pool.fetchrow(
        """
        SELECT *
        FROM reminders
        WHERE id = $1
          AND user_id = $2
        """,
        reminder_id,
        user_id,
    )

    if not reminder:
        return "Напоминание не найдено."

    if action == "off":
        await pool.execute(
            """
            UPDATE reminders
            SET active = FALSE
            WHERE id = $1
              AND user_id = $2
            """,
            reminder_id,
            user_id,
        )

        return "Напоминание отключено."

    if action == "del":
        await pool.execute(
            """
            DELETE FROM reminders
            WHERE id = $1
              AND user_id = $2
            """,
            reminder_id,
            user_id,
        )

        return "Напоминание удалено."

    if action == "snooze":
        later = datetime.now(timezone.utc) + timedelta(days=1)

        await pool.execute(
            """
            UPDATE reminders
            SET active = TRUE,
                next_at = $3
            WHERE id = $1
              AND user_id = $2
            """,
            reminder_id,
            user_id,
            later,
        )

        return "Напомню завтра."

    if action in ("less", "more"):
        rule = reminder["repeat_rule"]

        if not rule or not rule.startswith("days:"):
            return (
                "Частоту этого напоминания изменить нельзя, "
                "но его можно отключить."
            )

        try:
            days = int(rule.split(":", 1)[1])
        except (ValueError, IndexError):
            return "Не удалось изменить частоту напоминания."

        if action == "less":
            days = min(days * 2, 60)
        else:
            days = max(days // 2, 1)

        new_rule = f"days:{days}"

        await pool.execute(
            """
            UPDATE reminders
            SET repeat_rule = $3
            WHERE id = $1
              AND user_id = $2
            """,
            reminder_id,
            user_id,
            new_rule,
        )

        return f"Теперь напоминаю {human_rule(new_rule)}."

    return "Неизвестное действие."


async def _tick(bot) -> None:
    """Проверяет и отправляет все напоминания, срок которых наступил."""
    now = datetime.now(timezone.utc)
    pool = db.pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT
                    r.*,
                    u.chat_id
                FROM reminders AS r
                JOIN users AS u
                    ON u.id = r.user_id
                WHERE r.active = TRUE
                  AND r.next_at <= $1
                ORDER BY r.next_at
                LIMIT $2
                FOR UPDATE OF r SKIP LOCKED
                """,
                now,
                MAX_BATCH_SIZE,
            )

            for reminder in rows:
                try:
                    await bot.send_message(
                        chat_id=reminder["chat_id"],
                        text=f"🔔 Напоминание: {reminder['text']}",
                        reply_markup=keyboard(reminder),
                    )

                except TelegramForbiddenError:
                    log.warning(
                        "Пользователь %s заблокировал бота. "
                        "Его напоминания отключены.",
                        reminder["user_id"],
                    )

                    await conn.execute(
                        """
                        UPDATE reminders
                        SET active = FALSE
                        WHERE user_id = $1
                        """,
                        reminder["user_id"],
                    )
                    continue

                except Exception:
                    log.exception(
                        "Не удалось отправить напоминание id=%s. "
                        "Повторная попытка через 5 минут.",
                        reminder["id"],
                    )

                    await conn.execute(
                        """
                        UPDATE reminders
                        SET next_at = $2
                        WHERE id = $1
                        """,
                        reminder["id"],
                        now + timedelta(minutes=5),
                    )
                    continue

                await conn.execute(
                    """
                    INSERT INTO events (
                        user_id,
                        type,
                        payload
                    )
                    VALUES (
                        $1,
                        'reminder_sent',
                        $2::jsonb
                    )
                    """,
                    reminder["user_id"],
                    json.dumps(
                        {"reminder_id": reminder["id"]},
                        ensure_ascii=False,
                    ),
                )

                if reminder["repeat_rule"]:
                    try:
                        next_time = advance(
                            reminder["next_at"],
                            reminder["repeat_rule"],
                        )

                        while next_time <= now:
                            next_time = advance(
                                next_time,
                                reminder["repeat_rule"],
                            )

                    except (ValueError, TypeError):
                        log.exception(
                            "Некорректное правило повторения у напоминания %s",
                            reminder["id"],
                        )

                        await conn.execute(
                            """
                            UPDATE reminders
                            SET active = FALSE
                            WHERE id = $1
                            """,
                            reminder["id"],
                        )
                        continue

                    await conn.execute(
                        """
                        UPDATE reminders
                        SET next_at = $2
                        WHERE id = $1
                        """,
                        reminder["id"],
                        next_time,
                    )

                else:
                    await conn.execute(
                        """
                        UPDATE reminders
                        SET active = FALSE
                        WHERE id = $1
                        """,
                        reminder["id"],
                    )

                log.info(
                    "Напоминание id=%s успешно отправлено пользователю %s",
                    reminder["id"],
                    reminder["user_id"],
                )


async def loop(bot) -> None:
    """Фоновый цикл планировщика."""
    log.info(
        "Планировщик напоминаний запущен. Интервал проверки: %s сек.",
        CHECK_INTERVAL_SECONDS,
    )

    while True:
        try:
            await _tick(bot)

        except asyncio.CancelledError:
            log.info("Планировщик напоминаний остановлен.")
            raise

        except Exception:
            log.exception("Ошибка цикла напоминаний")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)