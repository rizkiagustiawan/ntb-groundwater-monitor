"""
ENSO (El Niño-Southern Oscillation) climate index integration.
Ref: Aldrian & Susanto (2003) — El Niño significantly impacts NTB rainfall.
Uses CHIRPS rainfall anomaly % as proxy for ENSO phase detection.
"""

from fastapi import APIRouter, Query

from app.db import get_pool

router = APIRouter(prefix="/enso", tags=["enso"])

EL_NINO_PCT = -30.0
LA_NINA_PCT = 30.0
CONSECUTIVE_STATUS = 3
CONSECUTIVE_EVENT = 5


@router.get("/status")
async def get_status(months: int = Query(6, ge=3, le=24, description="Recent months to analyze")):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH monthly AS (
                SELECT year, month, AVG(precip_mm) AS avg_precip
                FROM chirps_precip
                GROUP BY year, month
                ORDER BY year DESC, month DESC
                LIMIT $1
            ),
            baseline AS (
                SELECT AVG(precip_mm) AS mean_precip FROM chirps_precip
            )
            SELECT m.year, m.month, m.avg_precip,
                   ROUND(((m.avg_precip - b.mean_precip) / NULLIF(b.mean_precip, 0) * 100)::numeric, 2) AS anomaly_pct
            FROM monthly m CROSS JOIN baseline b
            ORDER BY m.year, m.month
            """,
            months,
        )

    if len(rows) < 3:
        return {"status": "insufficient_data", "months_analyzed": len(rows)}

    anomalies = [float(r["anomaly_pct"]) for r in rows]

    consecutive_neg = 0
    consecutive_pos = 0
    for a in anomalies:
        if a < EL_NINO_PCT:
            consecutive_neg += 1
            consecutive_pos = 0
        elif a > LA_NINA_PCT:
            consecutive_pos += 1
            consecutive_neg = 0
        else:
            consecutive_neg = 0
            consecutive_pos = 0

    if consecutive_neg >= CONSECUTIVE_STATUS:
        status = "El Niño likely"
    elif consecutive_pos >= CONSECUTIVE_STATUS:
        status = "La Niña likely"
    else:
        status = "Neutral"

    return {
        "status": status,
        "months_analyzed": len(rows),
        "recent_anomalies": [
            {"period": f"{r['year']}-{r['month']:02d}", "anomaly_pct": float(r["anomaly_pct"])}
            for r in rows
        ],
        "consecutive_el_nino_months": consecutive_neg,
        "consecutive_la_nina_months": consecutive_pos,
    }


@router.get("/events")
async def get_events(
    start_year: int = Query(2015, description="Tahun mulai"),
    end_year: int = Query(2025, description="Tahun akhir"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH monthly AS (
                SELECT year, month, AVG(precip_mm) AS avg_precip
                FROM chirps_precip
                WHERE year BETWEEN $1 AND $2
                GROUP BY year, month
            ),
            baseline AS (
                SELECT AVG(precip_mm) AS mean_precip FROM chirps_precip
            )
            SELECT m.year, m.month, m.avg_precip,
                   ROUND(((m.avg_precip - b.mean_precip) / NULLIF(b.mean_precip, 0) * 100)::numeric, 2) AS anomaly_pct
            FROM monthly m CROSS JOIN baseline b
            ORDER BY m.year, m.month
            """,
            start_year,
            end_year,
        )

    if not rows:
        return {"events": [], "count": 0}

    records = [(r["year"], r["month"], float(r["anomaly_pct"])) for r in rows]

    events = []
    _detect_events(records, events, "El Niño", EL_NINO_PCT, lambda a, t: a < t)
    _detect_events(records, events, "La Niña", LA_NINA_PCT, lambda a, t: a > t)

    events.sort(key=lambda e: e["start_month"])
    return {"events": events, "count": len(events)}


def _detect_events(records, events, phase, threshold, compare):
    in_event = False
    event_start = None
    event_anomalies = []

    for year, month, anomaly in records:
        if compare(anomaly, threshold):
            if not in_event:
                in_event = True
                event_start = f"{year}-{month:02d}"
                event_anomalies = []
            event_anomalies.append(anomaly)
        else:
            if in_event and len(event_anomalies) >= CONSECUTIVE_EVENT:
                _append_event(events, event_start, f"{year}-{month:02d}", event_anomalies, phase)
            in_event = False
            event_anomalies = []

    if in_event and len(event_anomalies) >= CONSECUTIVE_EVENT:
        last_y, last_m, _ = records[-1]
        _append_event(events, event_start, f"{last_y}-{last_m:02d}", event_anomalies, phase, ongoing=True)


