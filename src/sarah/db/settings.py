import json

import asqlite

CREATE_SETTINGS = "CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT)"

async def get_setting(pool: asqlite.Pool, name: str, default = None):
    async with pool.acquire() as conn:
        await conn.execute(CREATE_SETTINGS)
        async with conn.execute("SELECT value FROM settings WHERE name = ?", (name,)) as cursor:
            row = await cursor.fetchone()

    if not row and default is None:
        raise RuntimeError(f"A setting without a default value, {name}, is not set.")

    return json.loads(row["value"]) if row else default

async def set_setting(pool: asqlite.Pool, name: str, value):
    async with pool.acquire() as conn:
        await conn.execute(CREATE_SETTINGS)
        await conn.execute(
            "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
            (name, json.dumps(value),)
        )