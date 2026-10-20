#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d3.sh — Controles de sortie de l'etape D3 (lecture seule).
#
# sudo bash deploy/docker/verify-d3.sh
#
# N'installe rien, ne modifie rien. Code retour 0 = D3 conforme.
# Les controles d'execution (docker run) exigent root : le script refuse
# de conclure sans privilege plutot que de renvoyer un faux positif.
#
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKER_DIR="$APP_DIR/deploy/docker"
DOCKERFILE="$DOCKER_DIR/preview-base.Dockerfile"
IMAGE="${FORGE_PREVIEW_IMAGE:-forge-preview-base:local}"
PREVIEW_UID="${PREVIEW_UID:-1000}"
EXPECTED_VERBS='start|stop|rm|ps|logs|inspect|health'

fail=0
unverified=0

ok()    { printf ' \033[1;32mOK\033[0m   %s\n' "$*"; }
ko()    { printf ' \033[1;31mKO\033[0m   %s\n' "$*"; fail=$((fail+1)); }
skip()  { printf ' \033[1;33m??\033[0m   %s\n' "$*"; unverified=$((unverified+1)); }

IS_ROOT=1
if [ "$(id -u)" -ne 0 ]; then
    IS_ROOT=0
    printf '\033[1;33mAvertissement : exécution sans privilège.\033[0m\n' >&2
    printf 'Les contrôles statiques du Dockerfile sont exécutés ; les contrôles\n' >&2
    printf 'd image et de conteneur (socket Docker) seront marqués non exécutés.\n' >&2
    printf 'Vérification complète : sudo bash deploy/docker/verify-d3.sh\n' >&2
fi

export DOCKER_CONFIG="${DOCKER_CONFIG:-/tmp/forge-d3-dockercfg}"
mkdir -p "$DOCKER_CONFIG"

echo "== D3 : Dockerfile =="
if [ -f "$DOCKERFILE" ]; then
    ok "Dockerfile présent : deploy/docker/preview-base.Dockerfile"
else
    ko "Dockerfile absent : $DOCKERFILE"
    echo; printf '\033[1;31mRÉSULTAT : D3 NON conforme.\033[0m\n'; exit 1
fi

grep -Eq '^USER[[:space:]]+preview' "$DOCKERFILE" && ok "USER preview présent" \
    || ko "USER preview absent (l image tournerait en root)"
grep -Eq '^WORKDIR[[:space:]]+/workspace' "$DOCKERFILE" && ok "WORKDIR /workspace présent" \
    || ko "WORKDIR /workspace absent"
grep -Eq 'node:' "$DOCKERFILE" && ok "base Node présente" || ko "base Node absente"
grep -Eq 'python3' "$DOCKERFILE" && ok "python3 présent" || ko "python3 absent"
grep -Eq '^[[:space:]]+git[[:space:]]*\\?$|^[[:space:]]+git[[:space:]]' "$DOCKERFILE" && ok "git présent" \
    || ko "git absent"

# Le dernier USER du fichier doit etre preview (pas de retour a root).
LAST_USER="$(grep -E '^USER[[:space:]]' "$DOCKERFILE" | tail -1 | awk '{print $2}')"
[ "$LAST_USER" = "preview" ] && ok "dernier USER = preview" \
    || ko "dernier USER = ${LAST_USER:-aucun} (attendu preview)"

# Aucun secret, aucun .env, aucun code projet embarque.
if grep -Eq 'ENV[[:space:]]+.*(SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY)' "$DOCKERFILE"; then
    ko "une directive ENV ressemble a un secret"
else
    ok "aucun secret en variable ENV"
fi
# Ancrage en debut de ligne : un simple `grep COPY|ADD` matcherait les
# commentaires du Dockerfile qui citent ces directives (faux positif).
if grep -Eq '^[[:space:]]*(COPY|ADD)([[:space:]]|$)' "$DOCKERFILE"; then
    ko "COPY/ADD présent : du code pourrait être embarqué dans l image"
else
    ok "aucun COPY/ADD (aucun code projet dans l image)"
fi


