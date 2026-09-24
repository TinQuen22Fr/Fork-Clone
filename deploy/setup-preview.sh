#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# setup-preview.sh — installe une instance PREVIEW parallele a la production.
#
#   Production : /var/www/forge      port 8001  base forge           (INTOUCHEE)
#   Preview    : /var/www/forge-dev  port 8002  base forge_preview
#
# Aucun conteneur, aucun Docker : venv Python + systemd + Nginx.
#
#   sudo bash deploy/setup-preview.sh
#
# Variables surchargeables :
#   PREVIEW_DIR=/var/www/forge-dev   PREVIEW_PORT=8002
#   PREVIEW_DB=forge_preview         PREVIEW_BRANCH=claude-ai
#   PREVIEW_DOMAIN=preview-forge.quentin-astro.fr
#   SERVICE_USER=<owner de la prod>  REPO_URL=<origin de la prod>
#
set -euo pipefail

PROD_DIR="${PROD_DIR:-/var/www/forge}"
PREVIEW_DIR="${PREVIEW_DIR:-/var/www/forge-dev}"
PREVIEW_PORT="${PREVIEW_PORT:-8002}"
PREVIEW_DB="${PREVIEW_DB:-forge_preview}"
PREVIEW_BRANCH="${PREVIEW_BRANCH:-claude-ai}"
PREVIEW_DOMAIN="${PREVIEW_DOMAIN:-preview-forge.quentin-astro.fr}"
SERVICE_NAME="forge-backend-preview"
SERVICE_USER="${SERVICE_USER:-$(stat -c %U "$PROD_DIR" 2>/dev/null || echo root)}"

