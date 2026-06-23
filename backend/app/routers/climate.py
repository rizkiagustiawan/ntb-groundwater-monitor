from fastapi import APIRouter, Query

from app.db import get_pool

router = APIRouter(prefix="/climate", tags=["climate"])


@router.get("/precipitation")
async def get_chirps_precipitation(
    start_year: int = Query(2020, description="Tahun mulai"),
    end_year: int = Query(2025, description="Tahun akhir")
):
    """Monthly aggregated precipitation NTB dari CHIRPS. Unit: mm."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT year, month, ROUND(AVG(precip_mm)::numeric, 2) as avg_precip
            FROM chirps_precip
            WHERE year BETWEEN $1 AND $2
            GROUP BY year, month
            ORDER BY year, month
        """, start_year, end_year)

        series = [{
            "period": f"{r['year']}-{r['month']:02d}",
            "precip_mm": float(r["avg_precip"])
        } for r in rows]

        return {
            "metadata": {"unit": "mm", "source": "UCSB-CHG CHIRPS v2.0"},
            "series": series
        }
