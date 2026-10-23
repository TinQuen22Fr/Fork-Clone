#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d12.sh — Étape D12 : bascule et retour arrière (feature flag PREVIEW_DRIVER).
# Statique sans privilège (défaut natif, bind par driver) ; bout-en-bout réel en root
# (un projet pilote bascule native -> docker -> native sans perdre le workspace).
# sudo bash deploy/docker/verify-d12.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"

DOCKER=/usr/bin/docker
WRAPPER_DST=/usr/local/bin/forge-preview
PROJECT=verifyd12
WSROOT=${FORGE_PREVIEW_WORKSPACE_ROOTS:-/var/www/forge/workspace}
WSROOT=${WSROOT%%:*}
[ -n "$WSROOT" ] && [ -d "$WSROOT" ] || WSROOT=/var/www/forge/workspace
WS="$WSROOT/.verify-d12.$$"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1
export D12_BACKEND="$ROOTDIR/backend"

echo "== D12 : bascule et retour arrière (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

# 1. Feature flag : défaut natif, bind interne dépendant du driver, même chemin de projet.
OUT=$(python3 - "$ROOTDIR" <<'PY' 2>&1
import os, sys
sys.path.insert(0, os.environ["D12_BACKEND"])
from preview_runtime import PreviewManager

os.environ.pop("PREVIEW_DRIVER", None)
m = PreviewManager("/tmp")
print("DEF_DRIVER", m.driver)
print("DEF_BIND", m._bind())

os.environ["PREVIEW_DRIVER"] = "docker"
m = PreviewManager("/tmp")
print("DOC_DRIVER", m.driver)
print("DOC_BIND", m._bind())

os.environ["PREVIEW_DRIVER"] = "native"
m = PreviewManager("/tmp")
print("NAT_DRIVER", m.driver)
print("NAT_BIND", m._bind())

# Le chemin du projet ne dépend pas du driver (jamais de copie du workspace).
os.environ.pop("PREVIEW_DRIVER", None)
a = PreviewManager("/tmp").project_dir("p")
os.environ["PREVIEW_DRIVER"] = "docker"
b = PreviewManager("/tmp").project_dir("p")
print("SAME_DIR", "oui" if a == b else "non")
PY
) || true

printf '%s\n' "$OUT" | grep -q '^DEF_DRIVER native$' \
  && ok "défaut PREVIEW_DRIVER=native (point 1)" || ko "défaut non natif"
printf '%s\n' "$OUT" | grep -q '^DEF_BIND 127.0.0.1$' \
  && ok "bind interne natif 127.0.0.1" || ko "bind natif != 127.0.0.1"
printf '%s\n' "$OUT" | grep -q '^DOC_DRIVER docker$' \
  && ok "PREVIEW_DRIVER=docker pris en compte" || ko "driver docker non pris en compte"
printf '%s\n' "$OUT" | grep -q '^DOC_BIND 0.0.0.0$' \
  && ok "bind interne docker 0.0.0.0" || ko "bind docker != 0.0.0.0"
printf '%s\n' "$OUT" | grep -q '^NAT_DRIVER native$' \
  && ok "PREVIEW_DRIVER=native explicite" || ko "driver native explicite non pris en compte"
printf '%s\n' "$OUT" | grep -q '^NAT_BIND 127.0.0.1$' \
  && ok "bind interne native explicite 127.0.0.1" || ko "bind native explicite != 127.0.0.1"
printf '%s\n' "$OUT" | grep -q '^SAME_DIR oui$' \
  && ok "chemin du projet identique quel que soit le driver (pas de copie)" || ko "chemin du projet différent selon le driver"

echo
if [ "$ROOT" -eq 0 ]; then
  skip "bout-en-bout réel : root requis (sudo bash deploy/docker/verify-d12.sh)"
  echo
  if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
    echo "RÉSULTAT : D12 conforme."
    exit 0
  elif [ "$fail" -eq 0 ]; then
    echo "RÉSULTAT : D12 conforme (partiellement non vérifié)."
    exit 0
  else
    echo "RÉSULTAT : D12 NON conforme — corriger les lignes KO ci-dessus."
    exit 1
  fi
fi

echo
echo "== D12 : bout-en-bout réel (root requis) =="

cleanup() {
  "$DOCKER" rm -f "forge-preview-$PROJECT" >/dev/null 2>&1 || true
  rm -rf "$WS" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$WS/$PROJECT" && ok "workspace de test créé" || { ko "workspace non créé"; exit 1; }
printf '<!doctype html><title>d12</title><h1>D12</h1>\n' > "$WS/$PROJECT/index.html"
( cd "$WS/$PROJECT" && git init -q && git add -A && git -c user.email=d12@local -c user.name=d12 commit -qm init ) \
  && ok "projet pilote initialisé git (push possible)" || ko "projet pilote git non initialisable"

# Un unique process python orchestre le scénario (start/stop natif et docker),
# ce qui évite tout process orphelin : chaque start est suivi de son stop dans
# le même manager.
OUT2=$(PREVIEW_DRIVER=native FORGE_PREVIEW_WRAPPER="$WRAPPER_DST" D12_DOCKER="$DOCKER" \
  python3 - "$WS" "$PROJECT" <<'PY' 2>&1
import asyncio, os, socket, subprocess, sys
sys.path.insert(0, os.environ["D12_BACKEND"])
from preview_runtime import PreviewManager

WS = sys.argv[1]
PROJECT = sys.argv[2]
DK = os.environ.get("D12_DOCKER", "/usr/bin/docker")

def dk_ps(running_only=True):
    # Pas de "-q" : Docker ignore alors "--format" et renvoie des IDs, ce qui
    # casserait le filtrage par nom. On liste les noms et on filtre exactement.
    args = [DK, "ps", "--format", "{{.Names}}"] + ([] if running_only else ["-a"])
    r = subprocess.run(args, capture_output=True, text=True)
    return [n for n in r.stdout.split() if n == f"forge-preview-{PROJECT}"]

def free_port():
    for p in range(8090, 8190):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p)); s.close(); return p
        except OSError:
            try: s.close()
            except Exception: pass
    return 8096

