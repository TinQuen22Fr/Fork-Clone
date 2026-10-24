#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# install-d10.sh — Étape D10 : observabilité et garde-fous anti-boucle.
#   - Réinstalle le wrapper (ajout du verbe stats : CPU/RAM instantanés).
#   - Vérifie la présence des garde-fous (compteur, backoff, métriques)
#     dans preview_runtime.py et des métriques dans server.py.
#   - Aucun redémarrage automatique : la commande est affichée.
# sudo bash deploy/docker/install-d10.sh [service]
set -eu -o pipefail

PROG="install-d10.sh"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOTDIR="$(cd "$SRC_DIR/../.." && pwd)"
WRAPPER_SRC="$SRC_DIR/forge-preview"
WRAPPER_DST=/usr/local/bin/forge-preview
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
SERVER="$ROOTDIR/backend/server.py"
SERVICE="${1:-forge-backend.service}"

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
log() { printf '[D10] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "doit être lancé en root (sudo bash $0)"

# -- prérequis D2 ------------------------------------------------
[ -x "$WRAPPER_DST" ] || die "wrapper absent : $WRAPPER_DST (lancer install-d2.sh)"
[ -x /usr/bin/docker ] || die "docker introuvable"
log "prérequis présents (wrapper + sudoers + docker)"

# -- wrapper à jour (D10 ajoute le verbe stats) ------------------
install -o root -g root -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST" \
  || die "échec de copie du wrapper"
log "wrapper réinstallé : $WRAPPER_DST (root:root, 0755)"

if grep -q 'docker\.sock' "$WRAPPER_DST"; then
  die "le wrapper référence docker.sock — installation refusée"
fi

# -- garde-fous anti-boucle + backoff + métriques dans le runtime --
[ -f "$RUNTIME" ] || die "preview_runtime.py absent : $RUNTIME"
for pat in \
  'RESTART_MAX_FAILURES' \
  'RESTART_WINDOW' \
  'RESTART_BACKOFF' \
  'def _mark_failure' \
  'def backoff_remaining' \
  'def _record_failure' \
  'def _record_success' \
  'def is_disabled' \
  'def stats' \
  'def active_previews' ; do
  grep -qF -- "$pat" "$RUNTIME" || die "$pat absent de preview_runtime.py"
done
log "garde-fous anti-boucle et métriques présents dans preview_runtime.py"

# -- métriques exposées dans /chat/status -------------------------
grep -qF 'preview_mgr().active_previews()' "$SERVER" \
  || die "métriques preview absentes de /chat/status (server.py)"
log "métriques preview exposées dans /chat/status"

cat >&2 <<INNEREOF
[D10] Réglages (constantes du module) :
  RESTART_WINDOW       = 300 s   (fenêtre de comptage des échecs)
  RESTART_MAX_FAILURES = 3       (au-delà : preview désactivée + signalée)
  RESTART_BACKOFF      = 30 s    (délai min entre deux démarrages)
  Métriques /chat/status -> previews.<projet> : running, disabled, uptime,
  last_exit_code, phase, stats{cpu_percent, mem_usage, pids}.
INNEREOF

log "D10 installé. Appliquer : sudo systemctl restart $SERVICE"
log "Vérifier : sudo bash deploy/docker/verify-d10.sh"
