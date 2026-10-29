#!/usr/bin/env bash
set -euo pipefail
set +e
echo "== forge preview install =="
cd backend
echo "-- python du conteneur --"
python3 --version || true
echo "-- creation / reutilisation du venv --"
if [ ! -x /forge-state/venv/bin/python ]; then
  python3 -m venv /forge-state/venv || { echo "ERREUR: creation du venv impossible"; exit 1; }
fi
/forge-state/venv/bin/python --version
echo "-- mise a niveau de pip --"
/forge-state/venv/bin/python -m ensurepip --upgrade 2>/dev/null || true
/forge-state/venv/bin/python -m pip install --upgrade pip setuptools wheel || { echo "ERREUR: mise a niveau de pip impossible"; exit 1; }
echo "-- installation des dependances (requirements.txt) --"
/forge-state/venv/bin/python -m pip install -r requirements.txt
rc=$?
if [ "$rc" -ne 0 ]; then echo "ERREUR: pip install -r requirements.txt a echoue (code $rc)"; exit "$rc"; fi
echo "== installation terminee avec succes =="
