import os
import asyncpg
from typing import Optional

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater"
)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool._closed:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


async def get_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


async def close_pool():
    global _pool
    if _pool and not _pool._closed:
        await _pool.close()
        _pool = None
