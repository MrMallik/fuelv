#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

cleanup() {
  echo ""
  echo "Stopping FuelV servers..."
  kill $(jobs -p) 2>/dev/null || true
}
trap cleanup EXIT INT TERM

./start-api.sh &

cd frontend
if [ ! -d "node_modules" ]; then
  echo "Installing frontend dependencies..."
  npm install
fi

echo "Starting FuelV Vue frontend on http://localhost:5173..."
npm run dev
