#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# build-d3.sh — Etape D3 : construction de l'image de base commune.
#
# sudo bash deploy/docker/build-d3.sh
#
# Idempotent : relancer reconstruit l'image (cache Docker reutilise).
# Ne touche a aucun conteneur en cours.
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKER_DIR="$APP_DIR/deploy/docker"
DOCKERFILE="$DOCKER_DIR/preview-base.Dockerfile"
IMAGE="${FORGE_PREVIEW_IMAGE:-forge-preview-base:local}"
PREVIEW_UID="${PREVIEW_UID:-1000}"
PREVIEW_GID="${PREVIEW_GID:-1000}"

log()  { printf '\033[1;34m[D3]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m OK\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[D3] %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "build-d3.sh doit être lancé en root : sudo bash deploy/docker/build-d3.sh"
[ -f "$DOCKERFILE" ] || die "Dockerfile absent : $DOCKERFILE"
command -v docker >/dev/null 2>&1 || die "docker introuvable"

# Config Docker deterministe : le HOME de l'appelant peut etre absent ou
# non lisible (meme motif que deploy/docker/forge-preview).
export DOCKER_CONFIG="${DOCKER_CONFIG:-/tmp/forge-d3-dockercfg}"
mkdir -p "$DOCKER_CONFIG"

log "construction de $IMAGE"
log "  Dockerfile    : $DOCKERFILE"
log "  contexte      : $DOCKER_DIR"
log "  PREVIEW_UID   : $PREVIEW_UID"
log "  PREVIEW_GID   : $PREVIEW_GID"

docker build \
    --file "$DOCKERFILE" \
    --build-arg "PREVIEW_UID=$PREVIEW_UID" \
    --build-arg "PREVIEW_GID=$PREVIEW_GID" \
    --label "forge.step=D3" \
    --tag "$IMAGE" \
    "$DOCKER_DIR"

IMAGE_ID="$(docker image inspect -f '{{.Id}}' "$IMAGE" 2>/dev/null || true)"
[ -n "$IMAGE_ID" ] || die "image $IMAGE introuvable apres build"

log "image construite"
ok "tag         : $IMAGE"
ok "image id    : $IMAGE_ID"
ok "taille      : $(docker image inspect -f '{{.Size}}' "$IMAGE" | awk '{printf "%.1f Mo", $1/1024/1024}')"

log "D3 terminee. Verifier : sudo bash deploy/docker/verify-d3.sh"
