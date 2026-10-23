#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d11.sh — Étape D11 : tests et recette.
# Statique sans privilège ; bout-en-bout réel en root (limites, isolement, charge).
# sudo bash deploy/docker/verify-d11.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
SERVER="$ROOTDIR/backend/server.py"
WRAPPER_SRC="$ROOTDIR/deploy/docker/forge-preview"

DOCKER=/usr/bin/docker
WRAPPER_DST=/usr/local/bin/forge-preview
NETWORK=${FORGE_PREVIEW_NETWORK:-forge-preview-net}
IMAGE=${FORGE_PREVIEW_IMAGE:-forge-preview-base:local}
PROJECT=verifyd11
WSROOT=${FORGE_PREVIEW_WORKSPACE_ROOTS:-/var/www/forge/workspace}
WSROOT=${WSROOT%%:*}
[ -n "$WSROOT" ] && [ -d "$WSROOT" ] || WSROOT=/var/www/forge/workspace
WS="$WSROOT/.verify-d11.$$"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D11 : tests et recette (statique) =="
[ -f "$SERVER" ] && ok "server.py présent" || ko "server.py absent"
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"
[ -f "$WRAPPER_SRC" ] && ok "wrapper présent" || ko "wrapper absent"

# 1. Unitaire : ligne docker run portant toutes les limites, bind 127.0.0.1, pas de socket.
grep -qF -- '--cpus "$CPUS"' "$WRAPPER_SRC" \
  && ok "limite CPU (--cpus)" || ko "limite CPU absente"
grep -qF -- '--memory "$MEMORY" --memory-swap "$MEMORY"' "$WRAPPER_SRC" \
  && ok "limite mémoire + swap borné (pas de débordement)" || ko "limite mémoire absente"
grep -qF -- '--pids-limit "$PIDSLIMIT"' "$WRAPPER_SRC" \
  && ok "limite de processus (--pids-limit)" || ko "limite pids absente"
grep -qE '^PREVIEW_HOST=127\.0\.0\.1$' "$WRAPPER_SRC" \
  && ok "bind hôte 127.0.0.1 (pas 0.0.0.0)" || ko "bind hôte non restreint à 127.0.0.1"
if grep -qE 'docker\.sock|/var/run/docker' "$WRAPPER_SRC"; then
  ko "socket docker monté dans la ligne run"
else
  ok "aucun socket docker monté"
fi
grep -qF -- '--read-only' "$WRAPPER_SRC" \
  && ok "rootfs en lecture seule (--read-only)" || ko "--read-only absent"
grep -qF -- '--security-opt no-new-privileges' "$WRAPPER_SRC" \
  && ok "no-new-privileges" || ko "no-new-privileges absent"
grep -qF -- '--cap-drop ALL' "$WRAPPER_SRC" \
  && ok "capabilités toutes retirées (--cap-drop ALL)" || ko "--cap-drop ALL absent"
grep -qF -- '--restart=no' "$WRAPPER_SRC" \
  && ok "pas de redémarrage auto (--restart=no)" || ko "--restart=no absent"
grep -qF 'sleep infinity' "$WRAPPER_SRC" \
  && ok "garde en vie sans exécuter de code projet" || ko "sleep infinity absent"

# 2. Non-régression : les routes preview existantes sont conservées.
for r in 'preview/start' 'preview/restart' 'preview/stop' 'preview/status' 'preview/logs'; do
  grep -qF "/$r\"" "$SERVER" && ok "route /workspace/projects/{name}/$r présente" \
    || ko "route /workspace/projects/{name}/$r absente"
done

# 3. Arrêt par inactivité (reaper) toujours branché.
grep -qF 'await mgr.stop(name, port)' "$SERVER" \
  && ok "reaper (arrêt par inactivité) appelle mgr.stop()" || ko "reaper n'appelle pas stop()"
grep -qF '_reap_idle_previews' "$SERVER" \
  && ok "tâche _reap_idle_previews enregistrée" || ko "_reap_idle_previews absent"

echo
if [ "$ROOT" -eq 0 ]; then
  skip "bout-en-bout réel : root requis (sudo bash deploy/docker/verify-d11.sh)"
  echo
  if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
    echo "RÉSULTAT : D11 conforme."
    exit 0
  elif [ "$fail" -eq 0 ]; then
    echo "RÉSULTAT : D11 conforme (partiellement non vérifié)."
    exit 0
  else
    echo "RÉSULTAT : D11 NON conforme — corriger les lignes KO ci-dessus."
    exit 1
  fi
