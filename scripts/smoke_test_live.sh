#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://13.236.148.26:3000}"

echo "==> Smoke test: $BASE_URL"

python3 - "$BASE_URL" <<'PY'
import json
import sys
import urllib.request

base = sys.argv[1].rstrip("/")


def fetch_text(path: str) -> str:
    with urllib.request.urlopen(base + path, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_json(path: str):
    with urllib.request.urlopen(base + path, timeout=30) as resp:
        return json.load(resp)


def check(name: str, passed: bool, detail: str) -> None:
    prefix = "PASS" if passed else "FAIL"
    print(f"{prefix:<4} {name}: {detail}")


home_html = fetch_text("/")
check("home_has_new_grace_label", "Anomali TWS Regional" in home_html, "expect homepage contains new GRACE wording")
check("home_has_no_demo_fallback", "loadDemo" not in home_html, "expect homepage no longer ships demo fallback")

health = fetch_json("/api/health")
check("health_ok", health.get("status") == "ok", json.dumps(health, ensure_ascii=False))

ndvi = fetch_json("/api/ndvi/summary")
ndvi_meta = ndvi.get("metadata", {})
check("ndvi_latest_snapshot", bool(ndvi_meta.get("latest_snapshot")), json.dumps(ndvi_meta, ensure_ascii=False))
check("ndvi_summary_basis", bool(ndvi_meta.get("summary_basis")), json.dumps(ndvi_meta, ensure_ascii=False))

grace = fetch_json("/api/grace/timeseries?start_year=2020&end_year=2025")
grace_meta = grace.get("metadata", {})
check("grace_usage_note", bool(grace_meta.get("usage_note")), json.dumps(grace_meta, ensure_ascii=False))
check("grace_points", len(grace.get("series", [])) >= 12, f"series_count={len(grace.get('series', []))}")

esdm = fetch_json("/api/wells/esdm/geojson")
esdm_total = esdm.get("metadata", {}).get("total")
check("esdm_total_280", esdm_total == 280, f"total={esdm_total}")

wells = fetch_json("/api/wells/geojson")
check("monitoring_wells_available", wells.get("metadata", {}).get("total_wells", 0) >= 1, json.dumps(wells.get("metadata", {}), ensure_ascii=False))

try:
    ai = fetch_json("/api/ai/interpret")
    check("ai_interpretation", bool(ai.get("interpretation")), f"model={ai.get('ai_model')}")
except Exception as exc:
    check("ai_interpretation", False, str(exc))

try:
    req = urllib.request.Request(base + "/api/report/pdf")
    with urllib.request.urlopen(req, timeout=60) as resp:
        content_type = resp.headers.get("Content-Type", "")
        check("pdf_export", resp.status == 200 and "application/pdf" in content_type, f"status={resp.status} content_type={content_type}")
except Exception as exc:
    check("pdf_export", False, str(exc))
PY
