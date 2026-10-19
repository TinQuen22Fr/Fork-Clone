#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d2.sh — Contrôles de sortie de l'étape D2 (lecture seule).
#
#   sudo bash deploy/docker/verify-d2.sh
#
# À lancer en root : la lecture des sudoers et les tests `sudo` imbriqués
# l'exigent. N'installe rien, ne modifie rien. Code retour 0 = D2 conforme.
#
set -uo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${FORGE_SERVICE_USER:-quentin}"
WRAPPER_SRC="$SRC_DIR/forge-preview"
WRAPPER_DST="/usr/local/bin/forge-preview"
SUDOERS_DST="/etc/sudoers.d/forge-preview-docker"

fail=0
ok()   { printf '  \033[1;32mOK\033[0m   %s\n' "$*"; }
ko()   { printf '  \033[1;31mKO\033[0m   %s\n' "$*"; fail=1; }
info() { printf '       %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "verify-d2.sh doit être lancé en root : sudo bash deploy/docker/verify-d2.sh" >&2
  exit 2
fi

# --- as_quentin <cmd...> : exécute une commande en tant que service user -----
as_user() {
  runuser -u "$SERVICE_USER" -- "$@" 2>&1
}
# --- sudo_n_as_user <cmd...> : sudo -n depuis le service user ----------------
sudo_n_as_user() {
  runuser -u "$SERVICE_USER" -- sudo -n "$@" 2>&1
}

echo "== D2 : wrapper =="
if [ -x "$WRAPPER_DST" ]; then
  ok "wrapper présent : $WRAPPER_DST"
  OWNER="$(stat -c '%U:%G' "$WRAPPER_DST")"
  [ "$OWNER" = "root:root" ] && ok "propriétaire root:root" || ko "propriétaire $OWNER (attendu root:root)"
  MODE="$(stat -c '%a' "$WRAPPER_DST")"
  case "$MODE" in 755|555) ok "mode $MODE (non modifiable par le backend)" ;;
                 *) ko "mode $MODE (attendu 755, sans droit d'écriture pour le groupe/autres)" ;; esac
  if [ -f "$WRAPPER_SRC" ]; then
    A="$(sha256sum "$WRAPPER_SRC" | cut -d' ' -f1)"
    B="$(sha256sum "$WRAPPER_DST" | cut -d' ' -f1)"
    [ "$A" = "$B" ] && ok "wrapper identique au dépôt (sha256)" \
                    || ko "wrapper différent du dépôt (réinstaller : install-d2.sh)"
  else
    info "source dépôt absente — comparaison impossible"
  fi
  grep -q 'docker\.sock' "$WRAPPER_DST" \
    && ko "le wrapper mentionne docker.sock (interdit)" \
    || ok "le wrapper ne mentionne pas docker.sock"
else
  ko "wrapper absent : $WRAPPER_DST"
fi

echo "== D2 : règle sudoers =="
if [ -f "$SUDOERS_DST" ]; then
  ok "fichier présent : $SUDOERS_DST"
  OWNER="$(stat -c '%U:%G' "$SUDOERS_DST")"
  [ "$OWNER" = "root:root" ] && ok "propriétaire root:root" || ko "propriétaire $OWNER"
  MODE="$(stat -c '%a' "$SUDOERS_DST")"
  [ "$MODE" = "440" ] && ok "mode 0440" || ko "mode $MODE (attendu 440)"
  visudo -cqf "$SUDOERS_DST" >/dev/null 2>&1 && ok "syntaxe validée par visudo" || ko "syntaxe sudoers invalide"
  NB="$(grep -vcE '^\s*(#|$)' "$SUDOERS_DST")"
  [ "$NB" -eq 1 ] && ok "exactement 1 règle active" || ko "$NB règles actives (attendu 1)"
  grep -qE "^${SERVICE_USER} ALL=\(root\) NOPASSWD: /usr/local/bin/forge-preview \*$" "$SUDOERS_DST" \
    && ok "règle conforme pour $SERVICE_USER (wrapper seul, NOPASSWD)" \
    || ko "la règle ne correspond pas au modèle attendu"
