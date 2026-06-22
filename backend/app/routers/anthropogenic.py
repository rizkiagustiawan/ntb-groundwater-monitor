"""
Anthropogenic Signal Detection — identify areas where groundwater loss
exceeds what climate (rainfall) alone would explain.

Scientific basis: If rainfall is normal/above-average but GWS is declining,
the signal points to human extraction (pumping, mining).
Ref: Sudradjat et al. 2026, TRANSPARENCY.md.
"""

import math
from datetime import date

import numpy as np
from fastapi import APIRouter, Query

from app.db import get_pool

router = APIRouter(prefix="/anthropogenic", tags=["anthropogenic"])

KABUPATEN_CENTROIDS = [
    {"name": "Sumbawa", "lat": -8.4911, "lon": 117.4203},
    {"name": "Sumbawa Barat", "lat": -8.9833, "lon": 116.8500},
    {"name": "Dompu", "lat": -8.5364, "lon": 118.4614},
    {"name": "Bima", "lat": -8.5394, "lon": 118.6869},
    {"name": "Kota Bima", "lat": -8.4667, "lon": 118.7333},
    {"name": "Lombok Utara", "lat": -8.3500, "lon": 116.2833},
]

GWS_DECLINE_THRESHOLD = -2.0  # cm/year


def linear_trend(dates: list, values: list) -> dict:
    """Calculate linear trend. Returns slope (per year), intercept, r_squared."""
    x = np.array([(d - dates[0]).days / 365.25 for d in dates])
    y = np.array(values, dtype=float)
    mask = ~np.isnan(y)
    if mask.sum() < 3:
        return {"slope_per_year": None, "r_squared": None}
    coeffs = np.polyfit(x[mask], y[mask], 1)
    y_pred = np.polyval(coeffs, x)
    ss_res = np.sum((y[mask] - y_pred[mask]) ** 2)
    ss_tot = np.sum((y[mask] - np.mean(y[mask])) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return {"slope_per_year": float(coeffs[0]), "r_squared": float(r_squared)}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_kabupaten(lat: float, lon: float) -> str:
    best, best_dist = None, float("inf")
    for k in KABUPATEN_CENTROIDS:
        d = _haversine_km(lat, lon, k["lat"], k["lon"])
        if d < best_dist:
            best, best_dist = k["name"], d
    return best


def _classify_signal(gws_slope: float | None, rain_slope: float | None) -> str:
    if gws_slope is None or rain_slope is None:
        return "insufficient_data"
    if gws_slope < GWS_DECLINE_THRESHOLD and rain_slope >= 0:
        return "anthropogenic"
    if gws_slope < 0 and rain_slope < 0:
        return "natural"
    if gws_slope >= 0 and rain_slope >= 0:
        return "recovery"
    if gws_slope >= 0 and rain_slope < 0:
        return "recovery"
    return "inconclusive"


def _confidence(months: int) -> str:
    if months >= 24:
        return "high"
    if months >= 12:
        return "medium"
    return "low"


async def _fetch_region_data(conn, months: int | None = None) -> list:
    limit_clause = f"ORDER BY period_date DESC LIMIT {months}" if months else ""
    rows = await conn.fetch(
        f"""
        SELECT lat, lon, period_date, gws_anomaly, chirps_precip_mm
        FROM unified_monitoring
        WHERE gws_anomaly IS NOT NULL
        ORDER BY lat, lon, period_date
        """
    )
    return rows


def _group_by_grid(rows) -> dict:
    grid: dict[tuple[float, float], list] = {}
    for r in rows:
        key = (float(r["lat"]), float(r["lon"]))
        grid.setdefault(key, []).append(r)
    return grid


@router.get("/signals")
async def get_signals(
    months: int = Query(None, ge=6, le=120, description="Limit to last N months (None = all data)"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _fetch_region_data(conn, months)

    grid = _group_by_grid(rows)
    signals = []

    for (lat, lon), records in grid.items():
        dates = [r["period_date"] for r in records]
        gws_vals = [float(r["gws_anomaly"]) for r in records]
        rain_vals = [float(r["chirps_precip_mm"]) for r in records if r["chirps_precip_mm"] is not None]

        gws_trend = linear_trend(dates, gws_vals)
        rain_dates = [r["period_date"] for r in records if r["chirps_precip_mm"] is not None]
        rain_trend = linear_trend(rain_dates, rain_vals) if len(rain_vals) >= 3 else {"slope_per_year": None, "r_squared": None}

        signal = _classify_signal(gws_trend["slope_per_year"], rain_trend["slope_per_year"])
        n_months = len(dates)

        signals.append({
            "lat": lat,
            "lon": lon,
            "gws_trend_cm_per_year": round(gws_trend["slope_per_year"], 3) if gws_trend["slope_per_year"] is not None else None,
            "gws_r_squared": round(gws_trend["r_squared"], 4) if gws_trend["r_squared"] is not None else None,
            "rain_trend_mm_per_year": round(rain_trend["slope_per_year"], 3) if rain_trend["slope_per_year"] is not None else None,
            "rain_r_squared": round(rain_trend["r_squared"], 4) if rain_trend["r_squared"] is not None else None,
            "signal": signal,
            "confidence": _confidence(n_months),
            "months_of_data": n_months,
            "period": f"{dates[0].isoformat()} to {dates[-1].isoformat()}",
        })

    return {"count": len(signals), "signals": signals}


@router.get("/hotspots")
async def get_hotspots(
    months: int = Query(None, ge=6, le=120, description="Limit to last N months"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _fetch_region_data(conn, months)

    grid = _group_by_grid(rows)
    hotspots = []

    for (lat, lon), records in grid.items():
        dates = [r["period_date"] for r in records]
        gws_vals = [float(r["gws_anomaly"]) for r in records]
        rain_vals = [float(r["chirps_precip_mm"]) for r in records if r["chirps_precip_mm"] is not None]

        gws_trend = linear_trend(dates, gws_vals)
        rain_dates = [r["period_date"] for r in records if r["chirps_precip_mm"] is not None]
        rain_trend = linear_trend(rain_dates, rain_vals) if len(rain_vals) >= 3 else {"slope_per_year": None, "r_squared": None}

        signal = _classify_signal(gws_trend["slope_per_year"], rain_trend["slope_per_year"])
        if signal != "anthropogenic":
            continue

        hotspots.append({
            "lat": lat,
            "lon": lon,
            "kabupaten": _nearest_kabupaten(lat, lon),
            "gws_trend_cm_per_year": round(gws_trend["slope_per_year"], 3),
            "gws_r_squared": round(gws_trend["r_squared"], 4),
            "rain_trend_mm_per_year": round(rain_trend["slope_per_year"], 3) if rain_trend["slope_per_year"] is not None else None,
            "confidence": _confidence(len(dates)),
            "months_of_data": len(dates),
            "period": f"{dates[0].isoformat()} to {dates[-1].isoformat()}",
        })

    hotspots.sort(key=lambda h: h["gws_trend_cm_per_year"])
    return {"count": len(hotspots), "hotspots": hotspots}


@router.get("/batu-hijau")
async def get_batu_hijau(
    radius_km: float = Query(25.0, ge=1, le=100, description="Search radius in km around mine"),
):
    mine_lat, mine_lon = -8.9833, 116.85

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT lat, lon, period_date, gws_anomaly, chirps_precip_mm
            FROM unified_monitoring
            WHERE gws_anomaly IS NOT NULL
            ORDER BY period_date
            """
        )

    if not rows:
        return {"error": "no_data", "detail": "No unified_monitoring data found"}

    grid = _group_by_grid(rows)

    best_key, best_dist = None, float("inf")
    for (lat, lon) in grid:
        d = _haversine_km(mine_lat, mine_lon, lat, lon)
        if d < best_dist:
            best_key, best_dist = (lat, lon), d

    if best_dist > radius_km:
        return {
            "error": "no_data_within_radius",
            "detail": f"Nearest grid cell is {best_dist:.1f} km away (radius: {radius_km} km)",
            "nearest_lat": best_key[0],
            "nearest_lon": best_key[1],
        }

    records = grid[best_key]
    dates = [r["period_date"] for r in records]
    gws_vals = [float(r["gws_anomaly"]) for r in records]
    rain_vals = [float(r["chirps_precip_mm"]) for r in records if r["chirps_precip_mm"] is not None]
    rain_dates = [r["period_date"] for r in records if r["chirps_precip_mm"] is not None]

    gws_trend = linear_trend(dates, gws_vals)
    rain_trend = linear_trend(rain_dates, rain_vals) if len(rain_vals) >= 3 else {"slope_per_year": None, "r_squared": None}
    signal = _classify_signal(gws_trend["slope_per_year"], rain_trend["slope_per_year"])

    series = []
    for r in records:
        series.append({
            "period": r["period_date"].isoformat(),
            "gws_anomaly": round(float(r["gws_anomaly"]), 3),
            "chirps_precip_mm": round(float(r["chirps_precip_mm"]), 2) if r["chirps_precip_mm"] is not None else None,
        })

    return {
        "location": {
            "mine_lat": mine_lat,
            "mine_lon": mine_lon,
            "grid_lat": best_key[0],
            "grid_lon": best_key[1],
            "distance_km": round(best_dist, 2),
        },
        "analysis": {
            "gws_trend_cm_per_year": round(gws_trend["slope_per_year"], 3) if gws_trend["slope_per_year"] is not None else None,
            "gws_r_squared": round(gws_trend["r_squared"], 4) if gws_trend["r_squared"] is not None else None,
            "rain_trend_mm_per_year": round(rain_trend["slope_per_year"], 3) if rain_trend["slope_per_year"] is not None else None,
            "rain_r_squared": round(rain_trend["r_squared"], 4) if rain_trend["r_squared"] is not None else None,
            "signal": signal,
            "confidence": _confidence(len(dates)),
            "months_of_data": len(dates),
            "period": f"{dates[0].isoformat()} to {dates[-1].isoformat()}",
        },
        "series": series,
    }
