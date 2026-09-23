#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# upgrade-preview.sh — met a jour l'instance PREVIEW (/var/www/forge-dev).
# La production (/var/www/forge, port 8001) n'est jamais touchee.
#
#   sudo bash deploy/upgrade-preview.sh                  # pull + deps + build
#   sudo bash deploy/upgrade-preview.sh --no-pull
#   sudo bash deploy/upgrade-preview.sh --backend-only
#   sudo bash deploy/upgrade-preview.sh --branch main
#
set -euo pipefail

PREVIEW_DIR="${PREVIEW_DIR:-/var/www/forge-dev}"
PREVIEW_PORT="${PREVIEW_PORT:-8002}"
SERVICE_NAME="${SERVICE_NAME:-forge-backend-preview}"
BRANCH=""
DO_PULL=1
DO_FRONTEND=1

while [ $# -gt 0 ]; do
  case "$1" in
    --no-pull)       DO_PULL=0 ;;
    --backend-only)  DO_FRONTEND=0 ;;
    --branch)        BRANCH="${2:-}"; shift ;;
    -h|--help)       sed -n '1,16p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1"; exit 1 ;;
  esac
  shift
done

c_ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
c_step() { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
c_warn() { printf '\033[1;33m  /!\\\033[0m %s\n' "$*"; }
die()    { printf '\033[1;31mERREUR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "a lancer en root : sudo bash deploy/upgrade-preview.sh"
[ -f "$PREVIEW_DIR/backend/server.py" ] || die "$PREVIEW_DIR : instance preview introuvable (lance setup-preview.sh)"
[ "$PREVIEW_DIR" != "/var/www/forge" ] || die "refus : ce script ne touche pas a la production"
cd "$PREVIEW_DIR"

VENV="$PREVIEW_DIR/backend/venv"
SERVICE_USER="$(stat -c %U "$PREVIEW_DIR" 2>/dev/null || echo root)"
STAMP="$(date +%F-%H%M%S)"

# 1. Sauvegarde du .env
[ -f backend/.env ] && cp backend/.env "backend/.env.bak-$STAMP" \
  && c_ok "backend/.env -> backend/.env.bak-$STAMP"

# 2. Code
if [ "$DO_PULL" -eq 1 ] && [ -d .git ]; then
  c_step "Mise a jour du code"
  CUR="$(git -c safe.directory=* rev-parse --abbrev-ref HEAD)"
  TARGET="${BRANCH:-$CUR}"
  [ -z "$(git -c safe.directory=* status --porcelain)" ] || {
    git -c safe.directory=* stash push -u -m "upgrade-preview-$STAMP" >/dev/null
    c_warn "modifications locales mises de cote (git stash pop pour les reprendre)"
  }
  git -c safe.directory=* fetch --prune origin
  [ "$TARGET" = "$CUR" ] || git -c safe.directory=* checkout "$TARGET"
  git -c safe.directory=* pull --ff-only origin "$TARGET"
  c_ok "branche $TARGET @ $(git -c safe.directory=* rev-parse --short HEAD)"
fi

# 3. Nouvelles variables .env (valeurs preview preservees)
if [ -f backend/.env ] && [ -f backend/env.example ]; then
  c_step "Nouvelles variables de configuration"
  MISSING="$(
    LC_ALL=C comm -23 --nocheck-order \
      <(grep -oE '^[A-Z0-9_]+=' "$PREVIEW_DIR/backend/env.example" | LC_ALL=C sort -u) \
      <(grep -oE '^[A-Z0-9_]+=' "$PREVIEW_DIR/backend/.env"        | LC_ALL=C sort -u)
  )"
  if [ -n "$MISSING" ]; then
    {
      printf '\n# --- Ajoute par upgrade-preview.sh le %s ---\n' "$STAMP"
      for key in $MISSING; do grep -m1 "^$key" backend/env.example; done
    } >> backend/.env
    echo "$MISSING" | sed 's/^/  + /'
  else
    c_ok "aucune nouvelle variable"
  fi
fi

# 4. Dependances (Kokoro reste optionnel : jamais bloquant)
c_step "Dependances Python"
[ -x "$VENV/bin/python" ] || die "venv introuvable ($VENV)"
HASH_FILE="$PREVIEW_DIR/.upgrade-requirements.sha"
NEW_HASH="$(sha256sum backend/requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$HASH_FILE" 2>/dev/null)" = "$NEW_HASH" ]; then
  c_ok "requirements.txt inchange"
else
  "$VENV/bin/pip" install --upgrade pip >/dev/null
  "$VENV/bin/pip" install --upgrade-strategy only-if-needed -r backend/requirements.txt
  echo "$NEW_HASH" > "$HASH_FILE"
fi
"$VENV/bin/python" -c "import ast;ast.parse(open('backend/server.py').read())" \
  || die "backend/server.py ne compile pas — mise a jour interrompue"
c_ok "server.py compile"

# 5. Frontend
if [ "$DO_FRONTEND" -eq 1 ] && [ -d frontend ] && command -v npm >/dev/null 2>&1; then
  c_step "Build frontend"
  cd frontend
  PKG_HASH_FILE="$PREVIEW_DIR/.upgrade-package.sha"
  PKG_HASH="$(sha256sum package.json | cut -d' ' -f1)"
  if [ "$(cat "$PKG_HASH_FILE" 2>/dev/null)" = "$PKG_HASH" ] && [ -d node_modules ]; then
    c_ok "package.json inchange"
  else
    npm install --no-audit --no-fund
    echo "$PKG_HASH" > "$PKG_HASH_FILE"
  fi
  npm run build
  [ -f build/index.html ] || die "build frontend echoue"
  cd "$PREVIEW_DIR"
  chown -R "$SERVICE_USER":"$SERVICE_USER" frontend/build 2>/dev/null || true
  c_ok "build/ regenere"
fi

# 6. Chemins inscriptibles + redemarrage
c_step "Redemarrage de $SERVICE_NAME"
mkdir -p workspace .playwright backend/static/screenshots
chown -R "$SERVICE_USER":"$SERVICE_USER" workspace .playwright backend/static/screenshots 2>/dev/null || true
systemctl restart "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME" || sleep 3
systemctl is-active --quiet "$SERVICE_NAME" || {
  journalctl -u "$SERVICE_NAME" -n 40 --no-pager
  die "$SERVICE_NAME ne demarre pas"
}
CODE=000
for _ in $(seq 1 10); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -X GET "http://127.0.0.1:$PREVIEW_PORT/api/health" || echo 000)"
  [ "$CODE" = "200" ] && break
  sleep 1
done
[ "$CODE" = "200" ] && c_ok "API preview repond 200" \
  || c_warn "API preview repond $CODE — journalctl -u $SERVICE_NAME -n 50"

c_step "Preview a jour"
echo "  Production intacte : /var/www/forge (port 8001)"
