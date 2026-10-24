#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# install-d9.sh — Étape D9 : interdiction d'exécution sur l'OS principal.
#   - Réinstalle le wrapper (audit start|stop + syslog).
#   - Le garde-fou structurel (_host_exec_allowed) vit dans preview_runtime.py :
#     en mode docker, la seule exécution hôte autorisée est le wrapper sudo.
#   Aucun redémarrage automatique : la commande est affichée.
# sudo bash deploy/docker/install-d9.sh [service]
set -eu -o pipefail

PROG="install-d9.sh"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER_SRC="$SRC_DIR/forge-preview"
WRAPPER_DST=/usr/local/bin/forge-preview
SUDOERS_DST=/etc/sudoers.d/forge-preview
SERVICE="${1:-forge-backend.service}"
DOCKER=/usr/bin/docker

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
log() { printf '[D9] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "doit être lancé en root (sudo bash $0)"

# -- prérequis D2 ------------------------------------------------
[ -x "$WRAPPER_DST" ] || die "wrapper absent : $WRAPPER_DST (lancer install-d2.sh)"
[ -f "$SUDOERS_DST" ] || die "règle sudoers absente : $SUDOERS_DST (lancer install-d2.sh)"
[ -x "$DOCKER" ] || die "docker introuvable ($DOCKER)"
log "prérequis présents (wrapper + sudoers + docker)"

# -- wrapper à jour (D9 ajoute l'audit start|stop + syslog) -----
install -o root -g root -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST" \
  || die "échec de copie du wrapper"
log "wrapper réinstallé : $WRAPPER_DST (root:root, 0755)"

# -- contrôle de bon sens : jamais de socket docker -------------
if grep -q 'docker\.sock' "$WRAPPER_DST"; then
  die "le wrapper référence docker.sock — installation refusée"
fi

# -- audit : répertoire dédié (racine gérée par le wrapper root) --
AUDIT_DIR=${FORGE_PREVIEW_AUDIT_DIR:-/var/log/forge-preview}
mkdir -p "$AUDIT_DIR" || die "impossible de créer $AUDIT_DIR"
chmod 0750 "$AUDIT_DIR" 2>/dev/null || true
log "répertoire d'audit prêt : $AUDIT_DIR"

cat >&2 <<INNEREOF
[D9] Réglages (variables d'environnement) :
  FORGE_PREVIEW_AUDIT_DIR = ${AUDIT_DIR}  (journal forge-preview/audit.log)
  Chaque start|stop|rm est journalisé : qui (SUDO_USER), quand, projet, rc.
  Le garde-fou _host_exec_allowed (preview_runtime.py) interdit toute
  exécution hôte autre que le wrapper sudo en mode docker.
INNEREOF

log "D9 installé. Appliquer : sudo systemctl restart $SERVICE"
log "Vérifier : sudo bash deploy/docker/verify-d9.sh"
