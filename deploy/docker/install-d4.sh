#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# install-d4.sh — Étape D4 : contrat d'exécution (réseau dédié + wrapper).
# sudo bash deploy/docker/install-d4.sh
set -eu -o pipefail

PROG="install-d4.sh"
DOCKER=/usr/bin/docker
NETWORK=${FORGE_PREVIEW_NETWORK:-forge-preview-net}
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER_SRC="$SRC_DIR/forge-preview"
WRAPPER_DST=/usr/local/bin/forge-preview

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
log() { printf '[D4] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "doit être lancé en root (sudo bash $0)"
[ -f "$WRAPPER_SRC" ] || die "wrapper source introuvable : $WRAPPER_SRC"

log "réseau dédié : $NETWORK"
if "$DOCKER" network inspect "$NETWORK" >/dev/null 2>&1; then
  log "réseau déjà présent"
else
  "$DOCKER" network create --driver bridge "$NETWORK" >/dev/null \
    || die "échec de création du réseau $NETWORK"
  log "réseau créé (driver bridge)"
fi
DRIVER="$("$DOCKER" network inspect -f '{{.Driver}}' "$NETWORK")"
[ "$DRIVER" = "bridge" ] || die "réseau $NETWORK : driver $DRIVER != bridge"

log "installation du wrapper : $WRAPPER_DST"
install -o root -g root -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST" \
  || die "échec de copie du wrapper"
log "wrapper installé (root:root, 0755)"
log "D4 installé. Vérifier : sudo bash deploy/docker/verify-d4.sh"
