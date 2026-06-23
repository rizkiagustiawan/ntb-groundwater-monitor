#!/usr/bin/env python3
"""GRACE Downscaling via ML — predict well-level GWS from satellite data."""
import os
import sys
import logging
import json
import numpy as np
import psycopg2
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@localhost:5435/ntb_groundwater")


def get_training_data(conn):
    """Extract aligned multi-sensor data for wells with measurements."""
    cur = conn.cursor()

    cur.execute("""
        SELECT w.id, w.well_code, w.lat, w.lon, w.depth_m,
               COUNT(m.id) as n_meas
        FROM wells w
        JOIN measurements m ON m.well_id = w.id
        GROUP BY w.id, w.well_code, w.lat, w.lon, w.depth_m
        HAVING COUNT(m.id) >= 12
    """)
    wells = cur.fetchall()
    log.info(f"Found {len(wells)} wells with 12+ measurements")

    features = []
    targets = []
    metadata = []

    for well_id, well_code, wlat, wlon, depth, n_meas in wells:
        cur.execute("""
            SELECT DATE_TRUNC('month', measured_at) as month,
                   AVG(water_level_m) as avg_wl
            FROM measurements
            WHERE well_id = %s
            GROUP BY DATE_TRUNC('month', measured_at)
            ORDER BY month
        """, (well_id,))
        measurements = cur.fetchall()

        for month, avg_wl in measurements:
            year, mo = month.year, month.month

            cur.execute("""
                SELECT tws_anomaly FROM grace_tws
                WHERE EXTRACT(YEAR FROM period_date) = %s
                  AND EXTRACT(MONTH FROM period_date) = %s
                ORDER BY ABS(lat - %s) + ABS(lon - %s)
                LIMIT 1
            """, (year, mo, wlat, wlon))
            grace_row = cur.fetchone()

            cur.execute("""
                SELECT sms_anomaly FROM gldas_sms
                WHERE year = %s AND month = %s
                ORDER BY ABS(lat - %s) + ABS(lon - %s)
                LIMIT 1
            """, (year, mo, wlat, wlon))
            gldas_row = cur.fetchone()

            cur.execute("""
                SELECT precip_mm FROM chirps_precip
                WHERE year = %s AND month = %s
                ORDER BY ABS(lat - %s) + ABS(lon - %s)
                LIMIT 1
            """, (year, mo, wlat, wlon))
            chirps_row = cur.fetchone()

            cur.execute("""
                SELECT ndvi FROM sentinel2_ndvi
                WHERE EXTRACT(YEAR FROM period_date) = %s
                  AND EXTRACT(MONTH FROM period_date) = %s
                ORDER BY ABS(lat - %s) + ABS(lon - %s)
                LIMIT 1
            """, (year, mo, wlat, wlon))
            ndvi_row = cur.fetchone()

            tws = float(grace_row[0]) if grace_row and grace_row[0] is not None else 0.0
            sms = float(gldas_row[0]) if gldas_row and gldas_row[0] is not None else 0.0
            precip = float(chirps_row[0]) if chirps_row and chirps_row[0] is not None else 0.0
            ndvi = float(ndvi_row[0]) if ndvi_row and ndvi_row[0] is not None else 0.0

            month_sin = np.sin(2 * np.pi * mo / 12)
            month_cos = np.cos(2 * np.pi * mo / 12)

            feature = [tws, sms, precip, ndvi, month_sin, month_cos, wlat, wlon]
            features.append(feature)
            targets.append(float(avg_wl))
            metadata.append({"well_code": well_code, "month": month.strftime("%Y-%m")})

    cur.close()
    return np.array(features), np.array(targets), metadata


def train_model(X, y):
    """Train XGBoost or GradientBoosting model."""
    from sklearn.model_selection import cross_val_score
    from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
    from sklearn.ensemble import GradientBoostingRegressor

    try:
        import xgboost as xgb
        model = xgb.XGBRegressor(
            n_estimators=100, max_depth=6, learning_rate=0.1,
            subsample=0.8, colsample_bytree=0.8, random_state=42
        )
        log.info("Using XGBoost")
    except ImportError:
        model = GradientBoostingRegressor(
            n_estimators=100, max_depth=6, learning_rate=0.1, random_state=42
        )
        log.info("XGBoost not available, using GradientBoosting")

    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    r2 = r2_score(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    mae = mean_absolute_error(y_test, y_pred)

    log.info(f"Test R²: {r2:.4f}")
    log.info(f"Test RMSE: {rmse:.4f}")
    log.info(f"Test MAE: {mae:.4f}")

    feature_names = ["tws", "sms", "precip", "ndvi", "month_sin", "month_cos", "lat", "lon"]
    importances = model.feature_importances_
    for name, imp in sorted(zip(feature_names, importances), key=lambda x: -x[1]):
        log.info(f"  {name}: {imp:.4f}")

    return model, {"r2": float(r2), "rmse": float(rmse), "mae": float(mae)}


def save_model(model, metrics, output_dir="data/models"):
    """Save trained model."""
    os.makedirs(output_dir, exist_ok=True)

    try:
        import joblib
        joblib.dump(model, f"{output_dir}/grace_downscale.joblib")
        log.info(f"Model saved to {output_dir}/grace_downscale.joblib")
    except ImportError:
        import pickle
        with open(f"{output_dir}/grace_downscale.pkl", "wb") as f:
            pickle.dump(model, f)
        log.info(f"Model saved to {output_dir}/grace_downscale.pkl")

    with open(f"{output_dir}/metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)


def main():
    log.info("Starting GRACE downscale training")

    conn = psycopg2.connect(DATABASE_URL)
    X, y, metadata = get_training_data(conn)
    conn.close()

    if len(X) < 50:
        log.error(f"Not enough training data: {len(X)} samples. Need 50+.")
        sys.exit(1)

    log.info(f"Training data: {X.shape[0]} samples, {X.shape[1]} features")

    model, metrics = train_model(X, y)
    save_model(model, metrics)

    log.info("Training complete!")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
