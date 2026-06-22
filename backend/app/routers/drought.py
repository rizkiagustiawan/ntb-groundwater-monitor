"""
Drought Early Warning System (DEWS) — SPI + GWS + NDVI fusion.
Ref: Siswanto et al. 2022, Narulita et al. 2021, Faisol et al. 2022.
"""

from fastapi import APIRouter, Query
import numpy as np

from app.db import get_pool

router = APIRouter(prefix="/drought", tags=["drought"])

EL_NINO_YEARS = {2015, 2023}


def _classify_spi(spi: float) -> str:
    if spi > 2:
        return "extremely_wet"
    if spi > 1:
        return "very_wet"
    if spi > 0:
        return "wet"
    if spi > -1:
        return "mild_drought"
    if spi > -2:
        return "moderate_drought"
    return "severe_drought"


def _classify_combined(spi: float, gws: float | None, ndvi: float | None) -> str:
    """Combine SPI, GWS anomaly, NDVI into single drought status."""
    spi_sev = 0
    if spi <= -2:
        spi_sev = 3
    elif spi <= -1:
        spi_sev = 2
    elif spi <= 0:
        spi_sev = 1

    gws_sev = 0
    if gws is not None:
        if gws <= -10:
            gws_sev = 3
        elif gws <= -5:
            gws_sev = 2
        elif gws <= -2:
            gws_sev = 1

    ndvi_sev = 0
    if ndvi is not None:
        if ndvi <= 0.15:
            ndvi_sev = 3
        elif ndvi <= 0.25:
            ndvi_sev = 2
        elif ndvi <= 0.35:
            ndvi_sev = 1

    max_sev = max(spi_sev, gws_sev, ndvi_sev)
    if max_sev == 0:
        return "NORMAL"
    if max_sev == 1:
        return "NORMAL"
    if max_sev == 2:
        return "WASPADA"
    if max_sev == 3:
        # check if any is truly extreme
        if spi <= -2 and gws is not None and gws <= -10:
            return "SANGAT_KRITIS"
        return "KRITIS"
    return "NORMAL"


