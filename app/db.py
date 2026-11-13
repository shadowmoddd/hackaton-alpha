import json
from pathlib import Path

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None

PROFILE_FIELDS = {
    "stage",
    "area",
    "business_type",
    "tax_regime",
    "experience",
    "goal",
    "tz",
}


async def init_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=10,
        server_settings={"search_path": "app,public"},
    )
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    async with _pool.acquire() as conn:
        await conn.execute(sql)
    return _pool


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialised")
    return _pool


async def upsert_user(user_id: int, chat_id: int, username: str | None = None) -> None:
    await pool().execute(
        "INSERT INTO users(id, chat_id, username, last_seen_at) VALUES($1,$2,$3,now()) "
        "ON CONFLICT (id) DO UPDATE SET chat_id=$2, username=COALESCE($3, users.username), last_seen_at=now()",
        user_id, chat_id, username,
    )
    await pool().execute("INSERT INTO profiles(user_id) VALUES($1) ON CONFLICT DO NOTHING", user_id)


async def get_profile(user_id: int) -> dict:
    row = await pool().fetchrow("SELECT * FROM profiles WHERE user_id=$1", user_id)
    return dict(row) if row else {}


async def update_profile(user_id: int, **fields) -> None:
    clean = {k: (str(v)[:300] if v is not None else None) for k, v in fields.items() if k in PROFILE_FIELDS and v is not None}
    if not clean:
        return
    sets = ", ".join(f"{k}=${i+2}" for i, k in enumerate(clean))
    await pool().execute(f"UPDATE profiles SET {sets}, updated_at=now() WHERE user_id=$1", user_id, *clean.values())


async def save_message(user_id: int, role: str, content: str) -> None:
    await pool().execute("INSERT INTO messages(user_id, role, content) VALUES($1,$2,$3)", user_id, role, content[:4000])
    await pool().execute(
        "DELETE FROM messages WHERE user_id=$1 AND id NOT IN "
        "(SELECT id FROM messages WHERE user_id=$1 ORDER BY created_at DESC, id DESC LIMIT 40)", user_id,
    )


async def get_history(user_id: int, limit: int = 6) -> list[tuple[str, str]]:
    rows = await pool().fetch(
        "SELECT role, content FROM (SELECT role,content,created_at,id FROM messages "
        "WHERE user_id=$1 ORDER BY created_at DESC,id DESC LIMIT $2) h ORDER BY created_at,id",
        user_id, limit,
    )
    return [(r["role"], r["content"]) for r in rows]


async def log_event(user_id: int | None, type_: str, payload: dict | None = None) -> None:
    await pool().execute(
        "INSERT INTO events(user_id,type,payload) VALUES($1,$2,$3::jsonb)",
        user_id, type_, json.dumps(payload or {}, ensure_ascii=False, default=str),
    )
