#!/usr/bin/env python3
"""Standalone ML training — reads from CSV/NetCDF, no database needed."""
import os
import sys
import json
import logging
import pickle
import numpy as np
import pandas as pd
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = "data"
MODEL_DIR = "data/models"


def load_grace_nc(nc_path):
    """Load GRACE NetCDF and extract NTB grid points."""
    import xarray as xr

    ds = xr.open_dataset(nc_path)
    log.info(f"GRACE variables: {list(ds.data_vars)}")

    lat_range = slice(-9.25, -7.75)
    lon_range = slice(115.75, 119.25)

    ntb = ds.sel(lat=lat_range, lon=lon_range)

    if "lwe_thickness" in ntb:
        var = "lwe_thickness"
    else:
        var = list(ntb.data_vars)[0]

    records = []
    for t in ntb.time.values:
        date = pd.Timestamp(t)
        for lat in ntb.lat.values:
            for lon in ntb.lon.values:
                val = float(ntb[var].sel(time=t, lat=lat, lon=lon).values)
                if not np.isnan(val):
                    records.append(
                        {
                            "date": date,
                            "lat": float(lat),
                            "lon": float(lon),
                            "tws_anomaly": val,
                        }
                    )

    df = pd.DataFrame(records)
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    log.info(f"GRACE: {len(df)} records, {df['date'].nunique()} months")
    return df


