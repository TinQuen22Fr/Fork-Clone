#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# fix-previews.sh — remet TOUT le systeme de preview d'aplomb, en une commande.
#
# Corrige la cause des « 502 Bad Gateway » sur *.preview.<domaine> :
# Nginx proxifiait vers 127.0.0.1:<port du projet> alors qu'aucune application
# n'y ecoutait. La Forge sait maintenant demarrer elle-meme le serveur de dev de
# chaque projet (npm run dev / http.server / uvicorn) sur son port dedie.
#
# Ce script, dans l'ordre :
#   1. verifie l'installation et remet le depot propre si besoin
#   2. installe Node/npm s'ils manquent
#   3. cree les dossiers inscriptibles des previews (systemd ProtectHome/strict)
#   4. pose la regle sudoers NOPASSWD de regeneration de la map Nginx
#   5. complete backend/.env avec les nouvelles variables
#   6. corrige l'unite systemd (1 seul worker uvicorn, workspace inscriptible)
#   7. met a jour les dependances Python et rebuild le frontend
#   8. redemarre le backend et verifie /api/health
#   9. regenere le vhost + la map Nginx et recharge Nginx
#
# Usage :
#   sudo bash deploy/fix-previews.sh                 # tout
#   sudo bash deploy/fix-previews.sh --reset-code    # + git reset --hard origin
#   sudo bash deploy/fix-previews.sh --no-build      # saute le build frontend
#   sudo bash deploy/fix-previews.sh --no-nginx      # ne touche pas a Nginx
#
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE="${SERVICE:-forge-backend}"
UNIT="/etc/systemd/system/$SERVICE.service"
VENV_DIR="${VENV_DIR:-$APP_DIR/backend/venv}"
DO_RESET=0
DO_BUILD=1
DO_NGINX=1

while [ $# -gt 0 ]; do
  case "$1" in
    --reset-code) DO_RESET=1 ;;
    --no-build)   DO_BUILD=0 ;;
    --no-nginx)   DO_NGINX=0 ;;
    -h|--help)    sed -n '1,30p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1"; exit 1 ;;
  esac
  shift
done

