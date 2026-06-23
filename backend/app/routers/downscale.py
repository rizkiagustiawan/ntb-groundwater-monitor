from fastapi import APIRouter, Query
import os
import json
import numpy as np

from app.db import get_pool

router = APIRouter(prefix="/downscale", tags=["downscale"])

MODEL_DIR = "data/models"


def load_model():
    """Load trained model if available."""
    try:
        import joblib
        path = f"{MODEL_DIR}/grace_downscale.joblib"
        if os.path.exists(path):
            return joblib.load(path)
    except (ImportError, Exception):
        pass
    try:
        import pickle
        path = f"{MODEL_DIR}/grace_downscale.pkl"
        if os.path.exists(path):
            with open(path, "rb") as f:
                return pickle.load(f)
    except (ImportError, Exception):
        pass
    return None


@router.get("/predict")
async def predict_gws(
    well_id: int = Query(..., description="Well ID"),
    months: int = Query(12, ge=1, le=60)
):
    """Predict well-level GWS using downscaled GRACE model."""
    model = load_model()
    if model is None:
        return {"error": "Model not trained. Run: python3 scripts/grace_downscale.py"}

    pool = await get_pool()
    async with pool.acquire() as conn:
        well = await conn.fetchrow("SELECT * FROM wells WHERE id = $1", well_id)
        if not well:
            return {"error": f"Well {well_id} not found"}

        rows = await conn.fetch("""
            SELECT t.period_date, t.tws_anomaly,
                   s.sms_anomaly, c.precip_mm, n.ndvi
            FROM grace_tws t
            LEFT JOIN gldas_sms s ON
                EXTRACT(YEAR FROM t.period_date) = s.year AND
                EXTRACT(MONTH FROM t.period_date) = s.month AND
                ABS(t.lat - s.lat) < 0.01 AND ABS(t.lon - s.lon) < 0.01
            LEFT JOIN chirps_precip c ON
                EXTRACT(YEAR FROM t.period_date) = c.year AND
                EXTRACT(MONTH FROM t.period_date) = c.month AND
                ABS(t.lat - c.lat) < 0.1 AND ABS(t.lon - c.lon) < 0.1
            LEFT JOIN LATERAL (
                SELECT ndvi FROM sentinel2_ndvi
                ORDER BY ABS(lat - $2) + ABS(lon - $3)
                LIMIT 1
            ) n ON TRUE
            WHERE ABS(t.lat - $2) < 0.3 AND ABS(t.lon - $3) < 0.3
            ORDER BY t.period_date DESC
            LIMIT $4
        """, well_id, float(well["lat"]), float(well["lon"]), months)

        predictions = []
        for r in rows:
            mo = r["period_date"].month
            tws = float(r["tws_anomaly"]) if r["tws_anomaly"] else 0.0
            sms = float(r["sms_anomaly"]) if r["sms_anomaly"] else 0.0
            precip = float(r["precip_mm"]) if r["precip_mm"] else 0.0
            ndvi = float(r["ndvi"]) if r["ndvi"] else 0.0
            month_sin = np.sin(2 * np.pi * mo / 12)
            month_cos = np.cos(2 * np.pi * mo / 12)

            features = np.array([[tws, sms, precip, ndvi, month_sin, month_cos,
                                  float(well["lat"]), float(well["lon"])]])
            pred = model.predict(features)[0]

            predictions.append({
                "period": r["period_date"].strftime("%Y-%m"),
                "predicted_water_level_m": round(float(pred), 3),
                "tws_anomaly": tws,
                "gws_estimate": round(tws - sms, 2),
            })

        return {
            "well": {"id": well["id"], "code": well["well_code"], "name": well["name"]},
            "predictions": list(reversed(predictions)),
        }


@router.get("/metrics")
async def get_model_metrics():
    """Return trained model metrics."""
    metrics_path = f"{MODEL_DIR}/metrics.json"
    if os.path.exists(metrics_path):
        with open(metrics_path) as f:
            return json.load(f)
    return {"error": "Model not trained yet"}


@router.get("/train")
async def trigger_training():
    """Trigger model training (runs synchronously, may take a while)."""
    import subprocess
    try:
        result = subprocess.run(
            ["python3", "scripts/grace_downscale.py"],
            capture_output=True, text=True, timeout=300
        )
        return {
            "status": "completed" if result.returncode == 0 else "failed",
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "Training took too long (>5min)"}
