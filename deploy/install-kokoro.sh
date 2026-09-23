#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# Telecharge les modeles Kokoro (TTS local, CPU, 100% gratuit) dans
# backend/models/. Idempotent : ne retelecharge pas si les fichiers sont la.
#
#     sudo bash deploy/install-kokoro.sh
#
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODELS_DIR="${MODELS_DIR:-$APP_DIR/backend/models}"
SERVICE_USER="${SERVICE_USER:-$(stat -c %U "$APP_DIR" 2>/dev/null || echo root)}"
BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERREUR:\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p "$MODELS_DIR"

fetch() {
  local name="$1" dest="$MODELS_DIR/$1"
  if [ -s "$dest" ]; then
    log "$name deja present ($(du -h "$dest" | cut -f1))"
    return
  fi
  log "Telechargement de $name"
  curl -fL --progress-bar "$BASE_URL/$name" -o "$dest.part" || die "echec du telechargement de $name"
  mv "$dest.part" "$dest"
}

fetch kokoro-v1.0.onnx     # ~310 Mo
fetch voices-v1.0.bin      # ~27 Mo

chown -R "$SERVICE_USER":"$SERVICE_USER" "$MODELS_DIR" 2>/dev/null || true
chmod -R a+rX "$MODELS_DIR"

cat <<MSG

------------------------------------------------------------------------
Modeles Kokoro installes dans $MODELS_DIR

Dans $APP_DIR/backend/.env :

    TTS_ENGINE=auto
    KOKORO_MODEL_PATH=$MODELS_DIR/kokoro-v1.0.onnx
    KOKORO_VOICES_PATH=$MODELS_DIR/voices-v1.0.bin
    KOKORO_VOICE=ff_siwis

Attention : ProtectSystem=strict + ReadOnlyPaths=/var/www/forge rendent ce
dossier en lecture seule pour le service, ce qui suffit (lecture des modeles).

    sudo systemctl restart forge-backend
------------------------------------------------------------------------
MSG
