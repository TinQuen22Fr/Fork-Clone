#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d10e.sh — Étape D10e : agrégation des métriques preview dans /chat/status.
#   Statique + unitaire, sans privilège ni conteneur (aucun appel Docker).
# bash deploy/docker/verify-d10e.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
SERVER="$ROOTDIR/backend/server.py"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

echo "== D10e : agrégation métriques preview dans /chat/status (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"
[ -f "$SERVER" ] && ok "server.py présent" || ko "server.py absent"

grep -qF '@api_router.get("/chat/status")' "$SERVER" 2>/dev/null \
  && ok "endpoint présent : /chat/status" || ko "endpoint /chat/status absent"
grep -qE '^(async )?def chat_status' "$SERVER" 2>/dev/null \
  && ok "handler présent : chat_status" || ko "handler chat_status absent"
grep -qF 'preview_mgr().active_previews()' "$SERVER" 2>/dev/null \
  && ok "server : agrégation via active_previews()" || ko "server : active_previews() absent"
grep -qF '"previews": previews' "$SERVER" 2>/dev/null \
  && ok "server : clé 'previews' dans le retour" || ko "server : clé 'previews' absente"
grep -qF '"turns": turns' "$SERVER" 2>/dev/null \
  && ok "server : clé 'turns' conservée" || ko "server : clé 'turns' absente"
grep -qF '"running": running' "$SERVER" 2>/dev/null \
  && ok "server : clé 'running' conservée" || ko "server : clé 'running' absente"

# Le retour de /chat/status ne doit jamais lever si l'agrégation échoue.
awk '/previews = \{\}/{f=1} f&&/preview_mgr\(\)\.active_previews\(\)/{g=1} f&&/except Exception/{h=1} END{exit !(f&&g&&h)}' "$SERVER" 2>/dev/null \
  && ok "server : agrégation sous try/except (ne lève jamais)" \
  || ko "server : try/except autour de active_previews() absent"

for pat in \
  'def active_previews' \
  '"running"' \
  '"disabled"' \
  '"uptime"' \
  '"last_exit_code"' \
  '"phase"' \
  '"stats"' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null \
    && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

echo
echo "== D10e : agrégation active_previews (unitaire, sans conteneur) =="
OUT=$(python3 - "$RUNTIME" <<'PY' 2>&1
import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("pr", sys.argv[1])
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

m = pr.PreviewManager("/tmp/.verify-d10e-unit")
proj = "demo"

# 1. Process web vivant -> tous les champs agrégés, stats renseignées.
st = pr.PreviewProcess(proj, "web", 8400, m.log_path(proj, "web"))
st.running = True
st.phase = pr.PHASE_RUNNING
st.started_at = time.time() - 30
st.last_exit_code = 0
m._procs.setdefault(proj, {})["web"] = st
m.stats = lambda p: {"cpu_percent": "1.0%", "mem_usage": "10MiB", "mem_percent": "1GiB", "pids": 3}

r = m.active_previews()
assert proj in r, r
p = r[proj]
assert p["running"] is True, p
assert p["disabled"] is False, p
assert 25 <= p["uptime"] <= 35, p
assert p["last_exit_code"] == 0, p
assert p["phase"] == pr.PHASE_RUNNING, p
assert p["stats"]["cpu_percent"] == "1.0%", p
assert p["stats"]["mem_usage"] == "10MiB", p
assert p["stats"]["pids"] == 3, p

# 2. Preview désactivée -> disabled=True, toujours dans l'agrégat.
m._disabled.add(proj)
p = m.active_previews()[proj]
assert p["disabled"] is True, p

# 3. Process arrêté -> running=False, uptime=0, stats présentes mais vides.
m._disabled.discard(proj)
st.running = False
st.phase = pr.PHASE_STOPPED
st.started_at = 0.0
m.stats = lambda p: {"cpu_percent": None, "mem_usage": None, "mem_percent": None, "pids": None}
p = m.active_previews()[proj]
assert p["running"] is False, p
assert p["uptime"] == 0, p
assert p["phase"] == pr.PHASE_STOPPED, p
assert p["stats"]["cpu_percent"] is None, p

# 4. Projet sans cible -> absent de l'agrégat (pas de clé vide).
m._procs.setdefault("vide", {})
assert "vide" not in m.active_previews(), m.active_previews()

print("D10E_OK")
PY
)
RC=$?
if [ $RC -eq 0 ] && printf '%s\n' "$OUT" | grep -qF 'D10E_OK'; then
  ok "agrégation running/disabled/uptime/last_exit_code/phase/stats"
  ok "assertions unitaires intégralement passées"
else
  ko "échec unitaire (rc=$RC) : $OUT"
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "RÉSULTAT : D10e NON conforme — corriger les lignes KO ci-dessus."
  exit 1
elif [ "$unverified" -ne 0 ]; then
  echo "RÉSULTAT : D10e conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D10e conforme."
fi
