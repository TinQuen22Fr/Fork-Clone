#!/usr/bin/env bash
#
# Claude Unchained Forge — installation / mise à jour.
#
# Rejouable sans danger : ne touche jamais un .env existant, ne remplace
# le service systemd qu'après confirmation si son contenu a changé, et ne
# touche jamais à Nginx (à faire à la main — voir deploy/nginx-forge.conf).
#
# Usage :
#   cd /var/www/forge        # racine du dépôt cloné
#   ./install.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$APP_DIR/backend"
FRONTEND_DIR="$APP_DIR/frontend"
SERVICE_NAME="forge-backend"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

c_info()  { printf '\033[36m[i]\033[0m %s\n' "$1"; }
c_ok()    { printf '\033[32m[OK]\033[0m %s\n' "$1"; }
c_warn()  { printf '\033[33m[!]\033[0m %s\n' "$1"; }
c_error() { printf '\033[31m[X]\033[0m %s\n' "$1" >&2; }

# ---------------------------------------------------------------------------
# 0. Vérifications préalables
# ---------------------------------------------------------------------------
command -v python3 >/dev/null || { c_error "python3 introuvable."; exit 1; }
command -v npm     >/dev/null || { c_error "npm introuvable."; exit 1; }
command -v systemctl >/dev/null || { c_error "systemctl introuvable (ce script cible une machine avec systemd)."; exit 1; }

c_info "Répertoire de l'application : $APP_DIR"

# ---------------------------------------------------------------------------
# 1. Backend — environnement virtuel + dépendances
# ---------------------------------------------------------------------------
c_info "Backend : environnement virtuel..."
if [ ! -d "$BACKEND_DIR/venv" ]; then
    python3 -m venv "$BACKEND_DIR/venv"
    c_ok "venv créé."
else
    c_ok "venv déjà présent."
fi

"$BACKEND_DIR/venv/bin/pip" install --upgrade pip --quiet
"$BACKEND_DIR/venv/bin/pip" install -r "$BACKEND_DIR/requirements.txt" --quiet
c_ok "Dépendances Python installées."

# ---------------------------------------------------------------------------
# 2. Backend — .env (ne JAMAIS écraser un .env existant)
# ---------------------------------------------------------------------------
if [ ! -f "$BACKEND_DIR/.env" ]; then
    cp "$BACKEND_DIR/env.example" "$BACKEND_DIR/.env"
    chmod 600 "$BACKEND_DIR/.env"
    c_warn ".env créé depuis env.example — ÉDITEZ-LE avant de continuer :"
    c_warn "  nano $BACKEND_DIR/.env"
    c_warn "Renseignez au minimum JWT_SECRET, MONGO_URL, FRONTEND_URL."
    c_warn "Relancez ce script une fois le .env complété."
    exit 0
else
    c_ok ".env déjà présent (non modifié)."
    chmod 600 "$BACKEND_DIR/.env" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# 3. Frontend — dépendances + build
# ---------------------------------------------------------------------------
c_info "Frontend : installation des dépendances (npm)..."
(cd "$FRONTEND_DIR" && npm install)
c_ok "Dépendances Node installées."

if [ ! -f "$FRONTEND_DIR/.env" ] && [ -f "$FRONTEND_DIR/.env.example" ]; then
    cp "$FRONTEND_DIR/.env.example" "$FRONTEND_DIR/.env"
    c_ok "frontend/.env créé depuis l'exemple (valeurs par défaut : OK dans le cas nominal)."
fi

c_info "Frontend : build de production..."
(cd "$FRONTEND_DIR" && npm run build)
c_ok "Build généré dans $FRONTEND_DIR/build/"

# ---------------------------------------------------------------------------
# 4. Service systemd — n'écrase qu'après confirmation si le contenu diffère
# ---------------------------------------------------------------------------
NEW_SERVICE="$APP_DIR/deploy/forge-backend.service"

if [ ! -f "$SERVICE_FILE" ]; then
    c_warn "Aucun service systemd existant."
    read -r -p "Installer $NEW_SERVICE -> $SERVICE_FILE ? [y/N] " reply
    if [[ "$reply" =~ ^[Yy]$ ]]; then
        sudo cp "$NEW_SERVICE" "$SERVICE_FILE"
        sudo systemctl daemon-reload
        sudo systemctl enable "$SERVICE_NAME"
        c_ok "Service installé et activé."
    else
        c_warn "Service non installé — démarrez le backend manuellement."
    fi
elif ! diff -q "$NEW_SERVICE" "$SERVICE_FILE" >/dev/null 2>&1; then
    c_warn "Le service installé diffère du gabarit du dépôt :"
    diff -u "$SERVICE_FILE" "$NEW_SERVICE" || true
    read -r -p "Remplacer le service existant par celui du dépôt ? [y/N] " reply
    if [[ "$reply" =~ ^[Yy]$ ]]; then
        sudo cp "$NEW_SERVICE" "$SERVICE_FILE"
        sudo systemctl daemon-reload
        c_ok "Service mis à jour."
    else
        c_warn "Service existant conservé tel quel."
    fi
else
    c_ok "Service systemd déjà à jour."
fi

# ---------------------------------------------------------------------------
# 5. Redémarrage du backend
# ---------------------------------------------------------------------------
read -r -p "Redémarrer ${SERVICE_NAME} maintenant ? [Y/n] " reply
if [[ ! "$reply" =~ ^[Nn]$ ]]; then
    sudo systemctl restart "$SERVICE_NAME"
    sleep 1
    sudo systemctl --no-pager status "$SERVICE_NAME" | head -5
    c_ok "Backend redémarré."
else
    c_warn "Redémarrage laissé de côté — pensez-y avant de tester."
fi

# ---------------------------------------------------------------------------
# 6. Rappel Nginx (jamais automatisé ici)
# ---------------------------------------------------------------------------
echo ""
c_info "Nginx n'est PAS touché par ce script."
c_info "Si la config a changé, comparez avant de l'appliquer :"
c_info "  diff deploy/nginx-forge.conf /etc/nginx/sites-available/forge"
c_info "Puis, seulement si le diff est voulu :"
c_info "  sudo nginx -t && sudo systemctl reload nginx"
echo ""
c_ok "Terminé."
