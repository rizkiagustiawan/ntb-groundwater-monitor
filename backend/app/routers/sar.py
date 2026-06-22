from fastapi import APIRouter, Query
from typing import Optional

from app.db import get_pool

router = APIRouter(prefix="/sar", tags=["sar"])


def _rate_color(rate: float) -> str:
    if rate < -5:
        return "#e53e3e"
    if rate < -2:
        return "#dd6b20"
    return "#38a169"


def _risk_level(rate: float) -> str:
    if rate < -5:
        return "KRITIS"
    if rate < -2:
        return "WASPADA"
    return "NORMAL"


@router.get("/subsidence")
async def get_subsidence(
    kabupaten: Optional[str] = Query(None),
    min_rate: Optional[float] = Query(None, description="Filter rate_mm_year <= value (negative)"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        clauses = []
        params = []
        idx = 1
        if kabupaten:
            clauses.append(f"LOWER(kabupaten) = LOWER(${idx})")
            params.append(kabupaten)
            idx += 1
        if min_rate is not None:
            clauses.append(f"rate_mm_year <= ${idx}")
            params.append(min_rate)
            idx += 1

        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = await conn.fetch(
            f"SELECT * FROM sar_subsidence {where} ORDER BY rate_mm_year ASC",
            *params,
        )

        features = []
        for r in rows:
            rate = float(r["rate_mm_year"])
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(r["lon"]), float(r["lat"])],
                },
                "properties": {
                    "location": r["location"],
                    "kabupaten": r["kabupaten"],
                    "displacement_mm": float(r["displacement_mm"]),
                    "rate_mm_year": rate,
                    "period_start": r["period_start"].isoformat() if r["period_start"] else None,
                    "period_end": r["period_end"].isoformat() if r["period_end"] else None,
                    "n_observations": r["n_observations"],
                    "coherence": float(r["coherence"]) if r["coherence"] is not None else None,
                    "risk_level": _risk_level(rate),
                    "color": _rate_color(rate),
                },
            })

        return {
            "type": "FeatureCollection",
            "metadata": {
                "title": "SAR Subsidence — InSAR PSI",
                "source": "Sentinel-1 SAR Persistent Scatterer Interferometry",
                "filters": {"kabupaten": kabupaten, "min_rate": min_rate},
            },
            "features": features,
        }


@router.get("/subsidence/timeseries")
async def get_subsidence_timeseries(
    location: str = Query(...),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT period_start, period_end, displacement_mm, rate_mm_year,
                   n_observations, coherence
            FROM sar_subsidence
            WHERE LOWER(location) = LOWER($1)
            ORDER BY period_start
            """,
            location,
        )

        if not rows:
            return {"location": location, "series": []}

        return {
            "location": location,
            "series": [
                {
                    "period_start": r["period_start"].isoformat() if r["period_start"] else None,
                    "period_end": r["period_end"].isoformat() if r["period_end"] else None,
                    "displacement_mm": float(r["displacement_mm"]),
                    "rate_mm_year": float(r["rate_mm_year"]),
                    "n_observations": r["n_observations"],
                    "coherence": float(r["coherence"]) if r["coherence"] is not None else None,
                }
                for r in rows
            ],
        }


@router.get("/summary")
async def get_summary():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT kabupaten,
                   COUNT(*) AS n_locations,
                   AVG(rate_mm_year) AS avg_rate,
                   MIN(rate_mm_year) AS min_rate,
                   MAX(rate_mm_year) AS max_rate
            FROM sar_subsidence
            GROUP BY kabupaten
            ORDER BY avg_rate ASC
        """)

        return {
            "summary": [
                {
                    "kabupaten": r["kabupaten"],
                    "n_locations": r["n_locations"],
                    "avg_rate_mm_year": float(r["avg_rate"]),
                    "min_rate_mm_year": float(r["min_rate"]),
                    "max_rate_mm_year": float(r["max_rate"]),
                    "risk_level": _risk_level(float(r["avg_rate"])),
                    "color": _rate_color(float(r["avg_rate"])),
                }
                for r in rows
            ]
        }


@router.get("/geojson")
async def get_geojson():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM sar_subsidence ORDER BY rate_mm_year ASC"
        )

        features = []
        for r in rows:
            rate = float(r["rate_mm_year"])
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(r["lon"]), float(r["lat"])],
                },
                "properties": {
                    "location": r["location"],
                    "kabupaten": r["kabupaten"],
                    "rate_mm_year": rate,
                    "displacement_mm": float(r["displacement_mm"]),
                    "color": _rate_color(rate),
                },
            })

        return {"type": "FeatureCollection", "features": features}
