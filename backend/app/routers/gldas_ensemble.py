from fastapi import APIRouter
from datetime import datetime

from app.db import get_pool

router = APIRouter(prefix="/gldas", tags=["gldas"])


def _pearson_r(x: list, y: list) -> float | None:
    import numpy as np
    x, y = np.array(x, dtype=float), np.array(y, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    if mask.sum() < 3:
        return None
    return float(np.corrcoef(x[mask], y[mask])[0, 1])


@router.get("/compare")
async def compare_noah_grace():
    pool = await get_pool()
    async with pool.acquire() as conn:
        grid_points = await conn.fetch("""
            SELECT DISTINCT g.lat, g.lon, g.geom
            FROM gldas_sms g
            WHERE EXISTS (
                SELECT 1 FROM grace_tws t
                WHERE t.lat = g.lat AND t.lon = g.lon
            )
        """)

        results = []
        for gp in grid_points:
            sms_rows = await conn.fetch("""
                SELECT year, month, sms_anomaly
                FROM gldas_sms
                WHERE lat = $1 AND lon = $2
                ORDER BY year, month
            """, gp["lat"], gp["lon"])

            grace_rows = await conn.fetch("""
                SELECT period_date, tws_anomaly
                FROM grace_tws
                WHERE lat = $1 AND lon = $2
                ORDER BY period_date
            """, gp["lat"], gp["lon"])

            sms_map = {}
            for r in sms_rows:
                if r["sms_anomaly"] is not None:
                    key = f"{r['year']}-{int(r['month']):02d}"
                    sms_map[key] = float(r["sms_anomaly"])

            grace_map = {}
            for r in grace_rows:
                if r["tws_anomaly"] is not None:
                    key = r["period_date"].strftime("%Y-%m")
                    grace_map[key] = float(r["tws_anomaly"])

            common = sorted(set(sms_map) & set(grace_map))
            if len(common) < 6:
                continue

            sms_vals = [sms_map[m] for m in common]
            grace_vals = [grace_map[m] for m in common]

            r = _pearson_r(sms_vals, grace_vals)
            bias = sum(s - g for s, g in zip(sms_vals, grace_vals)) / len(common)
            rmse = (sum((s - g) ** 2 for s, g in zip(sms_vals, grace_vals)) / len(common)) ** 0.5

            results.append({
                "lat": float(gp["lat"]),
                "lon": float(gp["lon"]),
                "n_months": len(common),
                "correlation": round(r, 4) if r is not None else None,
                "bias_cm": round(bias, 4),
                "rmse_cm": round(rmse, 4),
                "classification": "strong" if r and abs(r) > 0.7 else "moderate" if r and abs(r) > 0.4 else "weak" if r and abs(r) > 0.2 else "none",
            })

        results.sort(key=lambda x: abs(x["correlation"] or 0), reverse=True)
        return {
            "description": "GLDAS Noah SMS vs GRACE TWS comparison per grid point",
            "metric": "SMS anomaly compared to TWS anomaly",
            "grid_points": results,
            "total": len(results),
            "generated_at": datetime.now().isoformat(),
        }


@router.get("/uncertainty")
async def glads_uncertainty():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT lat, lon, year, month, sms_cm_ewh, sms_anomaly
            FROM gldas_sms
            ORDER BY lat, lon, year, month
        """)

        results = []
        for r in rows:
            sms = float(r["sms_cm_ewh"]) if r["sms_cm_ewh"] is not None else 0.0
            results.append({
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "year": int(r["year"]),
                "month": int(r["month"]),
                "sms_cm_ewh": round(sms, 4),
                "sms_uncertainty_20pct": round(sms * 0.20, 4),
                "sms_uncertainty_30pct": round(sms * 0.30, 4),
                "effect_on_gws_20pct": "GWS uncertainty increases by sqrt(sigma_TWS^2 + (0.20*sms)^2)",
                "effect_on_gws_30pct": "GWS uncertainty increases by sqrt(sigma_TWS^2 + (0.30*sms)^2)",
            })

        return {
            "description": "GLDAS Noah SMS uncertainty estimation based on published literature",
            "source": "TRANSPARENCY.md: 20-30% uncertainty in soil moisture estimation",
            "reference": "Rodell et al. (2009); Tangdamrongsub et al. (2023)",
            "uncertainty_method": "Percentage-based: sigma_SMS = pct * SMS_value",
            "error_propagation": "sigma_GWS = sqrt(sigma_TWS^2 + sigma_SMS^2)",
            "grid_points": results,
            "total": len(results),
            "generated_at": datetime.now().isoformat(),
        }


@router.get("/recommendation")
async def glads_recommendation():
    return {
        "recommendation": {
            "current_model": "GLDAS Noah 2.1",
            "status": "adequate for regional trend analysis",
            "multi_model_ensemble": {
                "models": ["Noah", "CLM", "VIC", "Mosaic"],
                "benefit": "Reduces model-dependent bias, provides uncertainty bounds",
                "reference": "Tangdamrongsub et al. (2023)",
                "implementation_note": "Requires downloading CLM, VIC, Mosaic data from NASA GES DISC and importing to gldas_clm, gldas_vic, gldas_mosaic tables",
            },
            "uncertainty_assumption": {
                "sms_uncertainty": "20-30% of SMS value",
                "justification": "Sparse ground station coverage in NTB; GLDAS is model-based, not direct observation",
                "recommendation": "Use 30% as conservative bound for decision-making",
            },
            "version_advice": "GLDAS-2.1 (Noah) is current standard. GLDAS-2.2 available but differences are minor for tropical regions. Multi-model ensemble (Noah+CLM+VIC+Mosaic) recommended for robustness if data available.",
            "citations": [
                "Rodell, M., Velicogna, I., & Famiglietti, J. S. (2009). Nature, 461(7266), 997-1000.",
                "Tangdamrongsub, T., et al. (2023). Multi-model ensemble for improved TWS decomposition.",
                "TRANSPARENCY.md: Platform methodology and uncertainty documentation.",
            ],
        },
        "generated_at": datetime.now().isoformat(),
    }
