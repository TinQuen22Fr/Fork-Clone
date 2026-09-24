#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# ensure-rwpaths.sh — SOURCE UNIQUE DE VERITE de la conformite de l'unite
# systemd du backend. Tout script qui installe, regenere ou corrige l'unite
# (install.sh, upgrade.sh, deploy/fix-previews.sh) passe par ici, pour que les
# bugs suivants ne puissent plus revenir par regression :
#
#   1. ReadWritePaths incomplet  -> map nginx non ecrite -> PREVIEW en 503
#      (cf. deploy/KNOWN_ISSUE_preview_map_readonly.md)
#   2. PATH limite au venv       -> npm/node/python3 introuvables -> aucune
#                                   preview de projet Node ne peut demarrer
#   3. NoNewPrivileges=true      -> `sudo` IMPOSSIBLE depuis le service, donc
#                                   la regeneration automatique de la map nginx
#                                   echoue quoi qu'il arrive
#   4. --workers 2               -> plusieurs gestionnaires de preview
#                                   concurrents (start/stop erratiques)
#   5. KillMode par defaut       -> serveurs de dev orphelins gardant les ports
#
# Le patch est IDEMPOTENT et ADDITIF : il ne supprime JAMAIS un chemin ou une
# option ajoutee localement, il complete. Une sauvegarde .bak-<date> est creee
# uniquement si le fichier change reellement.
#
# Usage :
#   bash deploy/ensure-rwpaths.sh /etc/systemd/system/forge-backend.service /var/www/forge
#   REQUIRED_EXTRA="/srv/truc" bash deploy/ensure-rwpaths.sh <unite> <app_dir>
#   bash deploy/ensure-rwpaths.sh --print /var/www/forge     # liste attendue
#   bash deploy/ensure-rwpaths.sh --check <unite> <app_dir>  # audit sans ecrire
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

MODE=patch
case "${1:-}" in
  --print) required_list "${2:-/var/www/forge}" | tr '\n' ' '; echo; exit 0 ;;
  --check) MODE=check; shift ;;
esac

UNIT="${1:?unite systemd attendue}"
APP_DIR="${2:-/var/www/forge}"
[ -f "$UNIT" ] || { echo "  /!\\ $UNIT absent — rien a patcher"; exit 0; }

REQUIRED="$(required_list "$APP_DIR" | tr '\n' ' ')"

# Un chemin de ReadWritePaths inexistant fait echouer systemd en 226/NAMESPACE :
# on cree ceux qui manquent (sauf /dev/* et /run, geres par le systeme).
if [ "$MODE" = patch ]; then
  for p in $REQUIRED; do
    case "$p" in
      /dev/*|/run) continue ;;
    esac
    [ -e "$p" ] || mkdir -p "$p" 2>/dev/null || true
  done
fi

python3 - "$UNIT" "$APP_DIR" "$MODE" "$REQUIRED" <<'PY'
import re, shutil, sys, time

path, app_dir, mode, required = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split()
text = original = open(path).read()
changes: list[str] = []


def covered(p: str, current: list[str]) -> bool:
    """Un chemin deja couvert par lui-meme ou par un parent present suffit."""
    for c in current:
        c = c.rstrip("/")
        if p == c or p.startswith(c + "/"):
            return True
    return False


# --- 1. ReadWritePaths -----------------------------------------------------
m = re.search(r"(?m)^ReadWritePaths=(.*)$", text)
current = m.group(1).split() if m else []
missing = [p for p in required if not covered(p, current)]
if missing:
    line = "ReadWritePaths=" + " ".join(current + missing)
    if m:
        text = text[: m.start()] + line + text[m.end():]
    else:
        text = text.replace(
            "[Install]",
            "# Chemins autorises en ecriture (cf. "
            "deploy/KNOWN_ISSUE_preview_map_readonly.md) — ne rien retirer.\n"
            + line + "\n\n[Install]",
            1,
        )
    changes.append("ReadWritePaths += " + " ".join(missing))

# --- 2. PATH des process enfants (npm / node / python3) --------------------
need_dirs = ["/usr/local/bin", "/usr/bin", "/bin"]
mp = re.search(r'(?m)^Environment="PATH=([^"]*)"\s*$', text)
if mp:
    dirs = [d for d in mp.group(1).split(":") if d]
    add = [d for d in need_dirs if d not in dirs]
    if add:
        newline = 'Environment="PATH=' + ":".join(dirs + add) + '"'
        text = text[: mp.start()] + newline + text[mp.end():]
        changes.append("PATH += " + ":".join(add))
else:
    venv = f"{app_dir}/backend/venv/bin"
    text = re.sub(
        r"(?m)^(EnvironmentFile=.*)$",
        'Environment="PATH=' + ":".join([venv] + need_dirs) + '"\n\\1',
        text,
        count=1,
    )
    changes.append("Environment PATH ajoute (venv + binaires systeme)")

# --- 3. NoNewPrivileges : bloque sudo, donc le refresh de la map -----------
mn = re.search(r"(?mi)^NoNewPrivileges=(true|yes|1)\s*$", text)
if mn:
    text = (
        text[: mn.start()]
        + "# NoNewPrivileges doit rester false : le backend appelle\n"
        + "# `sudo deploy/setup-preview-domain.sh --map-only` (regeneration de la\n"
        + "# map nginx des previews). Avec true, sudo est refuse -> previews en 503.\n"
        + "NoNewPrivileges=false"
        + text[mn.end():]
    )
    changes.append("NoNewPrivileges=true -> false (sinon sudo impossible)")

# --- 4. Un seul worker uvicorn --------------------------------------------
new, n = re.subn(r"--workers\s+\d+", "--workers 1", text)
if n and new != text:
    text = new
    changes.append("--workers 1 (un seul gestionnaire de preview)")

# --- 5. Les serveurs de dev doivent mourir avec le service -----------------
extra = []
if not re.search(r"(?m)^KillMode=", text):
    extra.append("KillMode=control-group")
if not re.search(r"(?m)^TimeoutStopSec=", text):
    extra.append("TimeoutStopSec=20")
if extra:
    block = "\n".join(extra)
    if "[Install]" in text:
        text = text.replace("[Install]", block + "\n\n[Install]", 1)
    else:
        text = text.rstrip("\n") + "\n" + block + "\n"
    changes.append(" + ".join(extra))

# --- resultat --------------------------------------------------------------
if not changes:
    print("  ok unite conforme (aucune modification necessaire)")
    raise SystemExit(0)

if mode == "check":
    print("  /!\\ unite NON conforme :")
    for c in changes:
        print(f"      - {c}")
    raise SystemExit(1)

shutil.copy(path, f"{path}.bak-{time.strftime('%Y-%m-%d-%H%M%S')}")
open(path, "w").write(text)
for c in changes:
    print(f"  ok {c}")
print(f"  ok sauvegarde : {path}.bak-*")
PY
