#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# preview-base.Dockerfile — Etape D3 : image de base commune des previews.
#
# But : une seule image, construite une fois, utilisee par tous les projets.
# Elle ne contient AUCUN code projet, AUCUN secret, AUCUN .env.
#
# Build (voir deploy/docker/build-d3.sh) :
#   docker build -f deploy/docker/preview-base.Dockerfile \
#     -t forge-preview-base:v1 deploy/docker
#
# Contraintes imposees par D4 (contrat d'execution) :
#   --read-only  -> tout ce qui doit s'ecrire est sous /tmp, /run ou /workspace
#   --cap-drop ALL, no-new-privileges -> aucun besoin de capability
#   -v <workspace>:/workspace:rw      -> UID/GID alignes sur l'hote
#
# Choix d'implementation :
#   - UID/GID 1000 par defaut, surchargeables au build (ARG) pour rester
#     alignes sur le proprietaire du workspace monte. Defaut = quentin.
#   - /home/preview n'est PAS cree : HOME pointe sur /tmp, qui est le seul
#     chemin inscriptible garanti sous --read-only (tmpfs 256m monte par le
#     wrapper). Cela evite les volumes anonymes qui fuiraient a chaque run.
#   - npm cache et XDG cache pointent sous /tmp pour la meme raison.
#   - Aucun USER root a l'execution : USER preview en fin de fichier.
#   - Aucun superviseur (tini/supervisord) : PID 1 = le process du projet.
#     pids-limit=256 suffit a contenir les forks, un init intermediaire
#     compliquerait l'arret (le wrapper fait docker stop, pas docker kill).

# Node 26 = version "Current" a la date de redaction (2026-10-20) ; elle
# passe Active LTS le 28/10/2026. Node 24 est encore Active LTS jusqu a cette
# date. Choix assume : on aligne l image sur le host (v26.10.0) pour que les
# modules natifs compiles dans la preview et sur l hote restent compatibles.
# Pour exiger strictement "LTS" des aujourd hui, construire avec :
#   PREVIEW_NODE_VERSION=24 sudo bash deploy/docker/build-d3.sh
# (build-d3.sh transmet l ARG via --build-arg NODE_VERSION si fourni).
# Defaut 26 ; build-d3.sh transmet PREVIEW_NODE_VERSION en --build-arg.
ARG NODE_VERSION=26
FROM node:${NODE_VERSION}-bookworm-slim

LABEL org.opencontainers.image.title="forge-preview-base"
LABEL org.opencontainers.image.description="Base commune des previews de projets Forge (Node LTS + Python 3 + git)"
LABEL org.opencontainers.image.vendor="Claude Unchained Forge"
LABEL org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV NPM_CONFIG_UPDATE_NOTIFIER=false
ENV NPM_CONFIG_FUND=false
ENV NPM_CONFIG_AUDIT=false

# Paquets systeme : python3 (+ venv/pip pour les backends), git, outils de
# build usuels des projets (pas de compilateur lourd : les projets purement
# natifs sortent du perimetre de la preview).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        git \
        ca-certificates \
        curl \
        procps \
        build-essential \
        libpq-dev \
        libffi-dev \
        libssl-dev \
        zlib1g-dev \
        make \
        g++ \
    ; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /usr/share/doc/* /usr/share/man/*

# python = python3 (les projets appellent uniformement `python`)
RUN set -eux; \
    if [ ! -e /usr/local/bin/python ]; then ln -s /usr/bin/python3 /usr/local/bin/python; fi; \
    python --version; node --version; npm --version; git --version

# Utilisateur non-root a UID/GID fixes. Aligne sur le proprietaire du
# workspace hote (1000 par defaut). /workspace cree et possede par preview
# pour que le montage rw soit exploitable meme si le repertoire hote est vide.
ARG PREVIEW_UID=1000
ARG PREVIEW_GID=1000

# Les images node officielles embarquent deja un utilisateur "node" en
# uid/gid 1000. Plutot que de le supprimer (ce qui casserait la propriete des
# fichiers de l image), on le RENOMME en "preview" quand il occupe l UID
# demande. Aucune suppression de fichier, aucune orpheline.
RUN set -eux; \
    cur_uid_name="$(getent passwd "${PREVIEW_UID}" | cut -d: -f1 || true)"; \
    cur_gid_name="$(getent group "${PREVIEW_GID}" | cut -d: -f1 || true)"; \
    if [ -n "$cur_gid_name" ] && [ "$cur_gid_name" != "preview" ]; then \
        groupmod --new-name preview "$cur_gid_name"; \
    fi; \
    if [ -n "$cur_uid_name" ] && [ "$cur_uid_name" != "preview" ]; then \
        usermod --login preview --home /tmp --shell /bin/bash "$cur_uid_name"; \
    fi; \
    getent group preview >/dev/null 2>&1 || groupadd --gid "${PREVIEW_GID}" preview; \
    getent passwd preview >/dev/null 2>&1 || useradd --uid "${PREVIEW_UID}" --gid "${PREVIEW_GID}" \
            --home-dir /tmp --no-create-home --shell /bin/bash preview; \
    usermod --home /tmp preview; \
    mkdir -p /workspace; \
    chown "${PREVIEW_UID}:${PREVIEW_GID}" /workspace; \
    chmod 0755 /workspace; \
    id preview

# Caches et HOME sous /tmp : seuls chemins inscriptibles sous --read-only.
ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_CONFIG_HOME=/tmp/.config \
    npm_config_cache=/tmp/.npm \
    PIP_CACHE_DIR=/tmp/.pip

WORKDIR /workspace
USER preview

# Garde-fou : l'image ne doit jamais tourner en root. Le build echoue si la
# directive est perdue (le controle est aussi refait par verify-d3.sh).
RUN set -eux; \
    test "$(id -u)" = "${PREVIEW_UID}"; \
    test "$(id -u)" != "0"

CMD ["/bin/bash", "-l"]

# Pas de HEALTHCHECK ici, volontairement.
# Sous --read-only, access(2) echoue en EROFS sur /workspace avant meme de
# consulter les droits du repertoire : un `test -w /workspace` renverrait
# TOUJOURS 1 et le conteneur serait marque unhealthy en permanence. Le
# signal de vivacite reel est le port applicatif, sonde par D10 (HTTP sur
# le port publie) — pas une propriete du systeme de fichiers de l image.
