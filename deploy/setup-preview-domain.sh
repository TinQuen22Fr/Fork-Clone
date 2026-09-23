#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# setup-preview-domain.sh — reverse proxy Nginx wildcard pour les previews de
# projet : https://<projet>.preview.quentin-astro.fr -> 127.0.0.1:<port du projet>
#
# Le DNS wildcard *.preview.<domaine> pointe deja sur la machine.
# Les ports sont attribues automatiquement par la Forge (collection Mongo
# `projects`, champ preview_port, a partir de 8090) : ce script se contente de
# generer la table de correspondance Nginx.
#
#   sudo bash deploy/setup-preview-domain.sh            # vhost + map + reload
#   sudo bash deploy/setup-preview-domain.sh --map-only # regenere juste la map
#   sudo bash deploy/setup-preview-domain.sh --ssl      # + certificats certbot
#
set -euo pipefail

APP_DIR="${APP_DIR:-/var/www/forge}"
PREVIEW_SUFFIX="${PREVIEW_SUFFIX:-preview.quentin-astro.fr}"
MAP_FILE="${MAP_FILE:-/etc/nginx/forge-preview-ports.map}"
SITE_FILE="${SITE_FILE:-/etc/nginx/sites-available/forge-preview-wildcard}"
SITE_LINK="/etc/nginx/sites-enabled/forge-preview-wildcard"
CERT_EMAIL="${CERT_EMAIL:-}"
DO_SSL=0
MAP_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --ssl)      DO_SSL=1 ;;
    --map-only) MAP_ONLY=1 ;;
    -h|--help)  sed -n '1,18p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1"; exit 1 ;;
  esac
  shift
done