c_ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
c_step() { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
c_warn() { printf '\033[1;33m  /!\\\033[0m %s\n' "$*"; }
die()    { printf '\033[1;31mERREUR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "a lancer en root : sudo bash deploy/setup-preview.sh"
[ "$PREVIEW_DIR" != "$PROD_DIR" ] || die "PREVIEW_DIR ne peut pas etre la production"
[ "$PREVIEW_PORT" != "8001" ] || die "le port 8001 est celui de la production"

# ---------------------------------------------------------------------------
# 1. Code source
# ---------------------------------------------------------------------------
c_step "Code source dans $PREVIEW_DIR"
if [ -d "$PREVIEW_DIR/.git" ]; then
  git -C "$PREVIEW_DIR" -c safe.directory=* fetch --prune origin
  git -C "$PREVIEW_DIR" -c safe.directory=* checkout "$PREVIEW_BRANCH"
  git -C "$PREVIEW_DIR" -c safe.directory=* pull --ff-only origin "$PREVIEW_BRANCH"
  c_ok "depot existant mis a jour"
else
  REPO_URL="${REPO_URL:-$(git -C "$PROD_DIR" -c safe.directory=* remote get-url origin 2>/dev/null || true)}"
  [ -n "$REPO_URL" ] || die "REPO_URL introuvable — passe REPO_URL=https://github.com/..."
  mkdir -p "$(dirname "$PREVIEW_DIR")"
  git clone --branch "$PREVIEW_BRANCH" "$REPO_URL" "$PREVIEW_DIR"
  c_ok "depot clone depuis $REPO_URL"
fi
chown -R "$SERVICE_USER":"$SERVICE_USER" "$PREVIEW_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 2. Virtualenv dedie (jamais celui de la prod)
# ---------------------------------------------------------------------------
c_step "Virtualenv Python dedie"
VENV="$PREVIEW_DIR/backend/venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || die "creation du venv impossible"
fi
"$VENV/bin/pip" install --upgrade pip >/dev/null
# Kokoro est dans requirements-kokoro.txt : jamais bloquant sous Python 3.14.
"$VENV/bin/pip" install --upgrade-strategy only-if-needed \
  -r "$PREVIEW_DIR/backend/requirements.txt" \
  || die "installation des dependances echouee"
c_ok "dependances installees ($("$VENV/bin/python" -V))"

# ---------------------------------------------------------------------------
# 3. .env de preview (base et port distincts, secrets repris de la prod)
# ---------------------------------------------------------------------------
c_step "Configuration $PREVIEW_DIR/backend/.env"
ENV_FILE="$PREVIEW_DIR/backend/.env"
if [ -f "$ENV_FILE" ]; then
  c_ok ".env deja present (non modifie)"
else
  if [ -f "$PROD_DIR/backend/.env" ]; then
    cp "$PROD_DIR/backend/.env" "$ENV_FILE"
    c_ok "cles reprises depuis la production"
  else
    cp "$PREVIEW_DIR/backend/env.example" "$ENV_FILE"
    c_warn "aucun .env de prod : modele copie, complete les cles"
  fi
  # Valeurs specifiques a la preview (ecrasees si deja presentes).
  python3 - "$ENV_FILE" "$PREVIEW_PORT" "$PREVIEW_DB" "$PREVIEW_DIR" <<'PY'
import re, sys
path, port, db, root = sys.argv[1:5]
over = {
    "PORT": port,
    "DB_NAME": db,
    "WORKSPACE_ROOT": f"{root}/workspace",
    "WORKSPACE_DIR": root,
    "PLAYWRIGHT_BROWSERS_PATH": f"{root}/.playwright",
}
text = open(path).read()
if text and not text.endswith("\n"):
    text += "\n"
for key, val in over.items():
    line = f"{key}={val}"
    if re.search(rf"(?m)^{key}=", text):
        text = re.sub(rf"(?m)^{key}=.*$", line, text)
    else:
        text += line + "\n"
open(path, "w").write(text)
print("  ok PORT, DB_NAME, WORKSPACE_ROOT, PLAYWRIGHT_BROWSERS_PATH adaptes")
PY
fi
chmod 600 "$ENV_FILE"
chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 4. Chemins inscriptibles (ReadWritePaths du service)
# ---------------------------------------------------------------------------
c_step "Chemins inscriptibles"
mkdir -p "$PREVIEW_DIR/workspace" "$PREVIEW_DIR/.playwright" \
         "$PREVIEW_DIR/backend/static/screenshots"
chown -R "$SERVICE_USER":"$SERVICE_USER" \
  "$PREVIEW_DIR/workspace" "$PREVIEW_DIR/.playwright" \
  "$PREVIEW_DIR/backend/static/screenshots" 2>/dev/null || true
c_ok "workspace/, .playwright/, static/screenshots/ prets"

# ---------------------------------------------------------------------------
# 5. Build frontend
# ---------------------------------------------------------------------------
c_step "Build frontend"
if command -v npm >/dev/null 2>&1; then
  FRONT_ENV="$PREVIEW_DIR/frontend/.env"
  if [ ! -f "$FRONT_ENV" ]; then
    printf 'REACT_APP_BACKEND_URL=https://%s\nVITE_PREVIEW_URL=https://%s\n' \
      "$PREVIEW_DOMAIN" "$PREVIEW_DOMAIN" > "$FRONT_ENV"
    c_ok "frontend/.env cree (backend = https://$PREVIEW_DOMAIN)"
  fi
  (cd "$PREVIEW_DIR/frontend" && npm install --no-audit --no-fund && npm run build)
  [ -f "$PREVIEW_DIR/frontend/build/index.html" ] || die "build frontend echoue"
  chown -R "$SERVICE_USER":"$SERVICE_USER" "$PREVIEW_DIR/frontend/build" 2>/dev/null || true
  c_ok "build/ genere"
else
  c_warn "npm absent : build frontend saute"
fi

# ---------------------------------------------------------------------------
# 6. Service systemd dedie (la prod n'est jamais touchee)
# ---------------------------------------------------------------------------
c_step "Service $SERVICE_NAME"
UNIT="/etc/systemd/system/$SERVICE_NAME.service"
if [ -f "$UNIT" ]; then
  c_ok "unite existante conservee intacte"
else
  cat > "$UNIT" <<UNITEOF
[Unit]
Description=Claude Unchained Forge - backend PREVIEW (port $PREVIEW_PORT)
After=network.target mongodb.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$PREVIEW_DIR/backend

Environment="PATH=$PREVIEW_DIR/backend/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PLAYWRIGHT_BROWSERS_PATH=$PREVIEW_DIR/.playwright"
EnvironmentFile=$ENV_FILE

ExecStart=$PREVIEW_DIR/backend/venv/bin/uvicorn server:app --host 127.0.0.1 --port $PREVIEW_PORT --workers 1 --proxy-headers

# Isolation du systeme
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

# Chemins autorises en ecriture — NE RIEN RETIRER.
# /etc/nginx /run /var/log/nginx sont indispensables a la regeneration de la map
# des previews sous ProtectSystem=strict (sinon PREVIEW en 503).
# Voir deploy/KNOWN_ISSUE_preview_map_readonly.md
ReadWritePaths=$PREVIEW_DIR/workspace $PREVIEW_DIR/.playwright $PREVIEW_DIR/backend/static/screenshots /dev/shm /etc/nginx /run /var/log/nginx

# Chemins en lecture seule indispensables
ReadOnlyPaths=$PREVIEW_DIR

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITEOF
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
  c_ok "unite creee : $UNIT"
fi

systemctl restart "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME" || sleep 3
systemctl is-active --quiet "$SERVICE_NAME" || {
  journalctl -u "$SERVICE_NAME" -n 30 --no-pager
  die "$SERVICE_NAME ne demarre pas (journal ci-dessus)"
}
CODE=000
for _ in $(seq 1 10); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -X GET "http://127.0.0.1:$PREVIEW_PORT/api/health" || echo 000)"
  [ "$CODE" = "200" ] && break
  sleep 1
done
[ "$CODE" = "200" ] && c_ok "API preview repond 200 sur $PREVIEW_PORT" \
  || c_warn "API preview repond $CODE — journalctl -u $SERVICE_NAME -n 50"

# ---------------------------------------------------------------------------
# 7. Modele Nginx (jamais active automatiquement)
# ---------------------------------------------------------------------------
c_step "Modele Nginx"
TEMPLATE="$PREVIEW_DIR/deploy/nginx-forge-preview.conf"
OUT="/etc/nginx/sites-available/forge-preview"
if [ -f "$TEMPLATE" ]; then
  sed -e "s|__PREVIEW_DOMAIN__|$PREVIEW_DOMAIN|g" \
      -e "s|__PREVIEW_DIR__|$PREVIEW_DIR|g" \
      -e "s|__PREVIEW_PORT__|$PREVIEW_PORT|g" "$TEMPLATE" > "$OUT.new"
  if [ -f "$OUT" ] && cmp -s "$OUT" "$OUT.new"; then
    rm -f "$OUT.new"
    c_ok "config Nginx deja en place et identique"
  elif [ -f "$OUT" ]; then
    c_warn "config existante conservee — compare : diff -u $OUT $OUT.new"
  else
    mv "$OUT.new" "$OUT"
    c_ok "config generee : $OUT"
    c_warn "activation manuelle :"
    echo "      sudo ln -s $OUT /etc/nginx/sites-enabled/forge-preview"
    echo "      sudo certbot --nginx -d $PREVIEW_DOMAIN"
    echo "      sudo nginx -t && sudo systemctl reload nginx"
  fi
else
  c_warn "modele $TEMPLATE absent"
fi

c_step "Instance preview prete"
cat <<EOF
  Repertoire : $PREVIEW_DIR
  Port       : $PREVIEW_PORT (production 8001 intacte)
  Base Mongo : $PREVIEW_DB
  Service    : systemctl status $SERVICE_NAME
  Mise a jour: sudo bash $PREVIEW_DIR/deploy/upgrade-preview.sh
  Bouton UI  : VITE_PREVIEW_URL=https://$PREVIEW_DOMAIN dans
               $PROD_DIR/frontend/.env puis rebuild de la production
EOF