def _append_event(events, start, end, anomalies, phase, ongoing=False):
    avg = sum(anomalies) / len(anomalies)
    event = {
        "start_month": start,
        "end_month": end,
        "duration_months": len(anomalies),
        "phase": phase,
        "avg_anomaly_pct": round(avg, 2),
        "severity": _severity(abs(avg)),
    }
    if ongoing:
        event["ongoing"] = True
    events.append(event)


def _severity(abs_pct: float) -> str:
    if abs_pct >= 50:
        return "severe"
    if abs_pct >= 40:
        return "strong"
    if abs_pct >= 30:
        return "moderate"
    return "weak"


@router.get("/impact")
async def get_impact(
    start_year: int = Query(2015, description="Tahun mulai"),
    end_year: int = Query(2025, description="Tahun akhir"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH monthly AS (
                SELECT year, month, AVG(precip_mm) AS avg_precip
                FROM chirps_precip
                WHERE year BETWEEN $1 AND $2
                GROUP BY year, month
            ),
            baseline AS (
                SELECT AVG(precip_mm) AS mean_precip FROM chirps_precip
            ),
            anomalies AS (
                SELECT m.year, m.month, m.avg_precip,
                       (m.avg_precip - b.mean_precip) AS anomaly_mm,
                       ((m.avg_precip - b.mean_precip) / NULLIF(b.mean_precip, 0) * 100) AS anomaly_pct
                FROM monthly m CROSS JOIN baseline b
            ),
            periods AS (
                SELECT year, month, avg_precip, anomaly_mm, anomaly_pct,
                       CASE
                           WHEN anomaly_pct < $3 THEN 'El Niño'
                           WHEN anomaly_pct > $4 THEN 'La Niña'
                           ELSE 'Neutral'
                       END AS phase
                FROM anomalies
            )
            SELECT p.year, p.month, p.phase,
                   ROUND(p.anomaly_mm::numeric, 2) AS rainfall_anomaly_mm,
                   ROUND(p.anomaly_pct::numeric, 2) AS rainfall_anomaly_pct,
                   u.gws_anomaly, u.ndvi, u.drought_index, u.risk_level
            FROM periods p
            LEFT JOIN unified_monitoring u
                ON EXTRACT(YEAR FROM u.period_date) = p.year
                AND EXTRACT(MONTH FROM u.period_date) = p.month
            WHERE p.phase != 'Neutral'
            ORDER BY p.year, p.month
            """,
            start_year,
            end_year,
            EL_NINO_PCT,
            LA_NINA_PCT,
        )

    if not rows:
        return {"impacts": [], "count": 0}

    impacts = [
        {
            "period": f"{r['year']}-{r['month']:02d}",
            "phase": r["phase"],
            "rainfall_anomaly_mm": float(r["rainfall_anomaly_mm"]),
            "rainfall_anomaly_pct": float(r["rainfall_anomaly_pct"]),
            "gws_anomaly_cm": float(r["gws_anomaly"]) if r["gws_anomaly"] is not None else None,
            "ndvi": float(r["ndvi"]) if r["ndvi"] is not None else None,
            "drought_index": float(r["drought_index"]) if r["drought_index"] is not None else None,
            "risk_level": r["risk_level"],
        }
        for r in rows
    ]

    el_nino_months = [i for i in impacts if i["phase"] == "El Niño"]
    la_nina_months = [i for i in impacts if i["phase"] == "La Niña"]

    def _avg(key, items):
        vals = [i[key] for i in items if i[key] is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "impacts": impacts,
        "count": len(impacts),
        "summary": {
            "el_nino": {
                "months": len(el_nino_months),
                "avg_rainfall_deficit_mm": _avg("rainfall_anomaly_mm", el_nino_months),
                "avg_gws_anomaly_cm": _avg("gws_anomaly_cm", el_nino_months),
                "avg_ndvi": _avg("ndvi", el_nino_months),
            },
            "la_nina": {
                "months": len(la_nina_months),
                "avg_rainfall_surplus_mm": _avg("rainfall_anomaly_mm", la_nina_months),
                "avg_gws_anomaly_cm": _avg("gws_anomaly_cm", la_nina_months),
                "avg_ndvi": _avg("ndvi", la_nina_months),
            },
        },
    }
