#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
set +e
for i in 1 2 3 4 5 6 7 8 9 10; do
  if python3 -c "import socket,sys;s=socket.socket();s.settimeout(1);s.connect(('127.0.0.1',8192));s.close()" 2>/dev/null; then
    exit 0
  fi
  sleep 1
done
exit 1
