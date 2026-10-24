#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d10c.sh — Étape D10c : dernier code de sortie + uptime dans status().
#   Statique + unitaire, sans privilège ni conteneur (aucun appel Docker).
# bash deploy/docker/verify-d10c.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

echo "== D10c : dernier code de sortie + uptime dans status() (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

for pat in \
  'self.last_exit_code' \
  'self.started_at' \
  '"uptime"' \
  '"last_exit_code"' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null \
    && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

# _spawn_wait doit renvoyer le code de sortie réel du process.
grep -qF 'return await asyncio.wait_for(proc.wait(), timeout=timeout)' "$RUNTIME" 2>/dev/null \
  && ok "_spawn_wait : code de sortie réel propagé" \
  || ko "_spawn_wait : propagation du code de sortie absente"

echo
echo "== D10c : comportement (unitaire, sans conteneur) =="
export D10C_BACKEND="$ROOTDIR/backend"
OUT=$(PREVIEW_DRIVER=docker python3 - <<'PY' 2>&1
import asyncio, os, sys, time
sys.path.insert(0, os.environ["D10C_BACKEND"])
from preview_runtime import PreviewManager, PreviewProcess, PHASE_RUNNING, PHASE_ERROR

m = PreviewManager("/tmp/.verify-d10c")
proj = "demo"

# 1. Process vivant -> uptime croissant, last_exit_code conservé.
st = PreviewProcess(proj, "app", 8300, m.log_path(proj, "app"))
st.running = True
st.phase = PHASE_RUNNING
st.started_at = time.time() - 45
st.last_exit_code = 7
m._procs.setdefault(proj, {})["app"] = st
r = m.status(proj, 8300)
print("UPTIME_GTE", r["uptime"])
assert r["uptime"] >= 44, "uptime absent/incorrect pour process vivant"
assert r["last_exit_code"] == 7, "last_exit_code non exposé"
assert r["phase"] == PHASE_RUNNING, "phase inattendue"
print("ALIVE_EXIT_CODE", r["last_exit_code"])

# 2. _record_failure avec code -> last_exit_code mis à jour, uptime conservé.
st2 = PreviewProcess(proj + "2", "app", 8301, m.log_path(proj + "2", "app"))
st2.running = False
st2.phase = PHASE_ERROR
st2.started_at = time.time() - 10
m._procs.setdefault(proj + "2", {})["app"] = st2
m._record_failure(st2, "echec lancement", 2)
r2 = m.status(proj + "2", 8301)
print("FAIL_EXIT_CODE", r2["last_exit_code"])
assert r2["last_exit_code"] == 2, "code de sortie non propagé via _record_failure"
assert r2["phase"] == PHASE_ERROR, "phase d'erreur non reportée"

# 3. Process mort (non démarré) -> uptime nul, dernier code conservé.
st3 = PreviewProcess(proj + "3", "app", 8302, m.log_path(proj + "3", "app"))
st3.running = False
st3.phase = PHASE_RUNNING  # incohérence volontaire : corrigée par status()
st3.started_at = time.time() - 999
st3.last_exit_code = 3
m._procs.setdefault(proj + "3", {})["app"] = st3
r3 = m.status(proj + "3", 8302)
print("STOPPED_UPTIME", r3["uptime"])
print("STOPPED_EXIT_CODE", r3["last_exit_code"])
assert r3["uptime"] == 0, "uptime non nul pour process arrêté"
assert r3["last_exit_code"] == 3, "dernier code perdu après arrêt"
assert r3["phase"] != PHASE_RUNNING, "process mort toujours reporté running"

print("D10C_OK")

asyncio.run(main())
PY
) || true

printf '%s\n' "$OUT" | grep -q '^ALIVE_EXIT_CODE 7$' \
  && ok "dernier code de sortie exposé pour process vivant" || ko "code de sortie vivant absent"
printf '%s\n' "$OUT" | grep -q '^FAIL_EXIT_CODE 2$' \
  && ok "code de sortie propagé par _record_failure" || ko "code de sortie non propagé"
printf '%s\n' "$OUT" | grep -q '^STOPPED_UPTIME 0$' \
  && ok "uptime nul pour process arrêté" || ko "uptime non nul pour process arrêté"
printf '%s\n' "$OUT" | grep -q '^STOPPED_EXIT_CODE 3$' \
  && ok "dernier code conservé après arrêt" || ko "dernier code perdu après arrêt"
printf '%s\n' "$OUT" | grep -q '^D10C_OK$' \
  && ok "assertions unitaires intégralement passées" || ko "assertions unitaires en échec"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D10c conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D10c conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D10c NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
