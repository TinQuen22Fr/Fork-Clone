#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# ensure-rwpaths.sh — SOURCE UNIQUE DE VERITE des chemins que le backend doit
# pouvoir ecrire malgre ProtectSystem=strict.
#
# Tout script qui installe, regenere ou corrige une unite systemd de la Forge
# passe par ici. Objectif : le bug « PREVIEW en 503 » (map nginx non ecrite car
# /etc/nginx monte en lecture seule) ne peut plus revenir par regression.
# Voir deploy/KNOWN_ISSUE_preview_map_readonly.md
#
# Le patch est IDEMPOTENT et ADDITIF : il ne supprime jamais un chemin deja
# present dans l'unite installee (durcissement local preserve), il complete.
#
# Usage :
#   bash deploy/ensure-rwpaths.sh /etc/systemd/system/forge-backend.service /var/www/forge
#   REQUIRED_EXTRA="/srv/truc" bash deploy/ensure-rwpaths.sh <unite> <app_dir>
#   bash deploy/ensure-rwpaths.sh --print /var/www/forge     # liste attendue
#
set -euo pipefail

# Chemins systeme indispensables (identiques pour toutes les installations).
SYSTEM_RWPATHS=(/dev/shm /etc/nginx /run /var/log/nginx)
# Chemins relatifs a l'installation.
APP_RWPATHS=(workspace .playwright backend/static/screenshots)

required_list() {
  local app="$1" p
  for p in "${APP_RWPATHS[@]}"; do printf '%s\n' "$app/$p"; done
  for p in "${SYSTEM_RWPATHS[@]}"; do printf '%s\n' "$p"; done
  for p in ${REQUIRED_EXTRA:-}; do printf '%s\n' "$p"; done
}

if [ "${1:-}" = "--print" ]; then
  required_list "${2:-/var/www/forge}" | tr '\n' ' '
  echo
  exit 0
fi

UNIT="${1:?unite systemd attendue}"
APP_DIR="${2:-/var/www/forge}"
[ -f "$UNIT" ] || { echo "  /!\\ $UNIT absent — rien a patcher"; exit 0; }

REQUIRED="$(required_list "$APP_DIR" | tr '\n' ' ')"

# Un chemin de ReadWritePaths inexistant fait echouer systemd en 226/NAMESPACE :
# on cree ceux qui manquent (sauf /dev/* et /run, geres par le systeme).
for p in $REQUIRED; do
  case "$p" in
    /dev/*|/run) continue ;;
  esac
  [ -e "$p" ] || mkdir -p "$p" 2>/dev/null || true
done

python3 - "$UNIT" "$REQUIRED" <<'PY'
import re, shutil, sys, time
path, required = sys.argv[1], sys.argv[2].split()

text = open(path).read()
m = re.search(r"(?m)^ReadWritePaths=(.*)$", text)
current = m.group(1).split() if m else []
missing = [p for p in required if p not in current]

if not missing:
    print("  ok ReadWritePaths complet (aucun chemin manquant)")
    raise SystemExit(0)

shutil.copy(path, f"{path}.bak-{time.strftime('%Y-%m-%d-%H%M%S')}")
merged = current + missing
line = "ReadWritePaths=" + " ".join(merged)
if m:
    text = text[: m.start()] + line + text[m.end():]
else:
    block = (
        "# Chemins autorises en ecriture (voir "
        "deploy/KNOWN_ISSUE_preview_map_readonly.md) — ne rien retirer.\n"
        + line + "\n\n[Install]"
    )
    text = text.replace("[Install]", block, 1)
open(path, "w").write(text)
print("  ok ReadWritePaths complete : " + " ".join(missing))
PY
