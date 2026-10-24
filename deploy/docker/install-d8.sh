#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# install-d8.sh — Étape D8 : secrets et masquage.
#   Aucun fichier à déployer côté système : le filtrage du .env (liste blanche),
#   l'injection --env et le masquage redact_secrets() vivent dans
#   preview_runtime.py (déployé par le backend). Ce script vérifie que les
#   binaires de contrôle sont présents et rappelle les consignes de recette.
# sudo bash deploy/docker/install-d8.sh [service]
set -eu -o pipefail

PROG="install-d8.sh"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOTDIR="$(cd "$SRC_DIR/../.." && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
SERVICE="${1:-forge-backend.service}"

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
log() { printf '[D8] %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "doit être lancé en root (sudo bash $0)"

[ -f "$RUNTIME" ] || die "preview_runtime.py absent : $RUNTIME"

# Le masquage doit être présent dans le module runtime.
grep -qF 'def redact_secrets' "$RUNTIME" || die "redact_secrets() absent de preview_runtime.py"
grep -qF 'def _project_env' "$RUNTIME" || die "_project_env() absent de preview_runtime.py"
grep -qF 'PUBLIC_ENV_PREFIXES' "$RUNTIME" || die "liste blanche PUBLIC_ENV_PREFIXES absente"
log "masquage et liste blanche présents dans preview_runtime.py"

cat >&2 <<INNEREOF
[D8] Rappel des consignes (aucune variable d'environnement système à poser) :
  - le .env du projet est lu côté backend et SEULES les clés publiques
    (VITE_, NEXT_PUBLIC_, REACT_APP_, PUBLIC_, etc.) sont injectées en --env.
  - aucune clé SECRET/KEY/TOKEN/PASSWORD/DSN/URL de base de données n'est injectée.
  - le .env global de la Forge n'est JAMAIS monté dans un conteneur (contrat D4).
  - toute sortie (logs, build, inspect, statut) passe par redact_secrets().
  - côté agents : sed/grep tronqués, jamais de cat de .env.
INNEREOF

log "D8 prêt. Redéploiement du backend : sudo systemctl restart $SERVICE"
log "Vérifier : sudo bash deploy/docker/verify-d8.sh"
