#!/usr/bin/env bash
set -euo pipefail

KEY_PATH="${KEY_PATH:-$HOME/awanhehe.pem}"
HOST="${HOST:-ubuntu@13.236.148.26}"
LOCAL_DIR="${LOCAL_DIR:-/mnt/c/Users/Administrator/Downloads/ntb-groundwater-fase1}"
REMOTE_DIR="${REMOTE_DIR:-/home/ubuntu/ntb-groundwater-monitor/ntb-groundwater-monitor}"
APPLY_ESDM_SQL="${APPLY_ESDM_SQL:-1}"

copy_file() {
  local rel_path="$1"
  echo "==> Upload $rel_path"
  scp -i "$KEY_PATH" "$LOCAL_DIR/$rel_path" "$HOST:$REMOTE_DIR/$rel_path"
}

echo "==> Validate local paths"
test -f "$KEY_PATH"
test -d "$LOCAL_DIR"

echo "==> Ensure remote directories"
ssh -i "$KEY_PATH" "$HOST" "
  mkdir -p '$REMOTE_DIR/backend' '$REMOTE_DIR/frontend' '$REMOTE_DIR/scripts'
"

copy_file "backend/main.py"
copy_file "frontend/index.html"
copy_file "docker-compose.yml"
copy_file "scripts/wells_esdm.sql"
copy_file ".env.example"

echo "==> Verify remote markers"
ssh -i "$KEY_PATH" "$HOST" "
  set -e
  cd '$REMOTE_DIR'
  echo '--- frontend markers ---'
  grep -nE 'Anomali TWS Regional|Menganalisis TWS GRACE|loadDemo' frontend/index.html || true
  echo '--- backend markers ---'
  grep -nE 'latest_snapshot|summary_basis|usage_note|get_latest_ndvi_rows' backend/main.py || true
"

echo "==> Deploy containers"
ssh -i "$KEY_PATH" "$HOST" "
  set -e
  cd '$REMOTE_DIR'
  if [ '$APPLY_ESDM_SQL' = '1' ] && [ -f scripts/wells_esdm.sql ]; then
    echo '--- apply wells_esdm.sql ---'
    docker compose exec -T db psql -U rizki -d ntb_groundwater < scripts/wells_esdm.sql
  fi
  echo '--- rebuild api + restart frontend ---'
  docker compose up -d --build api frontend
  echo '--- docker compose ps ---'
  docker compose ps
"

echo "==> Run smoke test"
"$LOCAL_DIR/scripts/smoke_test_live.sh"
