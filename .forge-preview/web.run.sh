#!/usr/bin/env bash
set -euo pipefail
export HOST=0.0.0.0
export PORT=8092
cd frontend
exec npm run dev -- --host 0.0.0.0 --port 8092 --strictPort