# Sans privilège, on s'arrete ici : tout ce qui suit exige le socket Docker.
# Le script ne conclut pas "conforme" et ne sort pas en erreur : il rend les
# controles statiques executes, puis declare le reste non verifie.
if [ "$IS_ROOT" -ne 1 ]; then
    echo
    echo "== D3 : image construite =="
    skip "inspection de l'image non exécutable sans privilège (socket Docker)"
    skip "execution reelle non exécutable sans privilège (docker run)"
    echo
    if [ "$fail" -gt 0 ]; then
        printf '\033[1;31mRÉSULTAT : D3 NON conforme — %s contrôle(s) KO.\033[0m\n' "$fail"
        exit 1
    fi
    printf '\033[1;33mRÉSULTAT : D3 partiellement vérifié — contrôles dynamiques non exécutés (root requis).\033[0m\n'
    exit 0
fi

echo "== D3 : image construite =="
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    ko "image $IMAGE absente (lancer : sudo bash deploy/docker/build-d3.sh)"
    echo; printf '\033[1;31mRÉSULTAT : D3 NON conforme.\033[0m\n'; exit 1
fi
ok "image présente : $IMAGE"

IMG_USER="$(docker image inspect -f '{{.Config.User}}' "$IMAGE" 2>/dev/null)"
[ "$IMG_USER" = "preview" ] && ok "Config.User = preview" \
    || ko "Config.User = '${IMG_USER:-vide}' (attendu preview)"
IMG_WD="$(docker image inspect -f '{{.Config.WorkingDir}}' "$IMAGE" 2>/dev/null)"
[ "$IMG_WD" = "/workspace" ] && ok "WorkingDir = /workspace" \
    || ko "WorkingDir = '${IMG_WD:-vide}' (attendu /workspace)"

echo "== D3 : exécution réelle (identité, outils, read-only) =="
RUN_ID="$(docker run --rm --network none --read-only \
            --tmpfs /tmp:rw,size=64m,noexec,nosuid,nodev \
            --tmpfs /run:rw,size=16m,noexec,nosuid,nodev \
            --security-opt no-new-privileges --cap-drop ALL \
            "$IMAGE" /bin/sh -c 'id -u; id -g; node --version; npm --version; python3 --version; git --version' 2>&1)"
RUN_RC=$?
if [ "$RUN_RC" -eq 0 ]; then
    ok "conteneur démarre sous --read-only (code 0)"
else
    ko "le conteneur ne démarre pas sous --read-only : $RUN_ID"
fi

GOT_UID="$(printf '%s\n' "$RUN_ID" | sed -n '1p')"
GOT_GID="$(printf '%s\n' "$RUN_ID" | sed -n '2p')"
[ "$GOT_UID" = "$PREVIEW_UID" ] && ok "uid d exécution = $PREVIEW_UID" \
    || ko "uid d exécution = '${GOT_UID:-?}' (attendu $PREVIEW_UID)"
[ "$GOT_GID" = "1000" ] && ok "gid d exécution = 1000" \
    || ko "gid d exécution = '${GOT_GID:-?}' (attendu 1000)"
[ "$GOT_UID" != "0" ] && ok "l image ne tourne pas en root" \
    || ko "l image tourne en root (uid 0)"

printf '%s\n' "$RUN_ID" | grep -Eq '^v[0-9]+\.' && ok "node répond : $(printf '%s\n' "$RUN_ID" | sed -n '3p')" \
    || ko "node ne répond pas"
printf '%s\n' "$RUN_ID" | grep -Eq '^[0-9]+\.[0-9]+' && ok "npm répond : $(printf '%s\n' "$RUN_ID" | sed -n '4p')" \
    || ko "npm ne répond pas"
printf '%s\n' "$RUN_ID" | grep -Eq '^Python [0-9]+\.' && ok "python3 répond : $(printf '%s\n' "$RUN_ID" | sed -n '5p')" \
    || ko "python3 ne répond pas"
printf '%s\n' "$RUN_ID" | grep -Eq '^git version' && ok "git répond : $(printf '%s\n' "$RUN_ID" | sed -n '6p')" \
    || ko "git ne répond pas"

