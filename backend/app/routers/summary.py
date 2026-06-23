from fastapi import APIRouter
from datetime import datetime

from app.db import get_pool

router = APIRouter(tags=["summary"])


@router.get("/summary/kabupaten")
async def get_summary_by_kabupaten():
    """Ringkasan kondisi air tanah per kabupaten untuk kartu dashboard."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                kabupaten,
                COUNT(*) AS total_wells,
                COUNT(*) FILTER (WHERE status_level = 'normal') AS normal,
                COUNT(*) FILTER (WHERE status_level = 'waspada') AS waspada,
                COUNT(*) FILTER (WHERE status_level = 'kritis') AS kritis,
                COUNT(*) FILTER (WHERE status_level = 'sangat_kritis') AS sangat_kritis,
                COUNT(*) FILTER (WHERE status_level = 'tidak_ada_data') AS no_data,
                ROUND(AVG(water_level_m)::numeric, 2) AS avg_water_level_m,
                ROUND(AVG(ph)::numeric, 2) AS avg_ph
            FROM well_latest_status
            GROUP BY kabupaten
            ORDER BY kabupaten
        """)

        result = []
        for row in rows:
            total = row["total_wells"]
            kritis_count = (row["kritis"] or 0) + (row["sangat_kritis"] or 0)
            # Level risiko keseluruhan kabupaten
            if total > 0:
                kritis_pct = (kritis_count / total) * 100
                if kritis_pct >= 50:
                    risk = "KRITIS"
                elif kritis_pct >= 25:
                    risk = "WASPADA"
                else:
                    risk = "NORMAL"
            else:
                risk = "TIDAK_ADA_DATA"

            result.append({
                "kabupaten": row["kabupaten"],
                "total_wells": total,
                "status_breakdown": {
                    "normal": row["normal"] or 0,
                    "waspada": row["waspada"] or 0,
                    "kritis": row["kritis"] or 0,
                    "sangat_kritis": row["sangat_kritis"] or 0,
                    "tidak_ada_data": row["no_data"] or 0
                },
                "avg_water_level_m": float(row["avg_water_level_m"]) if row["avg_water_level_m"] else None,
                "avg_ph": float(row["avg_ph"]) if row["avg_ph"] else None,
                "overall_risk": risk
            })

        return {
            "generated_at": datetime.now().isoformat(),
            "total_kabupaten": len(result),
            "legal_basis": "PP No. 43 Tahun 2008",
            "data": result
        }