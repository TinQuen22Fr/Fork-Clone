#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# install-d5.sh — Étape D5 : driver Docker dans preview_runtime.py.
# Rend le wrapper utilisable par l'utilisateur du service qui exécute les
# previews (la règle D2 est nominative) et active le driver par un drop-in
# systemd. Aucun redémarrage automatique : la commande est affichée.
# sudo bash deploy/docker/install-d5.sh [service]
set -eu -o pipefail

PROG="install-d5.sh"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER_DST=/usr/local/bin/forge-preview
SUDOERS_DST=/etc/sudoers.d/forge-preview
SERVICE="${1:-forge-backend.service}"
DOCKER=/usr/bin/docker
NETWORK=${FORGE_PREVIEW_NETWORK:-forge-preview-net}
IMAGE=${FORGE_PREVIEW_IMAGE:-forge-preview-base:local}
DROPIN_DIR="/etc/systemd/system/${SERVICE}.d"
DROPIN="${DROPIN_DIR}/50-forge-preview-driver.conf"

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
log() { printf '[D5] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "doit être lancé en root (sudo bash $0)"

# -- prérequis D2/D3/D4 ------------------------------------------------
[ -x "$WRAPPER_DST" ] || die "wrapper absent : $WRAPPER_DST (lancer install-d2.sh)"
"$DOCKER" network inspect "$NETWORK" >/dev/null 2>&1 \
  || die "réseau $NETWORK absent (lancer install-d4.sh)"
"$DOCKER" image inspect "$IMAGE" >/dev/null 2>&1 \
  || die "image $IMAGE absente (lancer build-d3.sh)"
log "prérequis présents (wrapper, réseau $NETWORK, image $IMAGE)"

# -- wrapper a jour (D5 ajoute les verbes exec et port2) ----------------
install -o root -g root -m 0755 "$SRC_DIR/forge-preview" "$WRAPPER_DST" \
  || die "échec de copie du wrapper"
log "wrapper réinstallé : $WRAPPER_DST (root:root, 0755)"

# -- utilisateur du service --------------------------------------------
systemctl cat "$SERVICE" >/dev/null 2>&1 || die "service inconnu : $SERVICE"
SVC_USER="$(systemctl show -p User --value "$SERVICE")"
[ -n "$SVC_USER" ] || SVC_USER=root
id -u "$SVC_USER" >/dev/null 2>&1 || die "utilisateur du service introuvable : $SVC_USER"
log "service $SERVICE exécuté par $SVC_USER"

# -- règle sudoers pour cet utilisateur (la règle D2 est nominative) ----
if [ "$SVC_USER" != "root" ] && ! sudo -l -U "$SVC_USER" 2>/dev/null \
     | grep -qF "$WRAPPER_DST"; then
  TMP_SUDO="$(mktemp)"
  cat >"$TMP_SUDO" <<SUDOEOF
# Genere par deploy/docker/install-d5.sh — etape D5 (previews Docker).
# Autorise l'utilisateur du service de preview a appeler le wrapper borne.
${SVC_USER} ALL=(root) NOPASSWD: ${WRAPPER_DST} *
SUDOEOF
  visudo -cqf "$TMP_SUDO" 2>/dev/null || { rm -f "$TMP_SUDO"; die "règle sudoers invalide"; }
  EXTRA="/etc/sudoers.d/forge-preview-${SVC_USER}"
  install -o root -g root -m 0440 "$TMP_SUDO" "$EXTRA"
  rm -f "$TMP_SUDO"
  log "règle sudoers ajoutée pour $SVC_USER : $EXTRA"
else
  log "règle sudoers déjà en place pour $SVC_USER"
fi

# -- driver docker via drop-in -----------------------------------------
if id -nG "$SVC_USER" 2>/dev/null | grep -qw docker; then
  log "ATTENTION : $SVC_USER est membre du groupe docker (équivaut à root)."
fi
mkdir -p "$DROPIN_DIR"
cat >"$DROPIN" <<'CONFEOF'
# Genere par deploy/docker/install-d5.sh — etape D5.
# Driver des previews : conteneurs isoles au lieu de process hotes.
[Service]
Environment=PREVIEW_DRIVER=docker
CONFEOF
chmod 0644 "$DROPIN"
systemctl daemon-reload
log "drop-in installé : $DROPIN (PREVIEW_DRIVER=docker)"

log "D5 installé. Appliquer : sudo systemctl restart $SERVICE"
log "Vérifier : sudo bash deploy/docker/verify-d5.sh"
