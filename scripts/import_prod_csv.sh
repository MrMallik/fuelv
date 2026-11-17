#!/usr/bin/env bash
set -Eeuo pipefail

CSV_FILE="${1:-data/my_bike_import.csv}"
TARGET_HOST="${2:-${DEPLOY_HOST:-cloud-endurance}}"

if [[ ! -f "$CSV_FILE" ]]; then
  echo "Error: CSV file not found at: $CSV_FILE" >&2
  echo "Usage: ./scripts/import_prod_csv.sh [path/to/file.csv] [ssh_host_or_user@ip]" >&2
  exit 1
fi

echo "Connecting to $TARGET_HOST and streaming $CSV_FILE into fuelv container..."

ssh -o BatchMode=yes "$TARGET_HOST" "cat > /tmp/fuelv_remote_import.csv" < "$CSV_FILE"
ssh -o BatchMode=yes "$TARGET_HOST" "
  set -Eeuo pipefail
  docker cp /tmp/fuelv_remote_import.csv fuelv:/tmp/import.csv
  rm -f /tmp/fuelv_remote_import.csv
  docker exec fuelv python3 -m backend.scripts.import_csv /tmp/import.csv
  docker exec fuelv rm -f /tmp/import.csv
"

echo "Import complete. Verify live at: https://pritabrata.com/fuelv/"
