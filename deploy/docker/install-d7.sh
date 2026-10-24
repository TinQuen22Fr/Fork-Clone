#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# install-d7.sh — Étape D7 : nettoyage et quotas disque.
#   - Réinstalle le wrapper (verbe borné `prune`) : sudo seul, pas de socket docker.
#   - Le quota `du` (PREVIEW_MAX_WORKSPACE_GB) et l'alerte disque
#     (PREVIEW_MIN_FREE_DISK_GB) vivent dans preview_runtime.py / server.py ;
#     la cadence est réglée par PREVIEW_HOUSEKEEPING_INTERVAL (secondes).
#   Aucun redémarrage automatique : la commande est affichée.
# sudo bash deploy/docker/install-d7.sh [service]
set -eu -o pipefail

PROG="install-d7.sh"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER_SRC="$SRC_DIR/forge-preview"
WRAPPER_DST=/usr/local/bin/forge-preview
SUDOERS_DST=/etc/sudoers.d/forge-preview
SERVICE="${1:-forge-backend.service}"
DOCKER=/usr/bin/docker

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
log() { printf '[D7] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "doit être lancé en root (sudo bash $0)"

# -- prérequis D2 ------------------------------------------------
[ -x "$WRAPPER_DST" ] || die "wrapper absent : $WRAPPER_DST (lancer install-d2.sh)"
[ -f "$SUDOERS_DST" ] || die "règle sudoers absente : $SUDOERS_DST (lancer install-d2.sh)"
[ -x "$DOCKER" ] || die "docker introuvable ($DOCKER)"
log "prérequis présents (wrapper + sudoers + docker)"

# -- wrapper a jour (D7 ajoute le verbe borné prune) ------------
install -o root -g root -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST" \
  || die "échec de copie du wrapper"
log "wrapper réinstallé : $WRAPPER_DST (root:root, 0755)"

# -- contrôle de bon sens : jamais de socket docker -------------
if grep -q 'docker\.sock' "$WRAPPER_DST"; then
  die "le wrapper référence docker.sock — installation refusée"
fi

# -- rappel des réglages (variables d'environnement du service) ---
cat >&2 <<INNEREOF
[D7] Réglages (variables d'environnement du service) :
  PREVIEW_HOUSEKEEPING_INTERVAL = 3600   (cadence de la boucle, secondes)
  PREVIEW_PRUNE_OLDER_DAYS      = 30     (rm conteneurs arrêtés > N jours)
  PREVIEW_MAX_WORKSPACE_GB      = 10     (refus de lancement au-delà, 0 = illimité)
  PREVIEW_MIN_FREE_DISK_GB      = 50     (alerte si / passe sous ce seuil)
INNEREOF

log "D7 installé. Appliquer : sudo systemctl restart $SERVICE"
log "Vérifier : sudo bash deploy/docker/verify-d7.sh"
