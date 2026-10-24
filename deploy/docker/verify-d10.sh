#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d10.sh — Étape D10 : observabilité et garde-fous anti-boucle.
#   Statique sans privilège ; bout-en-bout réel en root (stats, audit, backoff).
# sudo bash deploy/docker/verify-d10.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
SERVER="$ROOTDIR/backend/server.py"
WRAPPER_SRC="$ROOTDIR/deploy/docker/forge-preview"
WRAPPER_DST=/usr/local/bin/forge-preview

DOCKER=/usr/bin/docker
PROJECT=verifyd10
WSROOT=${FORGE_PREVIEW_WORKSPACE_ROOTS:-/var/www/forge/workspace}
WSROOT=${WSROOT%%:*}
[ -n "$WSROOT" ] && [ -d "$WSROOT" ] || WSROOT=/var/www/forge/workspace
WS="$WSROOT/.verify-d10.$$"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D10 : observabilité et garde-fous anti-boucle (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

# 1. Compteur anti-boucle.
for pat in \
  'RESTART_WINDOW' \
  'RESTART_MAX_FAILURES' \
  'def _mark_failure' \
  'def _record_failure' \
  'def _record_success' \
  'def is_disabled' \
  'def disable' \
  'def enable' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

# 2. Backoff obligatoire.
for pat in 'RESTART_BACKOFF' 'def backoff_remaining' 'def mark_started'; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

# 3. Métriques : verbe stats dans le wrapper + stats/active_previews runtime + /chat/status.
grep -qF 'stats)' "$WRAPPER_SRC" 2>/dev/null \
  && ok "wrapper présent : verbe stats" || ko "wrapper : verbe stats absent"
grep -qF -- '--no-stream' "$WRAPPER_SRC" 2>/dev/null \
  && ok "wrapper : stats --no-stream (pas de boucle)" || ko "wrapper : stats --no-stream absent"
grep -qF 'def stats' "$RUNTIME" 2>/dev/null \
  && ok "runtime présent : def stats" || ko "runtime : def stats absent"
grep -qF 'def active_previews' "$RUNTIME" 2>/dev/null \
  && ok "runtime présent : def active_previews" || ko "runtime : def active_previews absent"
grep -qF 'preview_mgr().active_previews()' "$SERVER" 2>/dev/null \
  && ok "server : métriques exposées dans /chat/status" || ko "server : métriques /chat/status absentes"

# 4. Garde-fous de sécurité invariants.
grep -qF 'docker.sock' "$WRAPPER_SRC" 2>/dev/null \
  && ko "wrapper référence docker.sock (interdit)" || ok "wrapper : aucun socket docker"
grep -qF 'def redact_secrets' "$RUNTIME" 2>/dev/null \
  && ok "runtime : masquage secrets conservé" || ko "runtime : masquage secrets absent"

echo
if [ "$ROOT" -ne 1 ]; then
  skip "exécution réelle : root requis (sudo bash deploy/docker/verify-d10.sh)"
else
  echo "== D10 : bout-en-bout réel (root requis) =="
  cleanup() {
    "$WRAPPER_DST" rm "$PROJECT" >/dev/null 2>&1 || true
    rm -rf "$WS" 2>/dev/null || true
  }
  trap cleanup EXIT

  mkdir -p "$WS/$PROJECT" && ok "workspace de test créé" || { ko "workspace non créé"; exit 1; }
  printf '<!doctype html><title>d10</title><h1>D10</h1>\n' > "$WS/$PROJECT/index.html"

  OUT=$("$WRAPPER_DST" start "$PROJECT" --port 8114 --workspace "$WS/$PROJECT" 2>&1)
  RC=$?
  if [ $RC -eq 0 ]; then
    ok "start exécuté (rc=0)"
  else
    ko "start en échec : $OUT"
  fi

  # stats : le conteneur tourne -> une ligne CPUPerc|MemUsage|PIDs.
  ST=$("$WRAPPER_DST" stats "$PROJECT" 2>&1)
  RC=$?
  if [ $RC -eq 0 ] && printf '%s\n' "$ST" | grep -qE '^[0-9.]+%\|[0-9.]+[KMGT]?iB / [0-9.]+[KMGT]?iB\|[0-9]+$'; then
    ok "stats : CPU/RAM/PIDs lus (rc=0)"
  else
    ko "stats en échec (rc=$RC) : $ST"
  fi

  # backoff : un second start immédiat doit être refusé (conteneur déjà en cours).
  OUT2=$("$WRAPPER_DST" start "$PROJECT" --port 8114 --workspace "$WS/$PROJECT" 2>&1)
  RC2=$?
  if [ $RC2 -ne 0 ]; then
    ok "second start rapproché refusé (rc=$RC2)"
  else
    ko "second start rapproché accepté (anti-boucle inopérant)"
  fi

  # stop -> le conteneur disparaît, le stats échoue proprement.
  "$WRAPPER_DST" stop "$PROJECT" >/dev/null 2>&1
  if ! "$WRAPPER_DST" stats "$PROJECT" >/dev/null 2>&1; then
    ok "stats après stop : conteneur absent (échec attendu)"
  else
    ko "stats après stop : conteneur encore présent"
  fi
fi

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D10 conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D10 conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D10 NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
