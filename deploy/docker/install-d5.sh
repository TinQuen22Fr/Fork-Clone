#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# install-d5.sh — Étape D5 : driver docker du backend.
# Le driver est du code backend (déployé par git) ; côté système, D5 met à jour
# le wrapper (verbe borné exec + --port2 pour les monorepos) et garantit le
# réseau bridge dédié.
# sudo bash deploy/docker/install-d5.sh
set -eu -o pipefail

PROG="install-d5.sh"
DOCKER=/usr/bin/docker
NETWORK=${FORGE_PREVIEW_NETWORK:-forge-preview-net}
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER_SRC="$SRC_DIR/forge-preview"
WRAPPER_DST=/usr/local/bin/forge-preview
BACKEND_SRC="$(cd "$SRC_DIR/../.." && pwd)/backend/preview_runtime.py"

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
log() { printf '[D5] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "doit être lancé en root (sudo bash $0)"
[ -f "$WRAPPER_SRC" ] || die "wrapper source introuvable : $WRAPPER_SRC"

# Réseau bridge dédié (créé par D4, garanti ici).
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

# Wrapper mis à jour (verbe borné exec + --port2 pour les monorepos).
log "installation du wrapper : $WRAPPER_DST"
install -o root -g root -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST" \
  || die "échec de copie du wrapper"
log "wrapper installé (root:root, 0755)"

# Le driver backend est du code Python déployé par git ; aucun binaire système.
log "backend : $BACKEND_SRC (déployé par git, non copié ici)"
if python3 -m py_compile "$BACKEND_SRC" 2>/dev/null; then
  log "backend : py_compile OK"
else
  log "backend : py_compile impossible ici (vérifier avec verify-d5.sh)"
fi

log "D5 installé. Vérifier : sudo bash deploy/docker/verify-d5.sh"