@router.get("/spi")
async def get_spi(
    start_year: int = Query(2020, description="Tahun mulai"),
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
                ORDER BY year, month
            )
            SELECT year, month, avg_precip FROM monthly
            """,
            start_year,
            end_year,
        )

    if not rows:
        return {"series": [], "count": 0}

    precip_vals = [float(r["avg_precip"]) for r in rows]
    mean_p = float(np.mean(precip_vals))
    std_p = float(np.std(precip_vals))

    series = []
    for r, pv in zip(rows, precip_vals):
        spi = (pv - mean_p) / std_p if std_p > 0 else 0.0
        series.append({
            "period": f"{r['year']}-{r['month']:02d}",
            "year": r["year"],
            "month": r["month"],
            "avg_precip_mm": round(pv, 2),
            "spi": round(spi, 3),
            "classification": _classify_spi(spi),
        })

    return {
        "baseline": {"mean_precip_mm": round(mean_p, 2), "std_precip_mm": round(std_p, 2)},
        "count": len(series),
        "series": series,
    }


@router.get("/status")
async def get_status():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Latest month SPI from chirps
        precip_rows = await conn.fetch(
            """
            SELECT year, month, AVG(precip_mm) AS avg_precip
            FROM chirps_precip
            GROUP BY year, month
            ORDER BY year, month
            """
        )
        if len(precip_rows) < 2:
            return {"status": "NORMAL", "detail": "insufficient_data"}

        vals = [float(r["avg_precip"]) for r in precip_rows]
        mean_p = float(np.mean(vals))
        std_p = float(np.std(vals))
        latest_spi = (vals[-1] - mean_p) / std_p if std_p > 0 else 0.0

        # Latest GWS + NDVI from unified_monitoring
        um = await conn.fetchrow(
            """
            SELECT gws_anomaly, ndvi, risk_level
            FROM unified_monitoring
            ORDER BY period_date DESC LIMIT 1
            """
        )

    gws = float(um["gws_anomaly"]) if um and um["gws_anomaly"] is not None else None
    ndvi = float(um["ndvi"]) if um and um["ndvi"] is not None else None

    status = _classify_combined(latest_spi, gws, ndvi)

    return {
        "status": status,
        "spi": round(latest_spi, 3),
        "spi_classification": _classify_spi(latest_spi),
        "gws_anomaly": round(gws, 2) if gws is not None else None,
        "ndvi": round(ndvi, 4) if ndvi is not None else None,
        "unified_risk_level": um["risk_level"] if um else None,
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
            SELECT year, month, AVG(precip_mm) AS avg_precip
            FROM chirps_precip
            WHERE year BETWEEN $1 AND $2
            GROUP BY year, month
            ORDER BY year, month
            """,
            start_year,
            end_year,
        )

    if not rows:
        return {"events": [], "count": 0}

    vals = [float(r["avg_precip"]) for r in rows]
    mean_p = float(np.mean(vals))
    std_p = float(np.std(vals))

    spi_series = []
    for r, pv in zip(rows, vals):
        spi = (pv - mean_p) / std_p if std_p > 0 else 0.0
        spi_series.append((r["year"], r["month"], spi))

    # Detect consecutive drought periods (SPI < -1)
    events = []
    in_event = False
    event_start = None
    event_vals = []

    for year, month, spi in spi_series:
        if spi < -1:
            if not in_event:
                in_event = True
                event_start = f"{year}-{month:02d}"
                event_vals = []
            event_vals.append(spi)
        else:
            if in_event and len(event_vals) >= 2:
                avg_spi = float(np.mean(event_vals))
                # determine cause
                event_years = set()
                for y, m, s in spi_series:
                    if s < -1:
                        event_years.add(y)
                cause = "normal"
                if event_years & EL_NINO_YEARS:
                    cause = "el_nino"
                elif avg_spi < -1.5:
                    cause = "el_nino_suspected"

                severity = "moderate"
                if avg_spi < -2:
                    severity = "severe"
                elif avg_spi < -1.5:
                    severity = "high"

                events.append({
                    "start_month": event_start,
                    "end_month": f"{year}-{month:02d}",
                    "duration_months": len(event_vals),
                    "avg_spi": round(avg_spi, 3),
                    "severity": severity,
                    "cause": cause,
                })
            in_event = False
            event_vals = []

    # handle ongoing event
    if in_event and len(event_vals) >= 2:
        last_y, last_m, _ = spi_series[-1]
        avg_spi = float(np.mean(event_vals))
        event_years = set()
        for y, m, s in spi_series:
            if s < -1:
                event_years.add(y)
        cause = "normal"
        if event_years & EL_NINO_YEARS:
            cause = "el_nino"
        severity = "moderate"
        if avg_spi < -2:
            severity = "severe"
        elif avg_spi < -1.5:
            severity = "high"
        events.append({
            "start_month": event_start,
            "end_month": f"{last_y}-{last_m:02d}",
            "duration_months": len(event_vals),
            "avg_spi": round(avg_spi, 3),
            "severity": severity,
            "cause": cause,
            "ongoing": True,
        })

    return {"events": events, "count": len(events)}


@router.get("/forecast")
async def get_forecast(
    months: int = Query(12, ge=3, le=60, description="Jumlah bulan terakhir dianalisis"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT year, month, AVG(precip_mm) AS avg_precip
            FROM chirps_precip
            GROUP BY year, month
            ORDER BY year DESC, month DESC
            LIMIT $1
            """,
            months,
        )

    if len(rows) < 3:
        return {"trend": "insufficient_data", "months_analyzed": len(rows)}

    rows = list(reversed(rows))
    vals = [float(r["avg_precip"]) for r in rows]
    mean_p = float(np.mean(vals))
    std_p = float(np.std(vals))

    spi_vals = [(v - mean_p) / std_p if std_p > 0 else 0.0 for v in vals]

    # Check last 3 months trend
    last3 = spi_vals[-3:]
    decreasing = all(last3[i] > last3[i + 1] for i in range(len(last3) - 1))
    increasing = all(last3[i] < last3[i + 1] for i in range(len(last3) - 1))

    if decreasing:
        trend = "drought_developing"
    elif increasing:
        trend = "recovery"
    else:
        trend = "stable"

    el_nino_warning = False
    if len(spi_vals) >= 3:
        recent_avg = float(np.mean(spi_vals[-3:]))
        latest_year = rows[-1]["year"]
        if recent_avg < -1.5 and latest_year in EL_NINO_YEARS:
            el_nino_warning = True

    return {
        "trend": trend,
        "current_spi": round(spi_vals[-1], 3),
        "spi_last_3": [round(s, 3) for s in spi_vals[-3:]],
        "months_analyzed": len(rows),
        "el_nino_warning": el_nino_warning,
        "period": f"{rows[0]['year']}-{rows[0]['month']:02d} to {rows[-1]['year']}-{rows[-1]['month']:02d}",
    }
