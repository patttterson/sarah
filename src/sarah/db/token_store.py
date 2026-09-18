import os

import asqlite
from cryptography.fernet import Fernet

fernet = Fernet(os.getenv("FERNET_KEY", ""))

async def save_refresh_token(pool: asqlite.Pool, token: str):
    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value BLOB)")
        await conn.execute("INSERT OR REPLACE INTO secrets (name, value) VALUES ('refresh_token', ?)", (fernet.encrypt(token.encode()),))

async def get_refresh_token(pool: asqlite.Pool) -> str | None:
    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value BLOB)")
        async with conn.execute("SELECT value FROM secrets WHERE name = 'refresh_token'") as cursor:
            row = await cursor.fetchone()

    return fernet.decrypt(row["value"]).decode() if row else None