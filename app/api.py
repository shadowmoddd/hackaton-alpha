
"""HTTP API для Telegram Mini App. Авторизация только через подписанный Telegram initData."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qsl
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db, dialog, recs, reminders, search
from .config import settings
from .handlers import router
from .util import link

log = logging.getLogger(__name__)


def telegram_user(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(401, "Откройте Mini App из Telegram")

    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = values.pop("hash")
        auth_date = int(values.get("auth_date", "0"))
    except (ValueError, KeyError):
        raise HTTPException(401, "Некорректные данные Telegram")

    # Не принимаем данные, созданные в будущем или старше 24 часов.
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > 86400:
        raise HTTPException(
            401,
            "Сессия Telegram устарела. Перезапустите Mini App",
        )

    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(values.items())
    )

    secret = hmac.new(
        b"WebAppData",
        settings.telegram_token.encode(),
        hashlib.sha256,
    ).digest()

    calculated = hmac.new(
        secret,
        check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(
            401,
            "Не удалось подтвердить пользователя Telegram",
        )

    try:
        user = json.loads(values["user"])
        uid = int(user["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(
            401,
            "В initData отсутствует пользователь Telegram",
        )

    return {
        "id": uid,
        "first_name": user.get("first_name", ""),
        "username": user.get("username"),
    }


async def current_user(
    x_telegram_init_data: str = Header(default=""),
) -> dict:
    user = telegram_user(x_telegram_init_data)

    await db.upsert_user(
        user["id"],
        user["id"],
        user.get("username"),
    )

    return user


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()

    bot = Bot(token=settings.telegram_token)
    dp = Dispatcher()
    dp.include_router(router)

    reminder_task = None
    polling_task = None

    try:
        # Для работы через polling webhook должен быть отключён.
        await bot.delete_webhook(drop_pending_updates=True)

        # Запускаем цикл напоминаний.
        reminder_task = asyncio.create_task(
            reminders.loop(bot),
            name="reminders-loop",
        )

        # Запускаем Telegram-бота в фоне.
        polling_task = asyncio.create_task(
            dp.start_polling(
                bot,
                handle_signals=False,
                close_bot_session=False,
            ),
            name="telegram-polling",
        )

        log.info("Telegram bot polling started")

        yield

    finally:
        tasks = [
            task
            for task in (polling_task, reminder_task)
            if task is not None
        ]

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

        await bot.session.close()
        await db.pool().close()

        log.info("Application shutdown completed")


app = FastAPI(
    title="Skilly Mini App API",
    version="1.0.0",
    lifespan=lifespan,
)

allowed_origins = [
    x.strip()
    for x in settings.miniapp_origins.split(",")
    if x.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins or ["*"],
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],
    allow_headers=[
        "Content-Type",
        "X-Telegram-Init-Data",
    ],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ProfileRequest(BaseModel):
    stage: str | None = None
    area: str | None = None
    business_type: str | None = None
    tax_regime: str | None = None
    experience: str | None = None
    goal: str | None = None
    tz: str | None = None


class ReminderRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    date: str
    repeat: str = "none"


class MaterialClickRequest(BaseModel):
    material_id: UUID


@app.get("/health")
async def health():
    return {"ok": True, "service": "skilly-api"}


@app.get("/api/me")
async def me(user: dict = Depends(current_user)):
    return {
        "user": user,
        "profile": await db.get_profile(user["id"]),
    }


@app.get("/api/profile")
async def get_profile(user: dict = Depends(current_user)):
    return await db.get_profile(user["id"])


@app.put("/api/profile")
async def put_profile(
    payload: ProfileRequest,
    user: dict = Depends(current_user),
):
    data = payload.model_dump(exclude_unset=True)

    if data.get("tz"):
        try:
            ZoneInfo(data["tz"])
        except Exception:
            raise HTTPException(422, "Некорректный часовой пояс")

    await db.update_profile(user["id"], **data)

    await db.log_event(
        user["id"],
        "miniapp_profile_updated",
        {k: v for k, v in data.items() if v},
    )

    return await db.get_profile(user["id"])


@app.post("/api/chat")
async def chat(
    payload: ChatRequest,
    user: dict = Depends(current_user),
):
    answer, _buttons = await dialog.respond(
        user["id"],
        payload.message.strip(),
    )

    profile = await db.get_profile(user["id"])
    found = await search.materials(payload.message.strip())
    materials = recs.rank(found, profile, top=3)

    await db.log_event(
        user["id"],
        "miniapp_message",
        {"length": len(payload.message)},
    )

    return {
        "answer": answer,
        "no_answer": "не нашёл надёжного ответа" in answer.lower(),
        "materials": [
            {
                "id": str(m["id"]),
                "title": m["title"],
                "description": (
                    m.get("description") if not m.get("is_paid") else None
                ),
                "url": link(m.get("url")) if m.get("url") else None,
                "is_paid": m.get("is_paid", False),
                "price": m.get("price"),
                "topic": m.get("topic"),
                "reasons": m.get("reasons", []),
            }
            for m in materials
        ],
    }


@app.get("/api/history")
async def history(user: dict = Depends(current_user)):
    rows = await db.pool().fetch(
        """
        SELECT role, content, created_at
        FROM messages
        WHERE user_id=$1
        ORDER BY created_at DESC, id DESC
        LIMIT 40
        """,
        user["id"],
    )

    return {
        "items": [
            {
                "role": r["role"],
                "text": r["content"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in reversed(rows)
        ]
    }


@app.get("/api/materials")
async def get_materials(
    query: str = Query(default="", max_length=300),
    user: dict = Depends(current_user),
):
    profile = await db.get_profile(user["id"])

    found = (
        await search.materials(query)
        if query.strip()
        else await recs.cold_start()
    )

    items = (
        recs.rank(found, profile, top=10)
        if query.strip()
        else found[:10]
    )

    await db.log_event(
        user["id"],
        "miniapp_materials_view",
        {"query": query[:100]},
    )

    return {
        "items": [
            {
                "id": str(m["id"]),
                "title": m["title"],
                "description": (
                    m.get("description") if not m.get("is_paid") else None
                ),
                "topic": m.get("topic"),
                "format": m.get("format"),
                "is_paid": m.get("is_paid", False),
                "price": m.get("price"),
                "url": link(m.get("url")) if m.get("url") else None,
                "reasons": m.get("reasons", []),
            }
            for m in items
        ]
    }


@app.post("/api/material-click")
async def material_click(
    payload: MaterialClickRequest,
    user: dict = Depends(current_user),
):
    exists = await db.pool().fetchval(
        """
        SELECT EXISTS(
            SELECT 1
            FROM kb.materials
            WHERE id=$1::uuid
              AND is_recommendable
              AND is_closed IS NOT TRUE
              AND (
                  (access = 'free' AND url_confirmed IS TRUE AND url IS NOT NULL)
                  OR
                  (access IN ('paid','subscription') AND buy_url IS NOT NULL)
              )
        )
        """,
        payload.material_id,
    )

    if not exists:
        raise HTTPException(404, "Материал не найден")

    await db.log_event(
        user["id"],
        "material_clicked",
        {"material_id": payload.material_id},
    )

    return {"ok": True}


@app.get("/api/reminders")
async def get_reminders(user: dict = Depends(current_user)):
    rows = await db.pool().fetch(
        """
        SELECT id, text, next_at, repeat_rule, active
        FROM reminders
        WHERE user_id=$1
        ORDER BY active DESC, next_at
        """,
        user["id"],
    )

    tz = reminders.tz_of(
        (await db.get_profile(user["id"])).get("tz")
    )

    return {
        "items": [
            {
                "id": r["id"],
                "text": r["text"],
                "next_at": r["next_at"].astimezone(tz).isoformat(),
                "repeat_rule": r["repeat_rule"],
                "active": r["active"],
            }
            for r in rows
        ]
    }


@app.post("/api/reminders")
async def post_reminder(
    payload: ReminderRequest,
    user: dict = Depends(current_user),
):
    profile = await db.get_profile(user["id"])
    tz = reminders.tz_of(profile.get("tz"))

    try:
        d = datetime.fromisoformat(payload.date)

        if d.tzinfo is None:
            d = d.replace(tzinfo=tz)

        if d <= datetime.now(tz):
            raise ValueError

    except ValueError:
        raise HTTPException(422, "Укажите будущую дату")

    rule_map = {
        "Не повторять": None,
        "none": None,
        "Каждый день": "days:1",
        "daily": "days:1",
        "Каждую неделю": "days:7",
        "weekly": "days:7",
        "Каждый месяц": "monthly",
        "monthly": "monthly",
        "Каждый год": "yearly",
        "yearly": "yearly",
    }

    if payload.repeat not in rule_map:
        raise HTTPException(422, "Неизвестная периодичность")

    rid = await reminders.create(
        user["id"],
        payload.text,
        d.astimezone(timezone.utc),
        rule_map[payload.repeat],
    )

    if rid is None:
        raise HTTPException(
            409,
            "Достигнут лимит активных напоминаний",
        )

    await db.log_event(
        user["id"],
        "reminder_created_miniapp",
        {"id": rid},
    )

    return {"id": rid, "ok": True}


@app.patch("/api/reminders/{reminder_id}")
async def patch_reminder(
    reminder_id: int,
    active: bool,
    user: dict = Depends(current_user),
):
    result = await db.pool().execute(
        "UPDATE reminders SET active=$3 WHERE id=$1 AND user_id=$2",
        reminder_id,
        user["id"],
        active,
    )

    if result == "UPDATE 0":
        raise HTTPException(404, "Напоминание не найдено")

    return {"ok": True}


@app.delete("/api/reminders/{reminder_id}")
async def delete_reminder(
    reminder_id: int,
    user: dict = Depends(current_user),
):
    result = await db.pool().execute(
        "DELETE FROM reminders WHERE id=$1 AND user_id=$2",
        reminder_id,
        user["id"],
    )

    if result == "DELETE 0":
        raise HTTPException(404, "Напоминание не найдено")

    return {"ok": True}
