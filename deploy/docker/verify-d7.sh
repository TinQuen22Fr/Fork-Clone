#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d7.sh — Étape D7 : nettoyage et quotas disque.
# Statique sans privilège ; bout-en-bout réel en root (wrapper prune + quota du).
# sudo bash deploy/docker/verify-d7.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
SERVER="$ROOTDIR/backend/server.py"
WRAPPER_SRC="$ROOTDIR/deploy/docker/forge-preview"

DOCKER=/usr/bin/docker
WRAPPER_DST=/usr/local/bin/forge-preview
PROJECT=verifyd7
WSROOT=${FORGE_PREVIEW_WORKSPACE_ROOTS:-/var/www/forge/workspace}
WSROOT=${WSROOT%%:*}
[ -n "$WSROOT" ] && [ -d "$WSROOT" ] || WSROOT=/var/www/forge/workspace
WS="$WSROOT/.verify-d7.$$"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D7 : nettoyage et quotas disque (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"
[ -f "$SERVER" ] && ok "server.py présent" || ko "server.py absent"

# 1. Tâche périodique : rm des conteneurs arrêtés > N jours + image prune.
for pat in \
  'self.prune_older_days' \
  'async def prune' \
  'PREVIEW_PRUNE_OLDER_DAYS' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done
grep -qF -- '--older-days' "$WRAPPER_SRC" \
  && ok "wrapper : verbe prune borné --older-days" || ko "wrapper : --older-days absent"
grep -qF -- 'image prune' "$WRAPPER_SRC" \
  && ok "wrapper : image prune présent" || ko "wrapper : image prune absent"
grep -qF -- '--filter "status=exited"' "$WRAPPER_SRC" \
  && ok "wrapper : rm limité aux conteneurs arrêtés (exited)" || ko "wrapper : filtre exited absent"
grep -qF -- '--filter "status=created"' "$WRAPPER_SRC" \
  && ok "wrapper : rm limité aux conteneurs created" || ko "wrapper : filtre created absent"
if grep -q 'docker\.sock' "$WRAPPER_SRC"; then
  ko "wrapper référence docker.sock (interdit)"
else
  ok "wrapper : aucun montage socket docker"
fi

# 2. Contrôle du du workspace avant lancement, refus au-delà du seuil.
for pat in \
  'self.max_workspace_gb' \
  'PREVIEW_MAX_WORKSPACE_GB' \
  'def _du_bytes' \
  'projet trop volumineux' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

# 3. Alerte si l'espace libre / passe sous un seuil (ex. 50 Go).
for pat in \
  'self.min_free_disk_gb' \
  'PREVIEW_MIN_FREE_DISK_GB' \
  'def disk_free_gb' \
  'def disk_alert' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done
for pat in \
  'self.preview_housekeeping_interval' \
  'PREVIEW_HOUSEKEEPING_INTERVAL' \
  'async def _preview_housekeeping' \
  '_preview_housekeeping()' ; do
  grep -qF -- "$pat" "$SERVER" 2>/dev/null && ok "server présent : $pat" || ko "server absent : $pat"
done

echo "== D7 : bout-en-bout réel (root requis) =="
if [ "$ROOT" -ne 1 ]; then
  skip "exécution réelle : root requis (sudo bash deploy/docker/verify-d7.sh)"
else
  cleanup() {
    rm -rf "$WS" 2>/dev/null || true
  }
  trap cleanup EXIT

  mkdir -p "$WS/$PROJECT" && ok "workspace de test créé" || { ko "workspace non créé"; exit 1; }
  printf '<!doctype html><title>d7</title><h1>D7</h1>\n' > "$WS/$PROJECT/index.html"

  # --- wrapper prune : borning -------------------------------
  "$WRAPPER_DST" prune --older-days abc >/dev/null 2>&1
  [ $? -eq 64 ] && ok "prune --older-days non entier refusé (64)" || ko "prune --older-days invalide non refusé"
  "$WRAPPER_DST" prune --older-days 0 >/dev/null 2>&1
  [ $? -eq 64 ] && ok "prune --older-days 0 refusé (hors bornes)" || ko "prune --older-days 0 non refusé"
  "$WRAPPER_DST" prune --older-days 3651 >/dev/null 2>&1
  [ $? -eq 64 ] && ok "prune --older-days 3651 refusé (hors bornes)" || ko "prune --older-days 3651 non refusé"
  "$WRAPPER_DST" prune --badopt >/dev/null 2>&1
  [ $? -eq 64 ] && ok "prune option inconnue refusée (64)" || ko "prune option inconnue non refusée"

  # --- wrapper prune : exécution réelle -----------------------
  OUT=$("$WRAPPER_DST" prune --older-days 30 2>&1)
  [ $? -eq 0 ] && ok "prune exécuté (rc=0)" || ko "prune en échec : $OUT"
  printf '%s\n' "$OUT" | grep -q 'prune ok' \
    && ok "prune rapporte prune ok" || ko "prune sans compte-rendu"

  # --- quota du : refus au-delà du seuil ----------------------
  export D7_BACKEND="$ROOTDIR/backend"
  OUT=$(PREVIEW_MAX_WORKSPACE_GB=0.000000001 \
    python3 - "$WS" "$PROJECT" <<'PY' 2>&1
import asyncio, os, sys
sys.path.insert(0, os.environ["D7_BACKEND"])
from preview_runtime import PreviewManager

async def main():
    m = PreviewManager(sys.argv[1])
    st = await m.start(sys.argv[2], 8107)
    print("QUOTA_PHASE", st.get("phase"))
    print("QUOTA_MSG", st.get("message", ""))

asyncio.run(main())
PY
  ) || true
  printf '%s\n' "$OUT" | grep -q '^QUOTA_PHASE error$' \
    && ok "quota : lancement refusé (phase error)" || ko "quota : lancement non refusé"
  printf '%s\n' "$OUT" | grep -q 'projet trop volumineux' \
    && ok "quota : message 'projet trop volumineux' présent" || ko "quota : message absent"

  # --- quota : projet léger non bloqué (seuil par défaut) -----
  OUT=$(python3 - "$WS" "$PROJECT" <<'PY' 2>&1
import os, sys
sys.path.insert(0, os.environ["D7_BACKEND"])
from preview_runtime import PreviewManager, _du_bytes
m = PreviewManager(sys.argv[1])
print("SIZE_GB", round(_du_bytes(m.project_dir(sys.argv[2])) / (1024**3), 9))
print("DEFAULT_GB", m.max_workspace_gb)
PY
  ) || true
  printf '%s\n' "$OUT" | grep -qE '^DEFAULT_GB 10(\.0)?$' \
    && ok "quota : seuil par défaut 10 Go" || ko "quota : seuil par défaut inattendu"
  printf '%s\n' "$OUT" | grep -qE '^SIZE_GB [0-9.e-]+$' \
    && ok "quota : mesure du() disponible" || ko "quota : mesure du() absente"

  # --- alerte disque : seuil franchi --------------------------
  OUT=$(PREVIEW_MIN_FREE_DISK_GB=999999999 \
    python3 - "$WS" <<'PY' 2>&1
import os, sys
sys.path.insert(0, os.environ["D7_BACKEND"])
from preview_runtime import PreviewManager
m = PreviewManager(sys.argv[1])
print("FREE_GB", round(m.disk_free_gb(), 2))
print("ALERT", "oui" if m.disk_alert() else "non")
PY
  ) || true
  printf '%s\n' "$OUT" | grep -q '^ALERT oui$' \
    && ok "alerte disque déclenchée sous le seuil" || ko "alerte disque non déclenchée"
  printf '%s\n' "$OUT" | grep -qE '^FREE_GB [0-9.]+$' \
    && ok "espace libre / mesuré" || ko "espace libre / non mesuré"
fi

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D7 conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D7 conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D7 NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