def load_gldas_csv(csv_path):
    """Load GLDAS CSV."""
    df = pd.read_csv(csv_path)
    df["sms_cm_ewh"] = pd.to_numeric(df["sms_cm_ewh"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["sms_cm_ewh"])
    log.info(f"GLDAS: {len(df)}/{before} records with valid sms_cm_ewh")
    return df


def load_chirps_csv(csv_path):
    """Load CHIRPS CSV."""
    df = pd.read_csv(csv_path)
    log.info(f"CHIRPS: {len(df)} records")
    return df


def load_ndvi_csv(csv_path):
    """Load Sentinel-2 NDVI CSV."""
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip().str.strip('"')
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["ndvi"] = pd.to_numeric(df["ndvi"], errors="coerce")
    df["period_date"] = pd.to_datetime(df["period_date"])
    df["year"] = df["period_date"].dt.year
    df["month"] = df["period_date"].dt.month
    log.info(f"NDVI: {len(df)} records, {df['location'].nunique()} locations")
    return df


def build_features(grace_df, gldas_df, chirps_df, ndvi_df):
    """Build aligned feature matrix from all data sources."""

    features = []
    targets = []
    skipped = 0

    for _, row in grace_df.iterrows():
        lat, lon, year, month = row["lat"], row["lon"], row["year"], row["month"]
        tws = row["tws_anomaly"]

        gldas_match = gldas_df[
            (gldas_df["year"] == year)
            & (gldas_df["month"] == month)
            & (abs(gldas_df["lat"] - lat) < 0.01)
            & (abs(gldas_df["lon"] - lon) < 0.01)
        ]
        if len(gldas_match) > 0:
            sms = float(gldas_match["sms_cm_ewh"].iloc[0])
        else:
            sms = 0.0

        chirps_match = chirps_df[
            (chirps_df["year"] == year)
            & (chirps_df["month"] == month)
            & (abs(chirps_df["lat"] - lat) < 0.1)
            & (abs(chirps_df["lon"] - lon) < 0.1)
        ]
        precip = float(chirps_match["precip_mm"].iloc[0]) if len(chirps_match) > 0 else 0.0

        ndvi_match = ndvi_df[(ndvi_df["year"] == year) & (ndvi_df["month"] == month)]
        if len(ndvi_match) > 0:
            dists = abs(ndvi_match["lat"] - lat) + abs(ndvi_match["lon"] - lon)
            ndvi_val = float(ndvi_match.loc[dists.idxmin(), "ndvi"])
        else:
            ndvi_val = 0.5

        month_sin = np.sin(2 * np.pi * month / 12)
        month_cos = np.cos(2 * np.pi * month / 12)

        features.append([sms, precip, ndvi_val, month_sin, month_cos, lat, lon])
        targets.append(tws)

    X = np.array(features)
    y = np.array(targets)

    feature_names = ["sms", "precip", "ndvi", "month_sin", "month_cos", "lat", "lon"]
    log.info(f"Feature matrix: {X.shape}, Target: {y.shape}")
    return X, y, feature_names


def train_models(X, y, feature_names):
    """Train multiple models and compare."""
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    results = {}

    # 1. XGBoost
    try:
        import xgboost as xgb

        model = xgb.XGBRegressor(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
        )
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        metrics = {
            "r2": float(r2_score(y_test, y_pred)),
            "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))),
            "mae": float(mean_absolute_error(y_test, y_pred)),
            "feature_importance": dict(
                zip(feature_names, [float(x) for x in model.feature_importances_])
            ),
        }
        results["xgboost"] = {"model": model, "metrics": metrics}
        log.info(f"XGBoost: R²={metrics['r2']:.4f}, RMSE={metrics['rmse']:.4f}")
    except Exception as e:
        log.warning(f"XGBoost failed: {e}")

    # 2. Random Forest
    try:
        from sklearn.ensemble import RandomForestRegressor

        model = RandomForestRegressor(
            n_estimators=200, max_depth=10, random_state=42, n_jobs=-1
        )
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        metrics = {
            "r2": float(r2_score(y_test, y_pred)),
            "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))),
            "mae": float(mean_absolute_error(y_test, y_pred)),
            "feature_importance": dict(
                zip(feature_names, [float(x) for x in model.feature_importances_])
            ),
        }
        results["random_forest"] = {"model": model, "metrics": metrics}
        log.info(f"Random Forest: R²={metrics['r2']:.4f}, RMSE={metrics['rmse']:.4f}")
    except Exception as e:
        log.warning(f"Random Forest failed: {e}")

    # 3. Gradient Boosting (sklearn fallback if LightGBM unavailable)
    try:
        from sklearn.ensemble import GradientBoostingRegressor

        model = GradientBoostingRegressor(
            n_estimators=200, max_depth=8, learning_rate=0.05, random_state=42
        )
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        metrics = {
            "r2": float(r2_score(y_test, y_pred)),
            "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))),
            "mae": float(mean_absolute_error(y_test, y_pred)),
            "feature_importance": dict(
                zip(feature_names, [float(x) for x in model.feature_importances_])
            ),
        }
        results["gradient_boosting"] = {"model": model, "metrics": metrics}
        log.info(f"GradientBoosting: R²={metrics['r2']:.4f}, RMSE={metrics['rmse']:.4f}")
    except Exception as e:
        log.warning(f"GradientBoosting failed: {e}")

    return results


def save_models(results, feature_names):
    """Save trained models and metrics."""
    os.makedirs(MODEL_DIR, exist_ok=True)

    for name, data in results.items():
        path = f"{MODEL_DIR}/grace_downscale_{name}.pkl"
        with open(path, "wb") as f:
            pickle.dump(data["model"], f)
        log.info(f"Saved {name} to {path}")

    metrics_summary = {
        "trained_at": datetime.now().isoformat(),
        "feature_names": feature_names,
        "models": {name: data["metrics"] for name, data in results.items()},
    }

    with open(f"{MODEL_DIR}/metrics.json", "w") as f:
        json.dump(metrics_summary, f, indent=2)

    best_name = max(results.keys(), key=lambda k: results[k]["metrics"]["r2"])
    best_metrics = results[best_name]["metrics"]

    log.info(f"\n{'='*60}")
    log.info(f"BEST MODEL: {best_name}")
    log.info(f"  R²:   {best_metrics['r2']:.4f}")
    log.info(f"  RMSE: {best_metrics['rmse']:.4f}")
    log.info(f"  MAE:  {best_metrics['mae']:.4f}")
    log.info(f"\nFeature importance:")
    for feat, imp in sorted(
        best_metrics["feature_importance"].items(), key=lambda x: -x[1]
    ):
        log.info(f"  {feat}: {imp:.4f}")
    log.info(f"{'='*60}")

    return best_name, best_metrics


def main():
    log.info("Starting standalone ML training")

    grace_nc = os.path.join(
        DATA_DIR,
        "grace",
        "GRCTellus.JPL.200204_202512.GLO.RL06.3M.MSCNv04CRI.nc",
    )
    gldas_csv = os.path.join(DATA_DIR, "gldas", "gldas_sms_ntb.csv")
    chirps_csv = os.path.join(DATA_DIR, "chirps", "chirps_ntb.csv")
    ndvi_csv = os.path.join(DATA_DIR, "sentinel2", "ntb_ndvi_timeseries.csv")

    for p in [grace_nc, gldas_csv, chirps_csv, ndvi_csv]:
        if not os.path.exists(p):
            log.error(f"Missing: {p}")
            sys.exit(1)

    grace_df = load_grace_nc(grace_nc)
    gldas_df = load_gldas_csv(gldas_csv)
    chirps_df = load_chirps_csv(chirps_csv)
    ndvi_df = load_ndvi_csv(ndvi_csv)

    X, y, feature_names = build_features(grace_df, gldas_df, chirps_df, ndvi_df)

    if len(X) < 100:
        log.error(f"Not enough data: {len(X)} samples")
        sys.exit(1)

    results = train_models(X, y, feature_names)

    if not results:
        log.error("No models trained successfully")
        sys.exit(1)

    best_name, best_metrics = save_models(results, feature_names)
    log.info("Training complete!")


if __name__ == "__main__":
    main()