# HOME inscriptible sous --read-only (npm/pip en ont besoin).
# On ecrit reellement (mkdir + touch) au lieu de tester `test -w` : le
# tmpfs 256m du wrapper est le montage reel, et l ecriture prouve le droit
# sans dependre de l interpretation de access(2) sur un montage ro.
docker run --rm --network none --read-only \
    --tmpfs /tmp:rw,size=256m,noexec,nosuid,nodev \
    --tmpfs /run:rw,size=64m,noexec,nosuid,nodev \
    --security-opt no-new-privileges --cap-drop ALL \
    -e HOME=/tmp -e npm_config_cache=/tmp/.npm -e PIP_CACHE_DIR=/tmp/.pip \
    "$IMAGE" /bin/sh -c 'mkdir -p "$npm_config_cache" "$PIP_CACHE_DIR" \
        && : > "$npm_config_cache/.w" && : > "$PIP_CACHE_DIR/.w"' >/dev/null 2>&1
[ $? -eq 0 ] && ok "HOME/cache npm et pip inscriptibles sous --read-only" \
    || ko "HOME/cache npm ou pip non inscriptibles sous --read-only"

# /workspace : present, possede par preview, et reellement inscriptible.
# L ecriture d un fichier temoin prouve le droit (sans bind mount, donc sans
# dependre du montage hote) ; verify-d4.sh revalidera avec le montage reel.
WS_OUT="$(docker run --rm --network none --read-only \
    --security-opt no-new-privileges --cap-drop ALL \
    "$IMAGE" /bin/sh -c 'test -d /workspace || { echo NODIR; exit 1; }
        stat -c "%U" /workspace
        : > /workspace/.forge-write-test || { echo NOWRITE; exit 1; }
        rm -f /workspace/.forge-write-test' 2>&1)"
WS_RC=$?
if [ "$WS_RC" -eq 0 ] && [ "$WS_OUT" = "preview" ]; then
    ok "/workspace présent, propriétaire preview, inscriptible par preview"
else
    ko "/workspace : présence/propriété/écriture en échec (rc=$WS_RC, sortie: ${WS_OUT:-vide})"
fi

# Aucune capability effective, pas de new privileges.
docker run --rm --network none --read-only \
    --security-opt no-new-privileges --cap-drop ALL \
    "$IMAGE" /bin/sh -c 'grep -q "^CapEff:[[:space:]]*0000000000000000" /proc/self/status' >/dev/null 2>&1
[ $? -eq 0 ] && ok "aucune capability effective (CapEff=0)" \
    || skip "CapEff non nul (vérifier le runtime)"

# Pas de code projet, pas de .env dans l image.
LEAK="$(docker run --rm --network none --read-only \
    --security-opt no-new-privileges --cap-drop ALL \
    "$IMAGE" /bin/sh -c 'ls -A /workspace 2>/dev/null | wc -l' 2>/dev/null)"
if [ "${LEAK:-1}" = "0" ]; then
    ok "/workspace vide dans l image (aucun code embarqué)"
else
    ko "/workspace contient ${LEAK:-?} entrée(s) : du code est embarqué dans l image"
fi

if docker run --rm --network none --read-only \
    --security-opt no-new-privileges --cap-drop ALL \
    "$IMAGE" /bin/sh -c 'test -e /workspace/.env -o -e /app/.env' >/dev/null 2>&1; then
    ko "un fichier .env est présent dans l image"
else
    ok "aucun .env dans l image"
fi

echo
if [ "$fail" -gt 0 ]; then
    printf '\033[1;31mRÉSULTAT : D3 NON conforme — %s contrôle(s) KO.\033[0m\n' "$fail"
    exit 1
fi
if [ "$unverified" -gt 0 ]; then
    printf '\033[1;33mRÉSULTAT : D3 partiellement vérifié — %s contrôle(s) non concluant(s).\033[0m\n' "$unverified"
    exit 0
fi
printf '\033[1;32mRÉSULTAT : D3 conforme.\033[0m\n'
exit 0