c_ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
c_step() { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
c_warn() { printf '\033[1;33m  /!\\\033[0m %s\n' "$*"; }
die()    { printf '\033[1;31mERREUR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "a lancer en root : sudo bash deploy/fix-previews.sh"
[ -f "$APP_DIR/backend/server.py" ] || die "$APP_DIR ne ressemble pas a la Forge"
cd "$APP_DIR"
STAMP="$(date +%F-%H%M%S)"

SERVICE_USER="$(awk -F= '/^User=/{print $2; exit}' "$UNIT" 2>/dev/null || true)"
[ -n "${SERVICE_USER:-}" ] || SERVICE_USER="$(stat -c %U "$APP_DIR")"
c_ok "installation : $APP_DIR (service $SERVICE, utilisateur $SERVICE_USER)"

# ---------------------------------------------------------------------------
# 1. Depot propre
# ---------------------------------------------------------------------------
c_step "Code source"
if [ -d .git ]; then
  BRANCH="$(git -c safe.directory=* rev-parse --abbrev-ref HEAD)"
  if [ -n "$(git -c safe.directory=* status --porcelain -- . ':!backend/.env')" ]; then
    git -c safe.directory=* stash push -u -m "fix-previews-$STAMP" >/dev/null || true
    c_warn "modifications locales mises de cote (git stash list / git stash pop)"
  fi
  git -c safe.directory=* fetch --prune origin
  if [ "$DO_RESET" -eq 1 ]; then
    git -c safe.directory=* reset --hard "origin/$BRANCH"
    c_ok "branche $BRANCH remise a l'identique de origin"
  else
    git -c safe.directory=* pull --ff-only origin "$BRANCH" || \
      c_warn "pull impossible — relance avec --reset-code si le depot a divergé"
  fi
  c_ok "branche $BRANCH @ $(git -c safe.directory=* rev-parse --short HEAD)"
else
  c_warn "pas de depot git ici — etape sautee"
fi
[ -f backend/preview_runtime.py ] || die \
  "backend/preview_runtime.py absent : le code n'est pas a jour (git pull)"

# ---------------------------------------------------------------------------
# 2. Node / npm (necessaires aux projets React/Vite/Next)
# ---------------------------------------------------------------------------
c_step "Node.js et npm"
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  c_ok "node $(node -v) / npm $(npm -v)"
else
  c_warn "node ou npm absent — installation via apt"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs npm >/dev/null
  command -v npm >/dev/null 2>&1 || die "installation de npm echouee"
  c_ok "node $(node -v) / npm $(npm -v) installes"
fi

# ---------------------------------------------------------------------------
# 3. Dossiers inscriptibles des previews
# ---------------------------------------------------------------------------
c_step "Dossiers de travail des previews"
mkdir -p "$APP_DIR/workspace/.forge-preview/home" \
         "$APP_DIR/workspace/.forge-preview/npm-cache" \
         "$APP_DIR/workspace/.forge-preview/cache" \
         "$APP_DIR/.playwright" \
         "$APP_DIR/backend/static/screenshots"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/workspace" \
  "$APP_DIR/.playwright" "$APP_DIR/backend/static/screenshots" 2>/dev/null || \
  c_warn "chown partiel (utilisateur $SERVICE_USER inconnu ?)"
c_ok "workspace/.forge-preview/ pret (HOME + cache npm des projets)"

# ---------------------------------------------------------------------------
# 4. sudoers : regeneration de la map Nginx sans mot de passe
# ---------------------------------------------------------------------------
c_step "Regle sudoers de la map Nginx"
SUDO_FILE="/etc/sudoers.d/forge-preview"
SUDO_LINE="$SERVICE_USER ALL=(root) NOPASSWD: /usr/bin/bash $APP_DIR/deploy/setup-preview-domain.sh --map-only"
printf '# Genere par deploy/fix-previews.sh\n%s\n' "$SUDO_LINE" > "$SUDO_FILE.new"
chmod 0440 "$SUDO_FILE.new"
if visudo -cqf "$SUDO_FILE.new" 2>/dev/null; then
  mv "$SUDO_FILE.new" "$SUDO_FILE"
  c_ok "$SUDO_FILE"
else
  rm -f "$SUDO_FILE.new"
  c_warn "regle sudoers invalide, ignoree — ajoute-la a la main :"
  echo "      $SUDO_LINE"
fi

# ---------------------------------------------------------------------------
# 5. Nouvelles variables .env
# ---------------------------------------------------------------------------
c_step "Configuration backend/.env"
if [ -f backend/.env ]; then
  cp backend/.env "backend/.env.bak-$STAMP"
  MISSING="$(
    LC_ALL=C comm -23 --nocheck-order \
      <(grep -oE '^[A-Z0-9_]+=' backend/env.example | LC_ALL=C sort -u) \
      <(grep -oE '^[A-Z0-9_]+=' backend/.env        | LC_ALL=C sort -u)
  )"
  if [ -n "$MISSING" ]; then
    {
      printf '\n# --- Ajoute par fix-previews.sh le %s ---\n' "$STAMP"
      for key in $MISSING; do grep -m1 "^$key" backend/env.example; done
    } >> backend/.env
    echo "$MISSING" | sed 's/=$//' | sed 's/^/  + /'
  else
    c_ok "aucune nouvelle variable"
  fi
  # La commande sudo doit pointer sur CE repertoire d'installation.
  python3 - "$APP_DIR/backend/.env" "$APP_DIR" <<'PY'
import re, sys
env, app = sys.argv[1], sys.argv[2]
want = f"sudo /usr/bin/bash {app}/deploy/setup-preview-domain.sh --map-only"
text = open(env).read()
if not text.endswith("\n"):
    text += "\n"
if re.search(r"(?m)^PREVIEW_MAP_REFRESH_CMD=", text):
    text = re.sub(r"(?m)^PREVIEW_MAP_REFRESH_CMD=.*$",
                  f"PREVIEW_MAP_REFRESH_CMD={want}", text)
else:
    text += f"PREVIEW_MAP_REFRESH_CMD={want}\n"
open(env, "w").write(text)
print(f"  ok PREVIEW_MAP_REFRESH_CMD = {want}")
PY
  chown "$SERVICE_USER":"$SERVICE_USER" backend/.env 2>/dev/null || true
  chmod 600 backend/.env
else
  c_warn "backend/.env absent — lance install.sh d'abord"
fi

# ---------------------------------------------------------------------------
# 6. Unite systemd : 1 seul worker + workspace inscriptible
# ---------------------------------------------------------------------------
c_step "Unite systemd $SERVICE"
if [ -f "$UNIT" ]; then
  cp "$UNIT" "$UNIT.bak-$STAMP"
  python3 - "$UNIT" "$APP_DIR" <<'PY'
import re, sys
path, app = sys.argv[1], sys.argv[2]
text = open(path).read()
changed = []

# Plusieurs workers uvicorn = plusieurs gestionnaires de preview concurrents.
new, n = re.subn(r"--workers\s+\d+", "--workers 1", text)
if n and new != text:
    text, _ = new, changed.append("--workers 1")

ws = f"{app}/workspace"
m = re.search(r"(?m)^ReadWritePaths=(.*)$", text)
if m:
    if ws not in m.group(1).split():
        text = text[:m.start(1)] + f"{ws} " + text[m.start(1):]
        changed.append("ReadWritePaths += workspace")
else:
    text = text.replace("[Install]", f"ReadWritePaths={ws}\n\n[Install]")
    changed.append("ReadWritePaths ajoute")

# Les serveurs de dev sont des process enfants : ils doivent mourir avec le service.
if not re.search(r"(?m)^KillMode=", text):
    text = text.replace("Restart=always", "KillMode=control-group\nRestart=always")
    changed.append("KillMode=control-group")
if not re.search(r"(?m)^TimeoutStopSec=", text):
    text = text.replace("Restart=always", "TimeoutStopSec=20\nRestart=always")
    changed.append("TimeoutStopSec=20")

open(path, "w").write(text)
print("  ok unite inchangee" if not changed else "  ok " + ", ".join(changed))
PY
  systemctl daemon-reload
else
  c_warn "$UNIT absent : installe le service (install.sh) puis relance ce script"
fi

# ---------------------------------------------------------------------------
# 7. Dependances Python + build frontend
# ---------------------------------------------------------------------------
c_step "Dependances Python"
if [ -x "$VENV_DIR/bin/python" ]; then
  "$VENV_DIR/bin/pip" install -q --upgrade-strategy only-if-needed \
    -r backend/requirements.txt || c_warn "pip a signale une erreur (voir ci-dessus)"
  "$VENV_DIR/bin/python" -c "import ast;ast.parse(open('backend/server.py').read())" \
    || die "backend/server.py ne compile pas"
  "$VENV_DIR/bin/python" -c "import ast;ast.parse(open('backend/preview_runtime.py').read())" \
    || die "backend/preview_runtime.py ne compile pas"
  c_ok "backend a jour et compilable"
else
  die "venv introuvable ($VENV_DIR) — lance install.sh"
fi

if [ "$DO_BUILD" -eq 1 ] && [ -d frontend ]; then
  c_step "Build frontend"
  (cd frontend && npm install --no-audit --no-fund >/dev/null && npm run build >/dev/null)
  [ -f frontend/build/index.html ] || die "build frontend echoue"
  chown -R "$SERVICE_USER":"$SERVICE_USER" frontend/build 2>/dev/null || true
  c_ok "frontend/build regenere"
fi

# ---------------------------------------------------------------------------
# 8. Redemarrage du backend
# ---------------------------------------------------------------------------
c_step "Redemarrage du backend"
systemctl restart "$SERVICE"
PORT="$(awk -F'--port ' '/ExecStart/{split($2,a," ");print a[1];exit}' "$UNIT" 2>/dev/null)"
PORT="${PORT:-8001}"
CODE=000
for _ in $(seq 1 15); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/health" || echo 000)"
  [ "$CODE" = "200" ] && break
  sleep 1
done
[ "$CODE" = "200" ] && c_ok "API en ligne sur 127.0.0.1:$PORT" || {
  journalctl -u "$SERVICE" -n 30 --no-pager
  die "l'API ne repond pas ($CODE) — journal ci-dessus"
}

# ---------------------------------------------------------------------------
# 9. Nginx : vhost wildcard + map
# ---------------------------------------------------------------------------
if [ "$DO_NGINX" -eq 1 ]; then
  c_step "Nginx (vhost wildcard + map projet -> port)"
  APP_DIR="$APP_DIR" bash "$APP_DIR/deploy/setup-preview-domain.sh" || \
    c_warn "setup-preview-domain.sh a signale une erreur (voir ci-dessus)"
fi

c_step "Previews reparees"
cat <<EOF
  Ce qui change : la Forge demarre elle-meme l'app de chaque projet sur son
  port dedie. Plus de 502 tant que la preview est demarree.

  Depuis l'interface :
    - bouton PREVIEW (en-tete d'une conversation) : 1er clic = demarrage, le
      point de couleur indique l'etat (gris arretee / jaune demarrage /
      cyan en ligne / rouge erreur), puis l'onglet s'ouvre tout seul.
    - icone parchemin : journal du serveur de dev (pour voir une erreur npm).
    - carre : arret (libere RAM et port), fleche circulaire : redemarrage.
    - sur le Hub, chaque carte de projet a le meme bouton et le meme etat.

  En ligne de commande (diagnostic) :
    systemctl status $SERVICE
    journalctl -u $SERVICE -n 50 --no-pager
    tail -f $APP_DIR/workspace/.forge-preview/<projet>.log
    cat /etc/nginx/forge-preview-ports.map
    ss -ltnp | grep -E '809[0-9]|81[0-8][0-9]'

  Rappel : un projet doit contenir une app demarrable (package.json avec un
  script dev/start, un index.html, ou app.py/main.py). Sinon le journal
  l'indique clairement au lieu d'un 502 muet.
EOF
