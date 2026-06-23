from fastapi import APIRouter, HTTPException
from datetime import datetime

from app.db import get_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok", "database": "connected", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)}")