def port_open(p):
    s = socket.socket(); s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", p)); return True
    except Exception:
        return False
    finally:
        s.close()

async def start_poll(m, port):
    st = await m.start(PROJECT, port)
    for _ in range(30):
        if st.get("phase") in ("running", "error"):
            break
        await asyncio.sleep(0.5)
        st = m.status(PROJECT, port)
    return st

async def main():
    PORT = free_port()
    print("PORT", PORT)
    subprocess.run([DK, "rm", "-f", f"forge-preview-{PROJECT}"], capture_output=True)

    # --- pilote natif ---
    os.environ["PREVIEW_DRIVER"] = "native"
    mn = PreviewManager(WS)
    st = await start_poll(mn, PORT)
    print("NATIVE_START", st.get("phase"))
    print("NATIVE_NOCONTAINER", "1" if len(dk_ps(running_only=False)) == 0 else "0")
    print("NATIVE_PORT", "1" if port_open(PORT) else "0")
    print("NATIVE_WSINTACT", "1" if os.path.isfile(os.path.join(WS, PROJECT, "index.html")) else "0")
    await mn.stop(PROJECT, PORT)
    await asyncio.sleep(0.4)
    print("NATIVE_STOP", "0" if not port_open(PORT) else "1")

    # --- bascule docker (projet pilote) ---
    os.environ["PREVIEW_DRIVER"] = "docker"
    md = PreviewManager(WS)
    st = await start_poll(md, PORT)
    print("DOC_START", st.get("phase"))
    print("DOC_CONTAINER", "1" if len(dk_ps(running_only=True)) == 1 else "0")
    await md.stop(PROJECT, PORT)
    await asyncio.sleep(0.5)
    print("DOC_STOP", "0" if len(dk_ps(running_only=True)) == 0 else "1")

    # --- retour arrière : repasser natif, le workspace n'a jamais quitté l'hôte ---
    subprocess.run([DK, "rm", "-f", f"forge-preview-{PROJECT}"], capture_output=True)
    os.environ["PREVIEW_DRIVER"] = "native"
    mb = PreviewManager(WS)
    st = await start_poll(mb, PORT)
    print("BACK_START", st.get("phase"))
    print("BACK_NOCONTAINER", "1" if len(dk_ps(running_only=False)) == 0 else "0")
    print("BACK_WSINTACT", "1" if os.path.isfile(os.path.join(WS, PROJECT, "index.html")) else "0")
    print("BACK_PORT", "1" if port_open(PORT) else "0")
    await mb.stop(PROJECT, PORT)
    await asyncio.sleep(0.4)
    print("BACK_STOP", "0" if not port_open(PORT) else "1")

asyncio.run(main())
PY
) || true

mark() { printf '%s\n' "$OUT2" | grep "^$1 " | head -n1 | sed "s/^$1 //"; }

V=$(mark PORT); [ -n "$V" ] && ok "port libre sélectionné ($V)" || ko "port non sélectionné"

V=$(mark NATIVE_START); [ "$V" = "running" ] && ok "pilote natif : start running" || ko "pilote natif : start phase=$V (attendu running)"
V=$(mark NATIVE_NOCONTAINER); [ "$V" = "1" ] && ok "pilote natif : aucun conteneur créé" || ko "pilote natif : un conteneur existe"
V=$(mark NATIVE_PORT); [ "$V" = "1" ] && ok "pilote natif : port ouvert sur l'hôte (127.0.0.1)" || ko "pilote natif : port non ouvert"
V=$(mark NATIVE_WSINTACT); [ "$V" = "1" ] && ok "pilote natif : workspace intact" || ko "pilote natif : workspace perdu"
V=$(mark NATIVE_STOP); [ "$V" = "0" ] && ok "pilote natif : stop libère le port" || ko "pilote natif : stop ne libère pas le port"

V=$(mark DOC_START); [ "$V" = "running" ] && ok "bascule docker : start running" || ko "bascule docker : start phase=$V (attendu running)"
V=$(mark DOC_CONTAINER); [ "$V" = "1" ] && ok "bascule docker : conteneur forge-preview-* créé" || ko "bascule docker : conteneur non créé"
V=$(mark DOC_STOP); [ "$V" = "0" ] && ok "bascule docker : stop arrête le conteneur" || ko "bascule docker : conteneur encore running"

V=$(mark BACK_START); [ "$V" = "running" ] && ok "retour natif : start running" || ko "retour natif : start phase=$V (attendu running)"
V=$(mark BACK_NOCONTAINER); [ "$V" = "1" ] && ok "retour natif : plus de conteneur (hôte uniquement)" || ko "retour natif : conteneur résiduel"
V=$(mark BACK_WSINTACT); [ "$V" = "1" ] && ok "retour natif : workspace intact (jamais quitté l'hôte)" || ko "retour natif : workspace perdu"
V=$(mark BACK_PORT); [ "$V" = "1" ] && ok "retour natif : port ouvert sur l'hôte" || ko "retour natif : port non ouvert"
V=$(mark BACK_STOP); [ "$V" = "0" ] && ok "retour natif : stop libère le port" || ko "retour natif : stop ne libère pas le port"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D12 conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D12 conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D12 NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
