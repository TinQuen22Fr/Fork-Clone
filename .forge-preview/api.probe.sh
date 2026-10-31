#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
set +e
for i in $(seq 1 90); do
  if python3 -c "import socket,sys;s=socket.socket();s.settimeout(1);s.connect(('127.0.0.1',8192));s.close()" 2>/dev/null; then
    exit 0
  fi
  sleep 1
done
exit 1
