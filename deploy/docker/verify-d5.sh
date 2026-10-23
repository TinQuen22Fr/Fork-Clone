#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d5.sh — Contrôles de sortie de l'étape D5 (driver Docker).
# Statique sans privilège ; bout-en-bout réel en root (conteneur + port).
# sudo bash deploy/docker/verify-d5.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
WRAPPER_SRC="$ROOTDIR/deploy/docker/forge-preview"

DOCKER=/usr/bin/docker
WRAPPER_DST=/usr/local/bin/forge-preview
NETWORK=${FORGE_PREVIEW_NETWORK:-forge-preview-net}
IMAGE=${FORGE_PREVIEW_IMAGE:-forge-preview-base:local}
SERVICE=${FORGE_PREVIEW_SERVICE:-forge-backend.service}
PROJECT=verifyd5

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D5 : driver dans preview_runtime.py (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

for pat in \
  'self.driver' \
  'PREVIEW_DRIVER' \
  'FORGE_PREVIEW_WRAPPER' \
  'async def _start_docker' \
  'async def _run_docker' \
  'async def _docker' \
  'def _write_script' \
  'def _rel_script' \
  'def _docker_cmd' \
  'def _bind' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null && ok "présent : $pat" || ko "absent : $pat"
done

# API publique inchangée (le frontend ne doit pas bouger).
for sig in \
  'async def start(self, project: str, port: int, restart: bool = False)' \
  'async def stop(self, project: str, port: int)' \
  'async def stop_all(self)' \
  'def status(self, project: str, port: int)' \
  'def logs(self, project: str, target: str = "", max_bytes: int = LOG_TAIL_BYTES)' \
  'def idle_projects(self, max_idle: float)' \
  'def touch(self, project: str)' ; do
  grep -qF -- "$sig" "$RUNTIME" 2>/dev/null && ok "API publique : ${sig%(*}" \
    || ko "API publique modifiée : $sig"
done

# Le lancement metier ne doit plus passer par un process hote en mode docker.
grep -qF -- 'await self._docker("exec"' "$RUNTIME" \
  && ok "lancement via exec borne du wrapper" || ko "lancement non borné par le wrapper"
grep -qF -- '"--detach"' "$RUNTIME" \
  && ok "exec --detach présent" || ko "exec --detach absent"
grep -qF -- 'sudo", "-n"' "$RUNTIME" \
  && ok "appel sudo -n (jamais interactif)" || ko "sudo non borné / interactif"

# logs() doit passer par docker logs.
grep -qF -- '"logs", project, "--tail"' "$RUNTIME" \
  && ok "logs() via docker logs --tail" || ko "logs() ne passe pas par le conteneur"

# Verbe exec présent dans le wrapper, chemin borne.
for pat in 'exec)' '--script' '--detach' '.forge-preview/*.sh' ; do
  grep -qF -- "$pat" "$WRAPPER_SRC" 2>/dev/null \
    && ok "wrapper : $pat" || ko "wrapper incomplet : $pat"
done
grep -qF -- '--port2' "$WRAPPER_SRC" 2>/dev/null \
  && ok "wrapper : --port2 (cible api)" || ko "wrapper : --port2 absent"

echo "== D5 : bind interne vs publication =="
# 0.0.0.0 est interdit dans le wrapper (publication), obligatoire en conteneur.
grep -qF '0.0.0.0' "$WRAPPER_SRC" 2>/dev/null \
  && ko "0.0.0.0 interdit dans le wrapper" || ok "wrapper : aucune publication 0.0.0.0"
grep -qF 'return "0.0.0.0" if self.driver == "docker" else "127.0.0.1"' "$RUNTIME" \
  && ok "bind interne = 0.0.0.0 en docker, 127.0.0.1 en natif" \
  || ko "bind interne non adapté au driver"

echo "== D5 : installation =="
if [ -f /etc/systemd/system/"$SERVICE".d/50-forge-preview-driver.conf ]; then
  grep -qF 'PREVIEW_DRIVER=docker' \
    /etc/systemd/system/"$SERVICE".d/50-forge-preview-driver.conf \
    && ok "drop-in PREVIEW_DRIVER=docker présent" || ko "drop-in sans PREVIEW_DRIVER=docker"
else
  ko "drop-in absent (lancer install-d5.sh)"
fi
if [ -f "$WRAPPER_DST" ]; then
  A=$(sha256sum "$WRAPPER_DST" | cut -d' ' -f1)
  B=$(sha256sum "$WRAPPER_SRC" | cut -d' ' -f1)
  [ "$A" = "$B" ] && ok "wrapper installé identique au dépôt" \
    || ko "wrapper installé différent du dépôt (relancer install-d5.sh)"
else
  ko "wrapper non installé : $WRAPPER_DST"
fi

echo "== D5 : bout-en-bout réel (root requis) =="
if [ "$ROOT" -ne 1 ]; then
  skip "exécution réelle : root requis (sudo bash deploy/docker/verify-d5.sh)"
  echo
  if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
    echo "RÉSULTAT : D5 conforme."
  elif [ "$fail" -eq 0 ]; then
    echo "RÉSULTAT : D5 statiquement conforme — exécution réelle non vérifiée."
  else
    echo "RÉSULTAT : D5 NON conforme — corriger les lignes KO ci-dessus."
  fi
  exit "$fail"
fi

SVC_USER="$(systemctl show -p User --value "$SERVICE" 2>/dev/null)"
[ -n "$SVC_USER" ] || SVC_USER=root
[ -x "$WRAPPER_DST" ] || { ko "wrapper absent"; echo "RÉSULTAT : D5 NON conforme."; exit 1; }

# Le service doit pouvoir appeler le wrapper sans mot de passe.
if [ "$SVC_USER" = root ]; then
  ok "service exécuté par root (wrapper direct)"
else
  sudo -u "$SVC_USER" sudo -n "$WRAPPER_DST" health >/dev/null 2>&1 \
    && ok "$SVC_USER peut appeler le wrapper sans mot de passe" \
    || ko "$SVC_USER ne peut pas appeler le wrapper (install-d5.sh)"
fi

export D5_BACKEND="$ROOTDIR/backend"
# Le wrapper n'autorise que FORGE_PREVIEW_WORKSPACE_ROOTS (defaut
# /var/www/forge/workspace) et sudo -n reinitialise l'environnement :
# le workspace de test doit donc etre cree SOUS cette racine.
WSROOT=${FORGE_PREVIEW_WORKSPACE_ROOTS:-/var/www/forge/workspace}
WSROOT=${WSROOT%%:*}
mkdir -p "$WSROOT"
VROOT=$(mktemp -d "$WSROOT/.verify-d5.XXXXXX")
cleanup() {
  PREVIEW_DRIVER=docker FORGE_PREVIEW_WRAPPER="$WRAPPER_DST" \
    python3 - "$VROOT" "$PROJECT" <<'PY' >/dev/null 2>&1
import asyncio, sys
import os; sys.path.insert(0, os.environ["D5_BACKEND"])
from preview_runtime import PreviewManager
async def main():
    m = PreviewManager(sys.argv[1])
    await m.stop(sys.argv[2], 8095)
asyncio.run(main())
PY
  "$WRAPPER_DST" rm "$PROJECT" >/dev/null 2>&1 || true
  rm -rf "$VROOT"
}
trap cleanup EXIT

mkdir -p "$VROOT/$PROJECT"
printf '<!doctype html><title>d5</title><h1>D5</h1>\n' >"$VROOT/$PROJECT/index.html"
ok "workspace de test créé"

OUT=$(PREVIEW_DRIVER=docker FORGE_PREVIEW_WRAPPER="$WRAPPER_DST" \
  python3 - "$VROOT" "$PROJECT" <<'PY' 2>&1
import asyncio, json, sys
import os; sys.path.insert(0, os.environ["D5_BACKEND"])
from preview_runtime import PreviewManager

async def main():
    m = PreviewManager(sys.argv[1])
    print("DRIVER", m.driver)
    assert m.driver == "docker", f"driver={m.driver}"
    st = await m.start(sys.argv[2], 8095)
    for _ in range(30):
        if st.get("phase") in ("running", "error"):
            break
        await asyncio.sleep(0.5)
        st = m.status(sys.argv[2], 8095)
    print("START", json.dumps({k: st.get(k) for k in ("phase", "message", "port")}))
    lo = m.logs(sys.argv[2])
    print("LOGS", "oui" if lo is not None else "non")
    st2 = m.status(sys.argv[2], 8095)
    print("STATUS", json.dumps({k: st2.get(k) for k in ("phase", "message", "port")}))

asyncio.run(main())
PY
) || true

printf '%s\n' "$OUT" | grep -q '^DRIVER docker$' \
  && ok "PREVIEW_DRIVER=docker pris en compte" || ko "driver docker non pris en compte"
printf '%s\n' "$OUT" | grep -q 'AssertionError' \
  && ko "assertion échouée : $(printf '%s\n' "$OUT" | grep -m1 AssertionError)"

PHASE=$(printf '%s\n' "$OUT" | sed -n 's/^START .*"phase": "\([^"]*\)".*/\1/p')
[ "$PHASE" = "running" ] && ok "start() : phase running" \
  || ko "start() : phase $PHASE (attendu running)"

if "$DOCKER" inspect -f '{{.State.Running}}' "forge-preview-$PROJECT" 2>/dev/null | grep -q true; then
  ok "conteneur forge-preview-$PROJECT en cours"
else
  ko "conteneur forge-preview-$PROJECT non démarré"
fi

"$DOCKER" inspect -f '{{range $k, $v := .HostConfig.PortBindings}}{{$k}} -> {{range $v}}{{.HostIp}}{{end}}{{end}}' \
  "forge-preview-$PROJECT" 2>/dev/null | grep -q '8095/tcp -> 127.0.0.1' \
  && ok "port 8095 publié sur 127.0.0.1" || ko "publication du port 8095 incorrecte"

if command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 10 http://127.0.0.1:8095/ | grep -qi '<h1>D5</h1>'; then
    ok "HTTP 200 sur 127.0.0.1:8095 (application servie)"
  else
    ko "aucune réponse HTTP sur 127.0.0.1:8095"
  fi
else
  skip "curl absent : réponse HTTP non vérifiée"
fi

printf '%s\n' "$OUT" | grep -q '^LOGS oui' && ok "logs() répond via docker logs" \
  || ko "logs() vide ou en erreur"

STOP_OUT=$(PREVIEW_DRIVER=docker FORGE_PREVIEW_WRAPPER="$WRAPPER_DST" \
  python3 - "$VROOT" "$PROJECT" <<'PY' 2>&1
import asyncio, json, sys
import os; sys.path.insert(0, os.environ["D5_BACKEND"])
from preview_runtime import PreviewManager

async def main():
    m = PreviewManager(sys.argv[1])
    await m.stop(sys.argv[2], 8095)
    st = m.status(sys.argv[2], 8095)
    print("STOP", json.dumps({k: st.get(k) for k in ("phase", "message")}))

asyncio.run(main())
PY
) || true

SPHASE=$(printf '%s\n' "$STOP_OUT" | sed -n 's/^STOP .*"phase": "\([^"]*\)".*/\1/p')
[ "$SPHASE" = "stopped" ] && ok "stop() : phase stopped" \
  || ko "stop() : phase $SPHASE (attendu stopped)"

sleep 1
"$DOCKER" inspect -f '{{.State.Running}}' "forge-preview-$PROJECT" 2>/dev/null | grep -q false \
  && ok "conteneur arrêté (workspace conservé)" || ko "conteneur encore en cours après stop()"

[ -f "$VROOT/$PROJECT/index.html" ] \
  && ok "workspace intact après arrêt" || ko "workspace altéré par le conteneur"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D5 conforme."
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D5 conforme, $unverified contrôle(s) non vérifié(s)."
else
  echo "RÉSULTAT : D5 NON conforme — corriger les lignes KO ci-dessus."
fi
exit "$fail"
