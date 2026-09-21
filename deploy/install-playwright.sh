#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# Installe Chromium headless (Playwright) + ses dependances systeme.
# Cible : Ubuntu 26.04 LTS (paquets "t64", ex. libasound2t64).
# Script idempotent : relançable sans risque.
#
# Usage (sur le serveur, en root ou avec sudo) :
#     sudo bash deploy/install-playwright.sh
#
# Variables surchargeables :
#     APP_DIR=/var/www/forge                 racine de l'application
#     VENV_DIR=$APP_DIR/backend/venv         virtualenv du backend
#     PLAYWRIGHT_BROWSERS_PATH=$APP_DIR/.playwright
#     SERVICE_USER=www-data                  proprietaire du cache navigateur
#
set -euo pipefail

APP_DIR="${APP_DIR:-/var/www/forge}"
VENV_DIR="${VENV_DIR:-$APP_DIR/backend/venv}"
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$APP_DIR/.playwright}"
SERVICE_USER="${SERVICE_USER:-www-data}"
export PLAYWRIGHT_BROWSERS_PATH

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m/!\\\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERREUR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "a lancer en root (sudo bash deploy/install-playwright.sh)"

# ----------------------------------------------------------------------------
# 1. Controle de l'environnement
# ----------------------------------------------------------------------------
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  log "Systeme detecte : ${PRETTY_NAME:-inconnu}"
  case "${ID:-}" in
    ubuntu) : ;;
    *) warn "script calibre pour Ubuntu 26.04 LTS — poursuite quand meme" ;;
  esac
else
  warn "/etc/os-release illisible, poursuite a l'aveugle"
fi

# ----------------------------------------------------------------------------
# 2. Dependances systeme de Chromium headless
#    Ubuntu 26.04 : transition time_t 64 bits -> suffixe "t64".
#    On teste la disponibilite de chaque paquet pour rester compatible.
# ----------------------------------------------------------------------------
log "Mise a jour de l'index apt"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

# Noms "t64" explicites (Ubuntu 26.04) : libasound2 et consorts ne sont plus
# que des paquets virtuels sans candidat d'installation.
CANDIDATES=(
  ca-certificates fonts-liberation fonts-unifont
  libasound2t64
  libatk-bridge2.0-0t64
  libatk1.0-0t64
  libatspi2.0-0t64
  libcairo2
  libcups2t64
  libdbus-1-3 libdrm2 libexpat1 libgbm1 libglib2.0-0t64
  libnspr4 libnss3 libpango-1.0-0 libpangocairo-1.0-0
  libudev1 libvulkan1 libx11-6 libxcb1 libxcomposite1 libxdamage1
  libxext6 libxfixes3 libxkbcommon0 libxrandr2 libxshmfence1
  xdg-utils
)

# Repli pour les distributions plus anciennes (nom t64 inexistant).
declare -A LEGACY=(
  [libasound2t64]=libasound2
  [libatk-bridge2.0-0t64]=libatk-bridge2.0-0
  [libatk1.0-0t64]=libatk1.0-0
  [libatspi2.0-0t64]=libatspi2.0-0
  [libcups2t64]=libcups2
  [libglib2.0-0t64]=libglib2.0-0
)

# Un paquet virtuel passe apt-cache show mais n'a pas de candidat : on teste
# explicitement la ligne "Candidate:" d'apt-cache policy.
has_candidate() {
  local cand
  cand="$(apt-cache policy "$1" 2>/dev/null | awk '/Candidate:/ {print $2; exit}')"
  [ -n "$cand" ] && [ "$cand" != "(none)" ]
}

TO_INSTALL=()
for pkg in "${CANDIDATES[@]}"; do
  if has_candidate "$pkg"; then
    TO_INSTALL+=("$pkg")
  elif [ -n "${LEGACY[$pkg]:-}" ] && has_candidate "${LEGACY[$pkg]}"; then
    warn "$pkg indisponible, repli sur ${LEGACY[$pkg]}"
    TO_INSTALL+=("${LEGACY[$pkg]}")
  else
    warn "$pkg introuvable dans les depots, ignore"
  fi
done

log "Installation de ${#TO_INSTALL[@]} paquets systeme"
apt-get install -y --no-install-recommends "${TO_INSTALL[@]}"

# ----------------------------------------------------------------------------
# 3. Module Python playwright dans le venv du backend
# ----------------------------------------------------------------------------
PY="$VENV_DIR/bin/python"
if [ ! -x "$PY" ]; then
  warn "venv absent a $VENV_DIR, bascule sur python3 systeme"
  PY="$(command -v python3)" || die "python3 introuvable"
fi
log "Interpreteur : $PY"

if ! "$PY" -c "import playwright" >/dev/null 2>&1; then
  log "Installation du module playwright"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install "playwright>=1.50.0"
else
  log "Module playwright deja present"
fi

# ----------------------------------------------------------------------------
# 4. Navigateur Chromium dans un cache partage, lisible par le service
# ----------------------------------------------------------------------------
log "Cache navigateur : $PLAYWRIGHT_BROWSERS_PATH"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"

log "Telechargement de Chromium headless (~150 Mo, patience sur Atom)"
"$PY" -m playwright install chromium

if id -u "$SERVICE_USER" >/dev/null 2>&1; then
  log "Attribution du cache a $SERVICE_USER"
  chown -R "$SERVICE_USER":"$SERVICE_USER" "$PLAYWRIGHT_BROWSERS_PATH"
else
  warn "utilisateur $SERVICE_USER inconnu, droits laisses tels quels"
fi
chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"

# ----------------------------------------------------------------------------
# 5. Verification : lancement headless reel + capture jetable
# ----------------------------------------------------------------------------
log "Verification du lancement headless"
if sudo -u "$SERVICE_USER" -H \
     env PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
     "$PY" - <<'PYCHECK'
import sys, tempfile, pathlib
from playwright.sync_api import sync_playwright

out = pathlib.Path(tempfile.gettempdir()) / "forge_playwright_check.png"
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
    p = b.new_page(viewport={"width": 800, "height": 600})
    p.set_content("<h1>forge ok</h1>")
    p.screenshot(path=str(out))
    b.close()
print("capture test ecrite:", out, out.stat().st_size, "octets")
sys.exit(0)
PYCHECK
then
  log "Chromium headless operationnel"
else
  die "le lancement headless a echoue — relis les messages apt ci-dessus"
fi

# ----------------------------------------------------------------------------
# 6. Rappels de configuration
# ----------------------------------------------------------------------------
cat <<EOF

------------------------------------------------------------------------
Installation terminee.

1) Ajoute dans $APP_DIR/backend/.env :

     ENABLE_SCREENSHOT=true
     PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH

2) Verifie l'unite systemd (deploy/forge-backend.service) :

     Environment=PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH

3) Recharge le service :

     sudo systemctl daemon-reload && sudo systemctl restart forge-backend

4) Test depuis l'interface :

     "Capture https://example.com et affiche l'image dans ta reponse."
------------------------------------------------------------------------
EOF