else
  ko "règle sudoers absente : $SUDOERS_DST"
fi

# Aucune règle sudoers ne doit autoriser docker directement.
if grep -rEsq '(^|[[:space:]])(/usr/bin/)?docker([[:space:]]|\*|$)' /etc/sudoers /etc/sudoers.d/ 2>/dev/null; then
  ko "une règle sudoers autorise docker directement (interdit)"
else
  ok "aucune règle sudoers n'autorise docker directement"
fi

echo "== D2 : isolation de l'accès =="
if id -nG "$SERVICE_USER" 2>/dev/null | grep -qw docker; then
  ko "$SERVICE_USER est membre du groupe docker (équivaut à root)"
else
  ok "$SERVICE_USER hors du groupe docker"
fi
AS_USER_DOCKER="$(as_user docker info 2>&1 | head -n1)"
case "$AS_USER_DOCKER" in
  *"permission denied"*|*"not found"*|"") ok "$SERVICE_USER n'accède pas au socket Docker sans privilège" ;;
  *) ko "$SERVICE_USER accède à Docker sans privilège : $AS_USER_DOCKER" ;;
esac
SUDO_DOCKER="$(sudo_n_as_user /usr/bin/docker ps 2>&1)"
case "$SUDO_DOCKER" in
  *"password is required"*|*"a password is required"*|*"not allowed"*)
    ok "sudo -n docker refusé pour $SERVICE_USER (aucun accès direct)" ;;
  *) ko "sudo -n docker a répondu : $(printf '%s' "$SUDO_DOCKER" | head -n1)" ;;
esac

echo "== D2 : fonctionnement du wrapper =="
HEALTH="$(sudo_n_as_user "$WRAPPER_DST" health 2>&1)"
if printf '%s' "$HEALTH" | grep -Eq '^[0-9]+\.[0-9]+'; then
  ok "sudo -n forge-preview health : OK (moteur $(printf '%s' "$HEALTH" | head -n1))"
else
  ko "sudo -n forge-preview health : échec — $(printf '%s' "$HEALTH" | head -n1)"
fi

# Refus attendus, exécutés en tant que service user via sudo -n (sans mot de passe).
check_refus() {
  local label="$1"; shift
  local out rc
  out="$(sudo_n_as_user "$WRAPPER_DST" "$@" 2>&1)"; rc=$?
  if [ "$rc" -eq 64 ]; then
    ok "refus attendu ($label) : code 64"
  else
    ko "refus attendu ($label) mais code $rc — $out"
  fi
}
check_refus "projet avec traversée"  start '../evil'   --port 8090 --workspace /tmp
check_refus "port hors plage"         start demo        --port 22   --workspace /tmp
check_refus "workspace hors racine"   start demo        --port 8090 --workspace /etc
check_refus "verbe inconnu"           exec demo
check_refus "option non listée"       start demo --port 8090 --workspace /tmp --privileged

# Le verbe passe doit réellement atteindre le moteur (échec propre, pas un refus).
PS_OUT="$(sudo_n_as_user "$WRAPPER_DST" ps -- 2>&1)"
if printf '%s' "$PS_OUT" | grep -qE '(NAMES|CONTAINER ID|^$)'; then
  ok "sudo -n forge-preview ps -- : le verbe atteint le moteur"
else
  ko "forge-preview ps -- : $(printf '%s' "$PS_OUT" | head -n1)"
fi

echo "== D2 : contrat d'exécution (garde-fous présents dans le wrapper) =="
for pat in \
  'security-opt no-new-privileges' \
  'cap-drop ALL' \
  'read-only' \
  'pids-limit' \
  'memory-swap' \
  'restart=no' \
  '/workspace:rw' \
  '127.0.0.1' ; do
  grep -qF -- "$pat" "$WRAPPER_DST" 2>/dev/null && ok "garde-fou présent : $pat" \
                                             || ko "garde-fou absent du wrapper : $pat"
done

echo
if [ "$fail" -ne 0 ]; then
  echo "RÉSULTAT : D2 NON conforme — corriger les lignes KO ci-dessus."
else
  echo "RÉSULTAT : D2 conforme."
fi
exit "$fail"
