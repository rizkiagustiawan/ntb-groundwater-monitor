from fastapi import APIRouter

from app.db import get_pool

router = APIRouter(prefix="/potential", tags=["potential"])

NTB_BBOX = (115.5, -9.5, 120.0, -7.5)
GRID_STEP = 0.1


def _ndvi_score(ndvi: float) -> float:
    if ndvi >= 0.5:
        return 100
    if ndvi >= 0.3:
        return 70
    if ndvi >= 0.1:
        return 40
    return 10


def _rain_score(precip: float) -> float:
    if precip >= 200:
        return 100
    if precip >= 100:
        return 70
    if precip >= 50:
        return 40
    return 10


def _potential_class(score: float) -> str:
    if score >= 80:
        return "very_high"
    if score >= 60:
        return "high"
    if score >= 40:
        return "moderate"
    if score >= 20:
        return "low"
    return "very_low"


POTENTIAL_COLORS = {
    "very_high": "#1a9850",
    "high": "#91cf60",
    "moderate": "#fee08b",
    "low": "#fc8d59",
    "very_low": "#d73027",
}

BASE_SCORE = 50.0


@router.get("/map")
async def get_potential_map():
    pool = await get_pool()
    async with pool.acquire() as conn:
        ndvi_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (location) location, lat, lon, ndvi
            FROM sentinel2_ndvi
            ORDER BY location, period_date DESC
            """
        )
        precip_rows = await conn.fetch(
            """
            SELECT lat, lon, AVG(precip_mm) AS avg_precip
            FROM chirps_precip
            GROUP BY lat, lon
            """
        )

    import math

    def nearest(rows, plat, plon, key_lat="lat", key_lon="lon"):
        best, best_d = None, float("inf")
        for r in rows:
            d = math.hypot(float(r[key_lat]) - plat, float(r[key_lon]) - plon)
            if d < best_d:
                best_d = d
                best = r
        return best if best_d <= 0.5 else None

    lon_min, lat_min, lon_max, lat_max = NTB_BBOX
    features = []
    lat = lat_min
    while lat <= lat_max:
        lon = lon_min
        while lon <= lon_max:
            ndvi_r = nearest(ndvi_rows, lat, lon)
            precip_r = nearest(precip_rows, lat, lon)

            ndvi_val = float(ndvi_r["ndvi"]) if ndvi_r else 0.0
            precip_val = float(precip_r["avg_precip"]) if precip_r else 0.0

            score = (
                0.40 * _ndvi_score(ndvi_val)
                + 0.30 * _rain_score(precip_val)
                + 0.30 * BASE_SCORE
            )
            zone = _potential_class(score)

            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [round(lon, 2), round(lat, 2)]},
                    "properties": {
                        "score": round(score, 1),
                        "zone": zone,
                        "color": POTENTIAL_COLORS[zone],
                        "ndvi": round(ndvi_val, 3),
                        "precip_mm": round(precip_val, 1),
                    },
                }
            )
            lon = round(lon + GRID_STEP, 1)
        lat = round(lat + GRID_STEP, 1)

    return {
        "type": "FeatureCollection",
        "metadata": {
            "title": "Peta Zona Potensi Air Tanah NTB",
            "method": "Simplified AHP (Atmaja & Putra 2019, Razi et al. 2024)",
            "weights": {"ndvi": 0.40, "rainfall": 0.30, "base": 0.30},
            "grid_step_deg": GRID_STEP,
            "bbox": list(NTB_BBOX),
        },
        "features": features,
    }


@router.get("/summary")
async def get_potential_summary():
    pool = await get_pool()
    async with pool.acquire() as conn:
        ndvi_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (location) location, lat, lon, ndvi
            FROM sentinel2_ndvi
            ORDER BY location, period_date DESC
            """
        )
        precip_rows = await conn.fetch(
            """
            SELECT lat, lon, AVG(precip_mm) AS avg_precip
            FROM chirps_precip
            GROUP BY lat, lon
            """
        )

    import math

    def nearest(rows, plat, plon):
        best, best_d = None, float("inf")
        for r in rows:
            d = math.hypot(float(r["lat"]) - plat, float(r["lon"]) - plon)
            if d < best_d:
                best_d = d
                best = r
        return best if best_d <= 0.5 else None

    counts = {"very_high": 0, "high": 0, "moderate": 0, "low": 0, "very_low": 0}
    lon_min, lat_min, lon_max, lat_max = NTB_BBOX
    lat = lat_min
    while lat <= lat_max:
        lon = lon_min
        while lon <= lon_max:
            ndvi_r = nearest(ndvi_rows, lat, lon)
            precip_r = nearest(precip_rows, lat, lon)
            ndvi_val = float(ndvi_r["ndvi"]) if ndvi_r else 0.0
            precip_val = float(precip_r["avg_precip"]) if precip_r else 0.0
            score = (
                0.40 * _ndvi_score(ndvi_val)
                + 0.30 * _rain_score(precip_val)
                + 0.30 * BASE_SCORE
            )
            counts[_potential_class(score)] += 1
            lon = round(lon + GRID_STEP, 1)
        lat = round(lat + GRID_STEP, 1)

    total = sum(counts.values())
    return {"total_grid_points": total, **counts}


@router.get("/validate")
async def validate_potential():
    pool = await get_pool()
    async with pool.acquire() as conn:
        ndvi_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (location) location, lat, lon, ndvi
            FROM sentinel2_ndvi
            ORDER BY location, period_date DESC
            """
        )
        precip_rows = await conn.fetch(
            """
            SELECT lat, lon, AVG(precip_mm) AS avg_precip
            FROM chirps_precip
            GROUP BY lat, lon
            """
        )
        well_rows = await conn.fetch(
            """
            SELECT ST_Y(geom) AS lat, ST_X(geom) AS lon FROM wells
            UNION ALL
            SELECT lat, lon FROM wells_esdm
            """
        )

    import math

    def nearest(rows, plat, plon):
        best, best_d = None, float("inf")
        for r in rows:
            d = math.hypot(float(r["lat"]) - plat, float(r["lon"]) - plon)
            if d < best_d:
                best_d = d
                best = r
        return best if best_d <= 0.5 else None

    def score_at(plat, plon):
        ndvi_r = nearest(ndvi_rows, plat, plon)
        precip_r = nearest(precip_rows, plat, plon)
        ndvi_val = float(ndvi_r["ndvi"]) if ndvi_r else 0.0
        precip_val = float(precip_r["avg_precip"]) if precip_r else 0.0
        return (
            0.40 * _ndvi_score(ndvi_val)
            + 0.30 * _rain_score(precip_val)
            + 0.30 * BASE_SCORE
        )

    categories = {"very_high": 0, "high": 0, "moderate": 0, "low": 0, "very_low": 0}
    well_results = []
    for w in well_rows:
        s = score_at(float(w["lat"]), float(w["lon"]))
        z = _potential_class(s)
        categories[z] += 1
        well_results.append(
            {
                "lat": float(w["lat"]),
                "lon": float(w["lon"]),
                "score": round(s, 1),
                "zone": z,
            }
        )

    total = len(well_rows)
    high_pct = round(
        (categories["very_high"] + categories["high"]) / total * 100, 1
    ) if total else 0

    return {
        "total_wells": total,
        "wells_in_high_potential_pct": high_pct,
        "distribution": categories,
        "wells": well_results,
    }
