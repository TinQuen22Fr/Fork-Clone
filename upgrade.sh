#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# upgrade.sh — mise a jour d'une installation existante.
#   install.sh  = premiere installation (ou reinstallation complete)
#   upgrade.sh  = correctifs, nouveautes, nouvelles dependances, nouveau .env
#
# Usage (depuis la racine du projet, en root ou via sudo) :
#     sudo bash upgrade.sh                 # pull + deps + build + restart
#     sudo bash upgrade.sh --no-pull       # ne touche pas a git (code deja a jour)
#     sudo bash upgrade.sh --backend-only  # saute le build frontend
#     sudo bash upgrade.sh --frontend-only # saute pip + service
#     sudo bash upgrade.sh --branch main   # met a jour depuis une autre branche
#
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
VENV_DIR="${VENV_DIR:-$APP_DIR/backend/venv}"
SERVICE="${SERVICE:-forge-backend}"
SERVICE_USER="${SERVICE_USER:-quentin}"
BRANCH="${BRANCH:-}"
DO_PULL=1
DO_BACKEND=1
DO_FRONTEND=1

while [ $# -gt 0 ]; do
  case "$1" in
    --no-pull)       DO_PULL=0 ;;
    --backend-only)  DO_FRONTEND=0 ;;
    --frontend-only) DO_BACKEND=0 ;;
    --branch)        BRANCH="${2:-}"; shift ;;
    -h|--help)       sed -n '1,25p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1"; exit 1 ;;
  esac
  shift
done

