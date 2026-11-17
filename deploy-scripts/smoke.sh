#!/usr/bin/env bash
set -Eeuo pipefail

# Verify fuelv container health via Docker inspect & internal health endpoint
status="$(docker inspect -f '{{.State.Health.Status}}' fuelv)"
[[ "$status" == "healthy" ]] || { printf 'Container fuelv is not healthy (status: %s)\n' "$status" >&2; exit 1; }

health_json="$(docker exec fuelv python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).read().decode())")"
python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ok"' <<< "$health_json"

printf 'FuelV container health and API verified.\n'