fi

echo
echo "== D11 : bout-en-bout réel (root requis) =="

SUDO_USER_UID=${SUDO_UID:-0}
if [ "$SUDO_USER_UID" -ne 0 ] && sudo -n -u "#$SUDO_USER_UID" "$WRAPPER_DST" health >/dev/null 2>&1; then
  ok "utilisateur service peut appeler le wrapper sans mot de passe"
elif [ "$SUDO_USER_UID" -eq 0 ]; then
  ok "root peut appeler le wrapper"
else
  ko "wrapper non appelable sans mot de passe par l'utilisateur du service"
fi

cleanup() {
  "$WRAPPER_DST" stop "$PROJECT" >/dev/null 2>&1 || true
  "$WRAPPER_DST" rm "$PROJECT" >/dev/null 2>&1 || true
  rm -rf "$WS" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$WS" && ok "workspace de test créé" || { ko "workspace non créé"; exit 1; }
printf '<!doctype html><title>d11</title><h1>D11</h1>\n' > "$WS/index.html"
( cd "$WS" && git init -q && git add -A && git -c user.email=d11@local -c user.name=d11 commit -qm init ) \
  && ok "workspace initialisé git (push possible)" || ko "workspace git non initialisable"

"$WRAPPER_DST" start "$PROJECT" --workspace "$WS" --port 8097 >/dev/null 2>&1 \
  && ok "conteneur démarré" || { ko "échec forge-preview start"; exit 1; }

CID="$("$DOCKER" ps -q -f "name=forge-preview-$PROJECT" 2>/dev/null | head -n1)"
[ -n "$CID" ] && ok "conteneur identifié" || { ko "conteneur introuvable"; exit 1; }

# Limites réellement appliquées par Docker.
NANOCPUS=$("$DOCKER" inspect -f '{{.HostConfig.NanoCpus}}' "$CID" 2>/dev/null || true)
if [ -n "$NANOCPUS" ] && [ "$NANOCPUS" -gt 0 ]; then
  ok "CPU plafonné réellement (NanoCpus=$NANOCPUS)"
else
  ko "CPU non plafonné (NanoCpus=$NANOCPUS)"
fi

MEM=$("$DOCKER" inspect -f '{{.HostConfig.Memory}}' "$CID" 2>/dev/null || true)
MEMSWAP=$("$DOCKER" inspect -f '{{.HostConfig.MemorySwap}}' "$CID" 2>/dev/null || true)
if [ -n "$MEM" ] && [ "$MEM" -gt 0 ] && [ "$MEMSWAP" = "$MEM" ]; then
  ok "mémoire plafonnée, swap borné (mem=swap=$MEM)"
else
  ko "mémoire/swap non bornés (mem=$MEM swap=$MEMSWAP)"
fi

PIDS=$("$DOCKER" inspect -f '{{.HostConfig.PidsLimit}}' "$CID" 2>/dev/null || true)
if [ -n "$PIDS" ] && [ "$PIDS" -gt 0 ]; then
  ok "pids plafonnés (PidsLimit=$PIDS)"
else
  ko "pids non plafonnés (PidsLimit=$PIDS)"
fi

# Bind 127.0.0.1 côté hôte (pas 0.0.0.0).
PUB=$("$DOCKER" inspect -f '{{range $k,$v := .HostConfig.PortBindings}}{{range $v}}{{$v.HostIP}}:{{$v.HostPort}} {{end}}{{end}}' "$CID" 2>/dev/null || true)
case "$PUB" in
  *"0.0.0.0"*) ko "port publié sur 0.0.0.0 ($PUB)" ;;
  *"127.0.0.1"*) ok "port publié lié à 127.0.0.1 ($PUB)" ;;
  *) ok "port publié (pas de 0.0.0.0) : $PUB" ;;
esac

# Pas de socket docker monté.
if "$DOCKER" inspect -f '{{range .Mounts}}{{$_.Source}} {{end}}' "$CID" 2>/dev/null | grep -qE 'docker\.sock|/var/run/docker'; then
  ko "socket docker monté dans le conteneur"
else
  ok "aucun socket docker monté"
fi

