#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d4.sh — Contrôles de sortie de l'étape D4 (contrat d'exécution).
# Lecture seule sans privilège (contrôles statiques) ; exécution réelle en root.
# sudo bash deploy/docker/verify-d4.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2

DOCKER=/usr/bin/docker
WRAPPER_SRC=forge-preview
WRAPPER_DST=/usr/local/bin/forge-preview
NETWORK=${FORGE_PREVIEW_NETWORK:-forge-preview-net}
IMAGE=${FORGE_PREVIEW_IMAGE:-forge-preview-base:local}
SERVICE_USER=quentin

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D4 : contrat d'exécution (wrapper source) =="
[ -f "$WRAPPER_SRC" ] && ok "wrapper présent dans le dépôt" || ko "wrapper absent du dépôt"

for pat in \
  '--name' \
  '--network' \
  'PREVIEW_HOST' \
  '--cpus' \
  '--memory-swap' \
  '--pids-limit' \
  '--read-only' \
  '--tmpfs /tmp:rw' \
  '--tmpfs /run:rw' \
  '-v ' \
  '--security-opt no-new-privileges' \
  '--cap-drop ALL' \
  '--restart=no' \
  'sleep infinity' ; do
  grep -qF -- "$pat" "$WRAPPER_SRC" 2>/dev/null && ok "contrat présent : $pat" \
    || ko "absent du wrapper : $pat"
done

grep -qF '0.0.0.0' "$WRAPPER_SRC" && ko "0.0.0.0 interdit dans le wrapper" || ok "aucune publication 0.0.0.0"
grep -qF -- '--env-file' "$WRAPPER_SRC" && ko "--env-file interdit dans le wrapper" || ok "aucun --env-file"
grep -qF -- '--privileged' "$WRAPPER_SRC" && ko "--privileged interdit dans le wrapper" || ok "aucun --privileged"

echo "== D4 : wrapper installé =="
if [ -f "$WRAPPER_DST" ]; then
  SRC_SHA=$(sha256sum "$WRAPPER_SRC" | cut -d' ' -f1)
  DST_SHA=$(sha256sum "$WRAPPER_DST" | cut -d' ' -f1)
  [ "$SRC_SHA" = "$DST_SHA" ] && ok "wrapper installé identique au dépôt (sha256)" \
    || ko "wrapper installé différent du dépôt (re-lancer install-d4.sh)"
else
  ko "wrapper absent : $WRAPPER_DST"
fi

echo "== D4 : exécution réelle (contrat, root requis) =="
if [ "$ROOT" -ne 1 ]; then
  skip "exécution réelle non exécutable sans privilège (docker run)"
else
  if "$DOCKER" network inspect "$NETWORK" >/dev/null 2>&1; then
    DRIVER="$("$DOCKER" network inspect -f '{{.Driver}}' "$NETWORK")"
    [ "$DRIVER" = "bridge" ] && ok "réseau $NETWORK présent (bridge)" \
      || ko "réseau $NETWORK : driver $DRIVER"
  else
    ko "réseau $NETWORK absent (lancer install-d4.sh)"
  fi

  PROJ="verifyd4"; CNAME="forge-preview-$PROJ"
  WS="/var/www/forge/workspace/.verify-d4"
  PORT=8090
  mkdir -p "$WS" && chown "$SERVICE_USER":"$SERVICE_USER" "$WS"

  if "$WRAPPER_DST" start "$PROJ" --port "$PORT" --workspace "$WS" >/tmp/verify-d4-start.out 2>&1; then
    ok "start $PROJ (code 0)"
    sleep 2
    RUN="$("$DOCKER" inspect -f '{{.State.Running}}' "$CNAME" 2>/dev/null)"
    [ "$RUN" = "true" ] && ok "conteneur en vie (Up) après 2 s" || ko "conteneur non Up : $RUN"

    NW="$("$DOCKER" inspect -f '{{.HostConfig.NetworkMode}}' "$CNAME" 2>/dev/null)"
    [ "$NW" = "$NETWORK" ] && ok "réseau dédié : $NW" || ko "réseau : $NW"

    ROFS="$("$DOCKER" inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$CNAME")"
    [ "$ROFS" = "true" ] && ok "rootfs en lecture seule" || ko "rootfs non read-only"

    SECOPT="$("$DOCKER" inspect -f '{{.HostConfig.SecurityOpt}}' "$CNAME")"
    printf '%s' "$SECOPT" | grep -q 'no-new-privileges' && ok "security-opt no-new-privileges" || ko "security-opt absent"

    CAPDROP="$("$DOCKER" inspect -f '{{.HostConfig.CapDrop}}' "$CNAME")"
    [ "$CAPDROP" = "[ALL]" ] && ok "cap-drop ALL effectif" || ko "cap-drop : $CAPDROP"

    RESTART="$("$DOCKER" inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$CNAME")"
    [ "$RESTART" = "no" ] && ok "restart=no effectif" || ko "restart : $RESTART"

    MEMSWAP="$("$DOCKER" inspect -f '{{.HostConfig.MemorySwap}}' "$CNAME")"
    MEM="$("$DOCKER" inspect -f '{{.HostConfig.Memory}}' "$CNAME")"
    [ "$MEM" != "0" ] && [ "$MEMSWAP" = "$MEM" ] && ok "memory-swap == memory ($MEM)" || ko "memory-swap incohérent (mem=$MEM swap=$MEMSWAP)"

    PIDS="$("$DOCKER" inspect -f '{{.HostConfig.PidsLimit}}' "$CNAME")"
    [ "$PIDS" != "0" ] && ok "pids-limit effectif ($PIDS)" || ko "pids-limit nul"

    CPUS="$("$DOCKER" inspect -f '{{.HostConfig.NanoCpus}}' "$CNAME")"
    [ "$CPUS" != "0" ] && ok "cpus limités ($CPUS)" || ko "cpus non limités"

    PORTMAP="$("$DOCKER" port "$CNAME" 2>/dev/null)"
    printf '%s' "$PORTMAP" | grep -q '127.0.0.1' && ok "port publié sur 127.0.0.1" || ko "port non lié à 127.0.0.1 : $PORTMAP"

    BINDS="$("$DOCKER" inspect -f '{{range .Mounts}}{{.Destination}}:{{.Mode}} {{end}}' "$CNAME")"
    printf '%s' "$BINDS" | grep -q '/workspace:rw' && ok "bind mount /workspace:rw" || ko "bind /workspace absent : $BINDS"

    TMPFS="$("$DOCKER" inspect -f '{{range $k, $v := .HostConfig.Tmpfs}}{{$k}} {{end}}' "$CNAME")"
    printf '%s' "$TMPFS" | grep -q '/tmp' && printf '%s' "$TMPFS" | grep -q '/run' && ok "tmpfs /tmp + /run" || ko "tmpfs incomplet : $TMPFS"

    "$WRAPPER_DST" stop "$PROJ" >/dev/null 2>&1 && ok "stop $PROJ" || ko "stop échoué"
    "$WRAPPER_DST" rm "$PROJ" >/dev/null 2>&1 && ok "rm $PROJ" || ko "rm échoué"
  else
    ko "start $PROJ : $(cat /tmp/verify-d4-start.out 2>/dev/null)"
  fi
  rm -rf "$WS"
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "RÉSULTAT : D4 NON conforme — corriger les lignes KO ci-dessus."
elif [ "$unverified" -ne 0 ]; then
  echo "RÉSULTAT : contrôles statiques OK — exécution réelle non vérifiée (root requis)."
else
  echo "RÉSULTAT : D4 conforme."
fi
exit "$fail"
