from fastapi import APIRouter
from app.db import get_pool

router = APIRouter(prefix="/gap", tags=["gap"])


@router.get("/status")
async def get_gap_status():
    """Show GRACE data gap status and interpolation coverage."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Check for gap period data
        gap_count = await conn.fetchval("""
            SELECT COUNT(*) FROM grace_tws
            WHERE period_date >= '2017-07-01' AND period_date <= '2018-04-30'
        """)

        total = await conn.fetchval("SELECT COUNT(*) FROM grace_tws")

        # Coverage by year
        yearly = await conn.fetch("""
            SELECT EXTRACT(YEAR FROM period_date)::int as year,
                   COUNT(*) as n_records,
                   COUNT(DISTINCT lat || ',' || lon) as n_grid_points
            FROM grace_tws
            GROUP BY EXTRACT(YEAR FROM period_date)
            ORDER BY year
        """)

        return {
            "gap_period": {"start": "2017-07", "end": "2018-04", "n_months": 10},
            "gap_records": gap_count,
            "gap_filled": gap_count > 0,
            "total_records": total,
            "yearly_coverage": [{
                "year": r["year"],
                "n_records": r["n_records"],
                "n_grid_points": r["n_grid_points"]
            } for r in yearly]
        }


@router.get("/interpolate")
async def trigger_interpolation():
    """Run gap interpolation script."""
    import subprocess
    try:
        result = subprocess.run(
            ["python3", "scripts/grace_gap_interpolate.py"],
            capture_output=True, text=True, timeout=120
        )
        return {
            "status": "completed" if result.returncode == 0 else "failed",
            "output": result.stdout[-1000:] if result.stdout else "",
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