c_ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
c_step() { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
c_warn() { printf '\033[1;33m  /!\\\033[0m %s\n' "$*"; }
die()    { printf '\033[1;31mERREUR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "a lancer en root : sudo bash deploy/setup-preview-domain.sh"
command -v nginx >/dev/null 2>&1 || die "nginx introuvable"

# ---------------------------------------------------------------------------
# 1. Table projet -> port, lue directement dans MongoDB
# ---------------------------------------------------------------------------
c_step "Table de correspondance projet -> port"
PY="$APP_DIR/backend/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

"$PY" - "$APP_DIR/backend/.env" "$MAP_FILE" <<'PYEOF' || die "generation de la map impossible"
import os, sys, re
env_path, map_path = sys.argv[1], sys.argv[2]

conf = {}
if os.path.isfile(env_path):
    for line in open(env_path):
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            conf[m.group(1)] = m.group(2).strip().strip('"').strip("'")

lines = []
try:
    from pymongo import MongoClient

    cli = MongoClient(conf.get("MONGO_URL", "mongodb://localhost:27017"),
                      serverSelectionTimeoutMS=4000)
    db = cli[conf.get("DB_NAME", "forge")]
    for doc in db.projects.find({}, {"name": 1, "preview_port": 1}):
        name, port = doc.get("name"), doc.get("preview_port")
        if name and port and re.fullmatch(r"[A-Za-z0-9._-]+", str(name)):
            lines.append(f"    {name} {port};")
except Exception as e:  # noqa: BLE001
    print(f"  /!\\ Mongo illisible ({e}) : map vide, previews en 502", file=sys.stderr)

with open(map_path, "w") as fh:
    fh.write("# Genere par deploy/setup-preview-domain.sh — ne pas editer a la main.\n")
    fh.write("\n".join(lines) + ("\n" if lines else ""))
print(f"  ok {len(lines)} projet(s) mappe(s) dans {map_path}")
PYEOF

if [ "$MAP_ONLY" -eq 1 ]; then
  nginx -t && systemctl reload nginx && c_ok "map rechargee"
  exit 0
fi

# ---------------------------------------------------------------------------
# 2. Vhost wildcard
# ---------------------------------------------------------------------------
c_step "Vhost $SITE_FILE"
CERT_DIR="/etc/letsencrypt/live/$PREVIEW_SUFFIX"
if [ -f "$CERT_DIR/fullchain.pem" ]; then
  SSL_BLOCK="    ssl_certificate     $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;"
  HAS_CERT=1
else
  # Pas encore de certificat : on genere d'abord le vhost HTTP seul, certbot
  # ajoutera le bloc TLS (ou relance ce script apres --ssl).
  SSL_BLOCK=""
  HAS_CERT=0
  c_warn "aucun certificat pour *.$PREVIEW_SUFFIX — vhost HTTP uniquement"
fi

cat > "$SITE_FILE" <<NGINXEOF
# Claude Unchained Forge — previews de projet (genere, ne pas editer a la main).
# https://<projet>.$PREVIEW_SUFFIX -> 127.0.0.1:<preview_port du projet>

map \$project \$forge_preview_port {
    default 0;
    include $MAP_FILE;
}

server {
    listen 80;
    listen [::]:80;
    server_name ~^(?<project>[a-z0-9._-]+)\\.${PREVIEW_SUFFIX//./\\.}\$;

    location /.well-known/acme-challenge/ { root /var/www/html; }
$( [ "$HAS_CERT" -eq 1 ] && echo '    location / { return 301 https://$host$request_uri; }' || cat <<'HTTPONLY'
    location / {
        if ($forge_preview_port = 0) { return 503; }
        proxy_pass http://127.0.0.1:$forge_preview_port;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
    }
HTTPONLY
)
}
NGINXEOF

if [ "$HAS_CERT" -eq 1 ]; then
cat >> "$SITE_FILE" <<NGINXEOF

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ~^(?<project>[a-z0-9._-]+)\\.${PREVIEW_SUFFIX//./\\.}\$;

$SSL_BLOCK

    add_header X-Robots-Tag "noindex, nofollow" always;
    add_header X-Content-Type-Options "nosniff" always;
    client_max_body_size 64m;

    access_log /var/log/nginx/forge-preview.access.log;
    error_log  /var/log/nginx/forge-preview.error.log;

    location / {
        # Projet inconnu de la map : 503 explicite plutot qu'un 502 obscur.
        if (\$forge_preview_port = 0) { return 503; }

        proxy_pass http://127.0.0.1:\$forge_preview_port;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSockets + SSE : upgrade autorise, aucun tampon.
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_cache off;
        proxy_request_buffering off;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
    }
}
NGINXEOF
fi

[ -L "$SITE_LINK" ] || ln -s "$SITE_FILE" "$SITE_LINK"
c_ok "vhost genere et active"

# ---------------------------------------------------------------------------
# 3. Certificat SSL
# ---------------------------------------------------------------------------
if [ "$DO_SSL" -eq 1 ] && [ "$HAS_CERT" -eq 0 ]; then
  c_step "Certificat Let's Encrypt"
  command -v certbot >/dev/null 2>&1 || die "certbot absent : apt install certbot python3-certbot-nginx"
  EMAIL_ARG=""
  [ -n "$CERT_EMAIL" ] && EMAIL_ARG="-m $CERT_EMAIL"
  c_warn "un certificat wildcard demande une validation DNS-01 :"
  echo "      sudo certbot certonly --manual --preferred-challenges dns \\"
  echo "        -d '*.$PREVIEW_SUFFIX' -d '$PREVIEW_SUFFIX' $EMAIL_ARG"
  echo ""
  c_warn "alternative immediate (HTTP-01, un certificat par projet) :"
  DOMS="$(awk '{print $1}' "$MAP_FILE" 2>/dev/null | grep -vE '^#|^$' | sed "s|^|-d |;s|\$|.$PREVIEW_SUFFIX|" | tr '\n' ' ')"
  if [ -n "$DOMS" ]; then
    echo "      sudo certbot --nginx $DOMS --redirect $EMAIL_ARG"
  else
    echo "      (aucun projet dans la map pour l'instant)"
  fi
  echo ""
  c_warn "relance ce script apres obtention du certificat pour activer le bloc TLS."
fi

# ---------------------------------------------------------------------------
# 4. Validation + rechargement
# ---------------------------------------------------------------------------
c_step "Validation Nginx"
nginx -t || die "configuration Nginx invalide — rien n'a ete recharge"
systemctl reload nginx
c_ok "nginx recharge"

c_step "Previews actives"
cat <<EOF
  Domaine    : https://<projet>.$PREVIEW_SUFFIX
  Map        : $MAP_FILE  (regenere avec --map-only)
  Ports      : attribues par la Forge des la creation du projet (8090+)
  Astuce     : apres creation d'un nouveau projet, lance
               sudo bash deploy/setup-preview-domain.sh --map-only
EOF
