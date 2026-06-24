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


def train_rf(X, y):
    """Train Random Forest model."""
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error

    model = RandomForestRegressor(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1)
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    metrics = {"r2": float(r2_score(y_test, y_pred)),
               "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))),
               "mae": float(mean_absolute_error(y_test, y_pred))}
    return model, metrics


def train_lightgbm(X, y):
    """Train LightGBM model."""
    try:
        import lightgbm as lgb
    except ImportError:
        from sklearn.ensemble import GradientBoostingRegressor as GBR
        model = GBR(n_estimators=200, max_depth=8, learning_rate=0.05, random_state=42)
        split = int(len(X) * 0.8)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
        metrics = {"r2": float(r2_score(y_test, y_pred)),
                   "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))),
                   "mae": float(mean_absolute_error(y_test, y_pred))}
        return model, metrics

    model = lgb.LGBMRegressor(n_estimators=200, max_depth=8, learning_rate=0.05, random_state=42)
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
    metrics = {"r2": float(r2_score(y_test, y_pred)),
               "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))),
               "mae": float(mean_absolute_error(y_test, y_pred))}
    return model, metrics


def train_ensemble(X, y):
    """Train all models and return ensemble."""
    models = {}
    all_metrics = {}

    for name, trainer in [("xgboost", train_model), ("rf", train_rf), ("lightgbm", train_lightgbm)]:
        try:
            model, metrics = trainer(X, y)
            models[name] = model
            all_metrics[name] = metrics
            log.info(f"  {name}: R²={metrics['r2']:.4f}, RMSE={metrics['rmse']:.4f}")
        except Exception as e:
            log.warning(f"  {name} failed: {e}")

    weights = {}
    total_inv_rmse = sum(1/m["rmse"] for m in all_metrics.values() if m["rmse"] > 0)
    for name, metrics in all_metrics.items():
        weights[name] = (1/metrics["rmse"]) / total_inv_rmse if metrics["rmse"] > 0 else 1/len(all_metrics)

    log.info(f"  Ensemble weights: {weights}")
    return models, weights, all_metrics


def save_model(model_or_models, metrics_or_weights, extra_metrics=None, output_dir="data/models", model_type="xgboost"):
    """Save trained model(s). Supports single model or ensemble."""
    os.makedirs(output_dir, exist_ok=True)

    if model_type == "ensemble":
        models = model_or_models
        weights = metrics_or_weights
        all_metrics = extra_metrics
        data = {"models": models, "weights": weights, "metrics": all_metrics}
        try:
            import joblib
            joblib.dump(data, f"{output_dir}/grace_downscale_ensemble.joblib")
            log.info(f"Ensemble saved to {output_dir}/grace_downscale_ensemble.joblib")
        except ImportError:
            import pickle
            with open(f"{output_dir}/grace_downscale_ensemble.pkl", "wb") as f:
                pickle.dump(data, f)
        with open(f"{output_dir}/metrics.json", "w") as f:
            json.dump(all_metrics, f, indent=2)
    else:
        model = model_or_models
        metrics = metrics_or_weights
        try:
            import joblib
            joblib.dump(model, f"{output_dir}/grace_downscale_{model_type}.joblib")
            log.info(f"Model saved to {output_dir}/grace_downscale_{model_type}.joblib")
        except ImportError:
            import pickle
            with open(f"{output_dir}/grace_downscale_{model_type}.pkl", "wb") as f:
                pickle.dump(model, f)
        with open(f"{output_dir}/metrics.json", "w") as f:
            json.dump({model_type: metrics}, f, indent=2)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["xgboost", "rf", "lightgbm", "ensemble"], default="ensemble")
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--end-year", type=int, default=2026)
    args = parser.parse_args()

    log.info(f"Starting GRACE downscale training — model={args.model}")

    conn = psycopg2.connect(DATABASE_URL)
    X, y, metadata = get_training_data(conn)
    conn.close()

    if len(X) < 50:
        log.error(f"Not enough training data: {len(X)} samples. Need 50+.")
        sys.exit(1)

    log.info(f"Training data: {X.shape[0]} samples, {X.shape[1]} features")

    if args.model == "ensemble":
        models, weights, all_metrics = train_ensemble(X, y)
        save_model(models, weights, all_metrics, model_type="ensemble")
        print("\n=== Model Comparison ===")
        print(f"{'Model':<12} {'R²':>8} {'RMSE':>8} {'MAE':>8}")
        print("-" * 40)
        for name, m in all_metrics.items():
            print(f"{name:<12} {m['r2']:>8.4f} {m['rmse']:>8.4f} {m['mae']:>8.4f}")
        print(f"\nEnsemble weights: {weights}")
    elif args.model == "rf":
        model, metrics = train_rf(X, y)
        save_model(model, metrics, model_type="rf")
        print(json.dumps(metrics, indent=2))
    elif args.model == "lightgbm":
        model, metrics = train_lightgbm(X, y)
        save_model(model, metrics, model_type="lightgbm")
        print(json.dumps(metrics, indent=2))
    else:
        model, metrics = train_model(X, y)
        save_model(model, metrics, model_type="xgboost")
        print(json.dumps(metrics, indent=2))

    log.info("Training complete!")


if __name__ == "__main__":
    main()