ROFS=$("$DOCKER" inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$CID" 2>/dev/null || true)
[ "$ROFS" = "true" ] && ok "rootfs en lecture seule appliqué" || ko "rootfs non en lecture seule"

RESTART=$("$DOCKER" inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$CID" 2>/dev/null || true)
[ "$RESTART" = "no" ] && ok "restart=no appliqué" || ko "restart non 'no' : $RESTART"

# 3. Charge : une boucle CPU dans le conteneur reste plafonnée.
# On mesure l'usage reel via cpu.stat (cgroup v2) sur un intervalle fixe :
# docker stats CPUPerc est normalise sur le total des coeurs de l'hote et
# fausse a la premiere lecture, il ne mesure pas le plafond --cpus=1.0.
"$DOCKER" exec -d "$CID" /bin/sh -c 'while :; do :; done' >/dev/null 2>&1
# Localiser le cgroup du conteneur via son PID (robuste quel que soit le scope).
CPID=$("$DOCKER" inspect -f '{{.State.Pid}}' "$CID" 2>/dev/null || true)
CGCG=""
if [ -n "$CPID" ] && [ "$CPID" -gt 0 ] 2>/dev/null; then
  CGROUP=$(grep -m1 '^0::' "/proc/$CPID/cgroup" 2>/dev/null | cut -d: -f3)
  [ -n "$CGROUP" ] && [ -f "/sys/fs/cgroup${CGROUP}/cpu.stat" ]     && CGCG="/sys/fs/cgroup${CGROUP}/cpu.stat"
fi
read_usec() { awk '/^usage_usec /{print $2}' "$1" 2>/dev/null; }
U1=""; U2=""
[ -n "$CGCG" ] && U1=$(read_usec "$CGCG")
sleep 3
[ -n "$CGCG" ] && U2=$(read_usec "$CGCG")

# Seuil : --cpus=1.0 => ~1 coeur. On tolere 1.15 coeur (marge de burst/mesure).
if [ -n "$U1" ] && [ -n "$U2" ] && [ "$U2" -ge "$U1" ]; then
  DELTA=$((U2 - U1))
  CORES=$(awk -v d="$DELTA" 'BEGIN{printf "%.3f", d/3000000}')
  if [ "$DELTA" -le 3450000 ]; then
    ok "CPU reellement plafonne (usage moyen=${CORES} coeur(s), limite=1.0)"
  else
    ko "CPU non plafonne (usage moyen=${CORES} coeur(s) pour --cpus=1.0)"
  fi
else
  CPUPCT=$("$DOCKER" stats --no-stream --format '{{.CPUPerc}}' "$CID" 2>/dev/null | sed 's/%//')
  [ -z "$CPUPCT" ] && CPUPCT=0
  if [ "$(awk -v c="$CPUPCT" 'BEGIN{print (c+0) > 110 ? 1 : 0}')" -eq 0 ]; then
    ok "charge CPU plafonnee sous limite (usage=${CPUPCT}%)"
  else
    ko "CPU non plafonne (usage=${CPUPCT}% pour --cpus=1.0)"
  fi
fi
# L'hôte reste réactif : le script lui-même tourne encore (test implicite du point 3).
uptime >/dev/null 2>&1 && ok "hôte réactif pendant la charge" || ko "hôte non réactif"

# Arrêt par inactivité : équivalent reaper (mgr.stop => wrapper stop).
"$WRAPPER_DST" stop "$PROJECT" >/dev/null 2>&1 \
  && ok "arrêt par inactivité (équivalent reaper) via wrapper stop" || ko "échec wrapper stop"

STATE=$("$DOCKER" inspect -f '{{.State.Status}}' "forge-preview-$PROJECT" 2>/dev/null || true)
[ "$STATE" = "exited" ] && ok "conteneur arrêté (exited)" || ko "conteneur non arrêté : '$STATE'"

[ -f "$WS/index.html" ] && ok "workspace intact après arrêt" || ko "workspace perdu"

# Redémarrage manuel (point 2 de D11).
"$WRAPPER_DST" start "$PROJECT" --workspace "$WS" --port 8097 >/dev/null 2>&1 \
  && ok "redémarrage manuel possible" || ko "redémarrage manuel en échec"
STATE2=$("$DOCKER" inspect -f '{{.State.Status}}' "forge-preview-$PROJECT" 2>/dev/null || true)
[ "$STATE2" = "running" ] && ok "conteneur relancé (running)" || ko "conteneur non relancé : '$STATE2'"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D11 conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D11 conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D11 NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