c_ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
c_step() { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
c_warn() { printf '\033[1;33m  /!\\\033[0m %s\n' "$*"; }
die()    { printf '\033[1;31mERREUR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "a lancer en root : sudo bash upgrade.sh"
[ -f "$APP_DIR/backend/server.py" ] || die "$APP_DIR ne ressemble pas a la Forge"
cd "$APP_DIR"

# ---------------------------------------------------------------------------
# 1. Sauvegarde du .env (le seul fichier irremplacable)
# ---------------------------------------------------------------------------
c_step "Sauvegarde de la configuration"
STAMP="$(date +%F-%H%M%S)"
if [ -f backend/.env ]; then
  cp backend/.env "backend/.env.bak-$STAMP"
  c_ok "backend/.env -> backend/.env.bak-$STAMP"
else
  c_warn "backend/.env absent — lance install.sh d'abord ?"
fi

# ---------------------------------------------------------------------------
# 2. Recuperation du code
# ---------------------------------------------------------------------------
if [ "$DO_PULL" -eq 1 ] && [ -d .git ]; then
  c_step "Mise a jour du code"
  CUR_BRANCH="$(git -c safe.directory=* rev-parse --abbrev-ref HEAD)"
  TARGET="${BRANCH:-$CUR_BRANCH}"
  if [ -n "$(git -c safe.directory=* status --porcelain)" ]; then
    c_warn "modifications locales detectees, mises de cote (git stash)"
    git -c safe.directory=* stash push -u -m "upgrade-$STAMP" >/dev/null
    STASHED=1
  fi
  git -c safe.directory=* fetch --prune origin
  [ "$TARGET" = "$CUR_BRANCH" ] || git -c safe.directory=* checkout "$TARGET"
  git -c safe.directory=* pull --ff-only origin "$TARGET"
  c_ok "branche $TARGET @ $(git -c safe.directory=* rev-parse --short HEAD)"
  [ "${STASHED:-0}" -eq 1 ] && c_warn "recupere tes modifs avec : git stash pop"
else
  c_step "Code : pull ignore"
fi

# ---------------------------------------------------------------------------
# 3. Nouvelles cles .env : ajoutees a la fin, sans ecraser l'existant
# ---------------------------------------------------------------------------
if [ -f backend/.env ] && [ -f backend/env.example ]; then
  c_step "Nouvelles variables de configuration"
  MISSING="$(
    LC_ALL=C comm -23 --nocheck-order \
      <(grep -oE '^[A-Z0-9_]+=' "$APP_DIR/backend/env.example" | LC_ALL=C sort -u) \
      <(grep -oE '^[A-Z0-9_]+=' "$APP_DIR/backend/.env"        | LC_ALL=C sort -u)
  )"
  if [ -n "$MISSING" ]; then
    {
      printf '\n# --- Ajoute par upgrade.sh le %s ---\n' "$STAMP"
      for key in $MISSING; do
        grep -m1 "^$key" backend/env.example
      done
    } >> backend/.env
    echo "$MISSING" | sed 's/=$/ ajoutee/' | sed 's/^/  + /'
    c_warn "verifie les valeurs : nano backend/.env"
  else
    c_ok "aucune nouvelle variable"
  fi
fi

# ---------------------------------------------------------------------------
# 4. Dependances backend (uniquement si requirements.txt a bouge)
# ---------------------------------------------------------------------------
if [ "$DO_BACKEND" -eq 1 ]; then
  c_step "Dependances Python"
  [ -x "$VENV_DIR/bin/python" ] || die "venv introuvable ($VENV_DIR) — lance install.sh"
  HASH_FILE="$APP_DIR/.upgrade-requirements.sha"
  NEW_HASH="$(sha256sum backend/requirements.txt | cut -d' ' -f1)"
  if [ "$(cat "$HASH_FILE" 2>/dev/null)" = "$NEW_HASH" ]; then
    c_ok "requirements.txt inchange, installation sautee"
  else
    "$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
    "$VENV_DIR/bin/pip" install --upgrade-strategy only-if-needed \
      -r backend/requirements.txt
    echo "$NEW_HASH" > "$HASH_FILE"
    c_ok "dependances a jour"
  fi
  # Kokoro est optionnel (requirements-kokoro.txt) : jamais bloquant ici.
  if "$VENV_DIR/bin/python" -c "import kokoro_onnx" >/dev/null 2>&1; then
    c_ok "moteur TTS local Kokoro present"
  else
    c_ok "Kokoro absent — TTS assure par edge-tts (sudo bash deploy/install-kokoro.sh pour l'ajouter)"
  fi

  "$VENV_DIR/bin/python" -c "import ast,sys;ast.parse(open('backend/server.py').read())" \
    || die "backend/server.py ne compile pas — mise a jour interrompue"
  c_ok "server.py compile"
fi

# ---------------------------------------------------------------------------
# 5. Frontend : build seulement si les sources ont change
# ---------------------------------------------------------------------------
if [ "$DO_FRONTEND" -eq 1 ] && [ -d frontend ]; then
  c_step "Build frontend"
  cd frontend
  PKG_HASH_FILE="$APP_DIR/.upgrade-package.sha"
  PKG_HASH="$(sha256sum package.json | cut -d' ' -f1)"
  if [ "$(cat "$PKG_HASH_FILE" 2>/dev/null)" = "$PKG_HASH" ] && [ -d node_modules ]; then
    c_ok "package.json inchange, npm install saute"
  else
    npm install --no-audit --no-fund
    echo "$PKG_HASH" > "$PKG_HASH_FILE"
  fi
  npm run build
  [ -f build/index.html ] || die "build frontend echoue (build/index.html absent)"
  c_ok "build/ regenere"
  cd "$APP_DIR"
fi

# ---------------------------------------------------------------------------
# 6. Unite systemd : mise a jour si le modele du depot a change
# ---------------------------------------------------------------------------
if [ "$DO_BACKEND" -eq 1 ] && [ -f deploy/forge-backend.service ]; then
  c_step "Service systemd"
  UNIT="/etc/systemd/system/$SERVICE.service"
  if [ -f "$UNIT" ]; then
    # JAMAIS d'ecrasement : l'unite en place peut contenir du durcissement
    # (ProtectSystem, ReadWritePaths, namespaces Playwright...) absent du modele.
    c_ok "unite existante conservee intacte ($UNIT)"
    if ! cmp -s deploy/forge-backend.service "$UNIT"; then
      c_warn "le modele du depot differe de ton unite — rien n'a ete touche."
      echo "      Compare si tu veux recuperer une nouveaute :"
      echo "      diff -u $UNIT deploy/forge-backend.service"
    fi
  else
    cp deploy/forge-backend.service "$UNIT"
    systemctl daemon-reload
    systemctl enable "$SERVICE" >/dev/null 2>&1 || true
    c_ok "unite installee depuis le modele (aucune n'existait)"
  fi
fi

# ---------------------------------------------------------------------------
# 7. Nginx : signale un ecart, sans rien imposer
# ---------------------------------------------------------------------------
if [ -f deploy/nginx-forge.conf ] && [ -f /etc/nginx/sites-available/forge ]; then
  if ! cmp -s deploy/nginx-forge.conf /etc/nginx/sites-available/forge; then
    c_warn "la config Nginx du depot differe de celle installee :"
    echo "      diff deploy/nginx-forge.conf /etc/nginx/sites-available/forge"
  fi
fi

# ---------------------------------------------------------------------------
# 8. Redemarrage + verification
# ---------------------------------------------------------------------------
if [ "$DO_BACKEND" -eq 1 ]; then
  c_step "Chemins inscriptibles exiges par systemd (ReadWritePaths)"
  # Si un chemin de ReadWritePaths n'existe pas, systemd echoue avec
  # status=226/NAMESPACE des le restart.
  mkdir -p "$APP_DIR/workspace" "$APP_DIR/.playwright" \
           "$APP_DIR/backend/static/screenshots"
  chown -R "$SERVICE_USER":"$SERVICE_USER" \
    "$APP_DIR/workspace" "$APP_DIR/.playwright" \
    "$APP_DIR/backend/static/screenshots" 2>/dev/null \
    || c_warn "chown impossible (utilisateur $SERVICE_USER inconnu ?)"
  c_ok "workspace/, .playwright/, backend/static/screenshots/ prets"

  c_step "Redemarrage de $SERVICE"
  systemctl restart "$SERVICE"
  systemctl is-active --quiet "$SERVICE" || sleep 3
  systemctl is-active --quiet "$SERVICE" || {
    journalctl -u "$SERVICE" -n 40 --no-pager
    die "$SERVICE ne demarre pas (journal ci-dessus)"
  }
  c_ok "service actif"

  PORT="$(grep -m1 -oE '^PORT=[0-9]+' "$APP_DIR/backend/.env" 2>/dev/null | cut -d= -f2 || true)"
  URL="http://127.0.0.1:${PORT:-8001}/api/health"
  CODE=000
  for _ in $(seq 1 10); do
    CODE="$(curl -s -o /dev/null -w '%{http_code}' -X GET "$URL" || echo 000)"
    [ "$CODE" = "200" ] && break
    sleep 1
  done
  if [ "$CODE" = "200" ]; then
    c_ok "API /api/health repond 200"
  else
    c_warn "API /api/health repond $CODE — inspecte : journalctl -u $SERVICE -n 50"
  fi
fi

c_step "Mise a jour terminee"
cat <<EOF
  Journal en direct   : journalctl -u $SERVICE -f
  Nouvelles variables : nano $APP_DIR/backend/.env
  Retour arriere      : git checkout <sha> && sudo bash upgrade.sh --no-pull
  Sauvegarde .env     : backend/.env.bak-$STAMP
EOF
