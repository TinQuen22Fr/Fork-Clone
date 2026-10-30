#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
export HOST=0.0.0.0
export PORT=8192
cd backend
exec /forge-state/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8192
