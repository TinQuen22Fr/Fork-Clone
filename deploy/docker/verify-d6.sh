#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d6.sh — Étape D6 : veille et conservation du workspace.
# Statique sans privilège ; bout-en-bout réel en root (stop sans rm).
# sudo bash deploy/docker/verify-d6.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
SERVER="$ROOTDIR/backend/server.py"
WRAPPER_SRC="$ROOTDIR/deploy/docker/forge-preview"

DOCKER=/usr/bin/docker
WRAPPER_DST=/usr/local/bin/forge-preview
NETWORK=${FORGE_PREVIEW_NETWORK:-forge-preview-net}
IMAGE=${FORGE_PREVIEW_IMAGE:-forge-preview-base:local}
PROJECT=verifyd6
WSROOT=${FORGE_PREVIEW_WORKSPACE_ROOTS:-/var/www/forge/workspace}
WSROOT=${WSROOT%%:*}
[ -n "$WSROOT" ] && [ -d "$WSROOT" ] || WSROOT=/var/www/forge/workspace
WS="$WSROOT/.verify-d6.$$"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D6 : veille et conservation du workspace (statique) =="
[ -f "$SERVER" ] && ok "server.py présent" || ko "server.py absent"
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

# 1. Le reaper appelle stop(), jamais rm.
grep -qF 'await mgr.stop(name, port)' "$SERVER" \
  && ok "reaper appelle mgr.stop()" || ko "reaper n'appelle pas stop()"
if grep -qF 'docker rm' "$RUNTIME"; then
  grep -qF '"rm", project' "$RUNTIME" 2>/dev/null \
    && ko "rm atteint par le cycle de vie (hors restart)" \
    || ok "rm uniquement sous restart explicite"
else
  ok "aucun docker rm dans preview_runtime.py"
fi
grep -qF '"stop", project, timeout=60' "$RUNTIME" \
  && ok "stop() docker => wrapper stop (pas rm)" || ko "stop() ne passe pas par wrapper stop"

# 2. Workspace en bind mount => conserve apres arret.
grep -qF -- '-v "${WORKSPACE}:/workspace:rw"' "$WRAPPER_SRC" \
  && ok "workspace en bind mount rw" || ko "workspace non monté en bind"

# 3. Aucune relance automatique.
grep -qF -- '--restart=no' "$WRAPPER_SRC" \
  && ok "docker --restart=no (pas de relance)" || ko "politique de relance non neutre"
grep -qF 'sleep infinity' "$WRAPPER_SRC" \
  && ok "garde en vie sleep infinity (aucune execution projet)" || ko "code projet lance au demarrage"
grep -qF '"PREVIEW_AUTOSTART", "0"' "$SERVER" \
  && ok "PREVIEW_AUTOSTART defaut OFF" || ko "PREVIEW_AUTOSTART defaut ON"

echo "== D6 : bout-en-bout réel (root requis) =="
if [ "$ROOT" -ne 1 ]; then
  skip "exécution réelle : root requis (sudo bash deploy/docker/verify-d6.sh)"
else
  command -v sudo >/dev/null 2>&1 && SUDO=sudo || SUDO=""
  SUDO_USER_UID=${SUDO_UID:-0}
  # Wrapper utilisable sans mot de passe par l'utilisateur du service.
  if [ "$SUDO_USER_UID" -ne 0 ] && sudo -n -u "#$SUDO_USER_UID" "$WRAPPER_DST" health >/dev/null 2>&1; then
    ok "utilisateur service peut appeler le wrapper sans mot de passe"
  elif [ "$SUDO_USER_UID" -eq 0 ]; then
    ok "root peut appeler le wrapper"
  else
    ko "wrapper non appelable sans mot de passe par l'utilisateur du service"
  fi

  cleanup() {
    "$WRAPPER_DST" stop "$PROJECT" >/dev/null 2>&1 || true
    "$WRAPPER_DST" rm "$PROJECT" >/dev/null 2>&1 || true
    rm -rf "$WS" 2>/dev/null || true
  }
  trap cleanup EXIT

  mkdir -p "$WS" && ok "workspace de test créé" || { ko "workspace non créé"; exit 1; }
  printf '<!doctype html><title>d6</title><h1>D6</h1>\n' > "$WS/index.html"
  ( cd "$WS" && git init -q && git add -A && git -c user.email=d6@local -c user.name=d6 commit -qm init ) \
    && ok "workspace initialisé git (push possible)" || ko "workspace git non initialisable"

  "$WRAPPER_DST" start "$PROJECT" --workspace "$WS" --port 8096 >/dev/null 2>&1 \
    && ok "conteneur démarré" || { ko "échec forge-preview start"; exit 1; }

  # Simulation exacte du reaper : mgr.stop() => wrapper stop (jamais rm).
  "$WRAPPER_DST" stop "$PROJECT" >/dev/null 2>&1 \
    && ok "stop() via wrapper (équivalent reaper)" || ko "échec wrapper stop"

  STATE=$("$DOCKER" inspect -f '{{.State.Status}}' "forge-preview-$PROJECT" 2>/dev/null || true)
  [ "$STATE" = "exited" ] && ok "conteneur arrêté (exited)" || ko "conteneur non arrêté : '$STATE'"
  "$DOCKER" inspect "forge-preview-$PROJECT" >/dev/null 2>&1 \
    && ok "conteneur conservé (non supprimé par stop)" || ko "conteneur supprimé (rm) par stop"

  [ -f "$WS/index.html" ] && ok "fichiers workspace intacts" || ko "workspace perdu"
  ( cd "$WS" && git status --short >/dev/null 2>&1 ) \
    && ok "dépôt git du workspace utilisable après arrêt" || ko "dépôt git cassé"
fi

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D6 conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D6 conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D6 NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
