#!/usr/bin/env bash
set -euo pipefail

# Always run from repository root
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment in .venv..."
  python3 -m venv .venv
fi

source .venv/bin/activate

if ! python3 -c "import uvicorn, fastapi, multipart" >/dev/null 2>&1; then
  echo "Installing backend dependencies..."
  pip install -r backend/requirements.txt
fi

PORT="${PORT:-8000}"
echo "Running pre-flight configuration validation..."
python3 -m backend.config

echo "Starting FuelV API server on http://127.0.0.1:${PORT} (Docs: http://127.0.0.1:${PORT}/docs)..."
exec uvicorn backend.main:app --reload --port "${PORT}"
