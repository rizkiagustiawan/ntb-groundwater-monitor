#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-https://gw.rizkiagustiawan.tech}"

echo "==> Smoke test: $BASE_URL"

python3 - "$BASE_URL" <<'PY'
import json
import sys
import urllib.request
import ssl

# Handle self-signed certs if SSL is just being set up
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

base = sys.argv[1].rstrip("/")


def fetch_text(path: str) -> str:
    with urllib.request.urlopen(base + path, timeout=30, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_json(path: str):
    with urllib.request.urlopen(base + path, timeout=30, context=ctx) as resp:
        return json.load(resp)


def check(name: str, passed: bool, detail: str) -> None:
    prefix = "PASS" if passed else "FAIL"
    print(f"{prefix:<4} {name}: {detail}")


home_html = fetch_text("/")
check("home_has_gws_label", "Estimasi Anomali Air Tanah (GWS)" in home_html, "expect homepage contains new GWS wording")

health = fetch_json("/api/health")
check("health_ok", health.get("status") == "ok", json.dumps(health, ensure_ascii=False))

ndvi = fetch_json("/api/ndvi/summary")
check("ndvi_features", len(ndvi.get("features", [])) > 0, f"features={len(ndvi.get('features', []))}")

# Check Groundwater (GWS)
gws = fetch_json("/api/groundwater/timeseries?start_year=2020")
check("gws_metadata", gws.get("metadata", {}).get("method") == "GRACE_minus_GLDAS", "Verify GWS method in metadata")
check("gws_data_points", len(gws.get("data", [])) >= 1, f"data_count={len(gws.get('data', []))}")

# Check GRACE (TWS)
grace = fetch_json("/api/grace/timeseries?start_year=2020")
check("grace_scientific_note", "soil moisture" in grace.get("metadata", {}).get("scientific_note", ""), "Verify scientific warning in GRACE metadata")

esdm = fetch_json("/api/wells/esdm/geojson")
esdm_total = esdm.get("metadata", {}).get("total")
check("esdm_total_280", esdm_total == 280, f"total={esdm_total}")

try:
    ai = fetch_json("/api/ai/interpret")
    check("ai_interpretation", bool(ai.get("interpretation")), f"model={ai.get('ai_model')}")
except Exception as exc:
    check("ai_interpretation", False, str(exc))

try:
    req = urllib.request.Request(base + "/api/report/pdf")
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        content_type = resp.headers.get("Content-Type", "")
        check("pdf_export", resp.status == 200 and "application/pdf" in content_type, f"status={resp.status} content_type={content_type}")
except Exception as exc:
    check("pdf_export", False, str(exc))

# Phase 1: New endpoints
try:
    bmkg_stations = fetch_json("/api/bmkg/stations")
    check("bmkg_stations", "stations" in bmkg_stations, f"total={bmkg_stations.get('total_stations')}")
except Exception as exc:
    check("bmkg_stations", False, str(exc))

try:
    sar_summary = fetch_json("/api/sar/summary")
    check("sar_summary", "data" in sar_summary, f"source={sar_summary.get('source')}")
except Exception as exc:
    check("sar_summary", False, str(exc))

try:
    fusion_summary = fetch_json("/api/fusion/summary")
    check("fusion_summary", "source" in fusion_summary, f"source={fusion_summary.get('source')}")
except Exception as exc:
    check("fusion_summary", False, str(exc))
PY
