import json

import asqlite

from sarah.errors import NotSetupError

CREATE_SETTINGS = "CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT)"
_MISSING = object()

async def get_setting(pool: asqlite.Pool, name: str, default = _MISSING):
    async with pool.acquire() as conn:
        await conn.execute(CREATE_SETTINGS)
        async with conn.execute("SELECT value FROM settings WHERE name = ?", (name,)) as cursor:
            row = await cursor.fetchone()

    if row is None:
        if default is _MISSING:
            raise NotSetupError(name)
        return default
    return json.loads(row["value"])

async def set_setting(pool: asqlite.Pool, name: str, value):
    async with pool.acquire() as conn:
        await conn.execute(CREATE_SETTINGS)
        await conn.execute(
            "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
            (name, json.dumps(value),)
        )