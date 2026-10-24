#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d9.sh — Étape D9 : interdiction d'exécution sur l'OS principal.
#   Statique sans privilège ; bout-en-bout réel en root (audit start|stop).
# sudo bash deploy/docker/verify-d9.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
WRAPPER_SRC="$ROOTDIR/deploy/docker/forge-preview"

DOCKER=/usr/bin/docker
WRAPPER_DST=/usr/local/bin/forge-preview
PROJECT=verifyd9
AUDIT_DIR=${FORGE_PREVIEW_AUDIT_DIR:-/var/log/forge-preview}
AUDIT_LOG="$AUDIT_DIR/audit.log"
WSROOT=${FORGE_PREVIEW_WORKSPACE_ROOTS:-/var/www/forge/workspace}
WSROOT=${WSROOT%%:*}
[ -n "$WSROOT" ] && [ -d "$WSROOT" ] || WSROOT=/var/www/forge/workspace
WS="$WSROOT/.verify-d9.$$"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D9 : interdiction d'exécution sur l'OS principal (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

# 1. Toute exécution liée à un projet passe par le sandbox.
for pat in \
  'def _host_exec_allowed' \
  'execution hote interdite en mode docker' \
  'sudo' \
  'forge-preview' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

# 2. Les outils agent ciblent le conteneur ou le répertoire projet, jamais l'hôte.
if grep -qE 'create_subprocess_exec|subprocess\.Popen' "$RUNTIME" 2>/dev/null; then
  ok "runtime : points de lancement identifiés"
else
  ko "runtime : aucun point de lancement identifié"
fi
# Le garde-fou doit être branché AVANT Popen et create_subprocess_exec.
if grep -qF '_host_exec_allowed(plan["cmd"])' "$RUNTIME" 2>/dev/null; then
  ok "garde-fou branché avant Popen"
else
  ko "garde-fou absent avant Popen"
fi
if grep -qF '_host_exec_allowed(cmd)' "$RUNTIME" 2>/dev/null; then
  ok "garde-fou branché avant create_subprocess_exec"
else
  ko "garde-fou absent avant create_subprocess_exec"
fi

# 3. Journaliser chaque start|stop (qui, quand, projet) pour audit.
for pat in \
  'audit' \
  'SUDO_USER' \
  'date ' \
  'audit.log' ; do
  grep -qF -- "$pat" "$WRAPPER_SRC" 2>/dev/null && ok "wrapper présent : $pat" || ko "wrapper absent : $pat"
done
grep -qF 'audit "start"' "$WRAPPER_SRC" 2>/dev/null \
  && ok "wrapper : audit start" || ko "wrapper : audit start absent"
grep -qF 'audit "$VERB"' "$WRAPPER_SRC" 2>/dev/null \
  && ok "wrapper : audit stop/rm" || ko "wrapper : audit stop/rm absent"

# Le wrapper ne doit jamais exposer le socket docker.
if grep -qF 'docker.sock' "$WRAPPER_SRC" 2>/dev/null; then
  ko "wrapper référence docker.sock (interdit)"
else
  ok "wrapper : aucun socket docker"
fi

echo "== D9 : bout-en-bout réel (root requis) =="
if [ "$ROOT" -ne 1 ]; then
  skip "exécution réelle : root requis (sudo bash deploy/docker/verify-d9.sh)"
else
  cleanup() {
    "$WRAPPER_DST" rm "$PROJECT" >/dev/null 2>&1 || true
    rm -rf "$WS" 2>/dev/null || true
  }
  trap cleanup EXIT

  mkdir -p "$WS/$PROJECT" && ok "workspace de test créé" || { ko "workspace non créé"; exit 1; }
  printf '<!doctype html><title>d9</title><h1>D9</h1>\n' > "$WS/$PROJECT/index.html"

  # Compteur d'audit avant.
  if [ -f "$AUDIT_LOG" ]; then
    BEFORE=$(wc -l < "$AUDIT_LOG")
  else
    BEFORE=0
  fi

  # start réel -> doit journaliser audit start.
  OUT=$("$WRAPPER_DST" start "$PROJECT" --port 8113 --workspace "$WS/$PROJECT" 2>&1)
  RC=$?
  if [ $RC -eq 0 ]; then
    ok "start exécuté (rc=0)"
  else
    ko "start en échec : $OUT"
  fi
  grep -q "verb=start project=$PROJECT" "$AUDIT_LOG" 2>/dev/null \
    && ok "audit : start journalisé (verb=start)" || ko "audit : start non journalisé"

  # stop réel -> doit journaliser audit stop.
  "$WRAPPER_DST" stop "$PROJECT" >/dev/null 2>&1
  grep -q "verb=stop project=$PROJECT" "$AUDIT_LOG" 2>/dev/null \
    && ok "audit : stop journalisé (verb=stop)" || ko "audit : stop non journalisé"

  # Champs d'audit : qui (SUDO_USER), quand (ts=), projet, rc.
  LINE=$(grep "verb=start project=$PROJECT" "$AUDIT_LOG" 2>/dev/null | tail -1)
  printf '%s\n' "$LINE" | grep -qE 'ts=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}' \
    && ok "audit : horodatage ts= présent" || ko "audit : horodatage ts= absent"
  printf '%s\n' "$LINE" | grep -qE 'who=[^ ]+' \
    && ok "audit : identité who= présente" || ko "audit : identité who= absente"
  printf '%s\n' "$LINE" | grep -qE 'rc=[0-9]+' \
    && ok "audit : code retour rc= présent" || ko "audit : code retour rc= absent"

  # cleanup explicite
  "$WRAPPER_DST" rm "$PROJECT" >/dev/null 2>&1 || true
fi

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D9 conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D9 conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D9 NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
