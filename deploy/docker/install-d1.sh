#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# install-d1.sh — Étape D1 : installation et durcissement du moteur Docker.
#                  Docker ne sert QUE de sandbox pour les previews de projets.
#
#   sudo bash deploy/docker/install-d1.sh
#
# Idempotent : peut être relancé sans effet de bord.
# Ne démarre AUCUN conteneur de projet : uniquement la vérification hello-world.
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DAEMON_JSON="/etc/docker/daemon.json"
SYSCTL_CONF="/etc/sysctl.d/99-forge-docker.conf"
MODULES_CONF="/etc/modules-load.d/forge-docker.conf"
DOCKER_KEYRING="/etc/apt/keyrings/docker.asc"
DOCKER_LIST="/etc/apt/sources.list.d/docker.list"

log()  { printf '\033[1;34m[D1]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[D1]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[D1]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "à lancer en root : sudo bash deploy/docker/install-d1.sh"

# ---------------------------------------------------------------------------
# 1. Prérequis noyau
# ---------------------------------------------------------------------------
log "module overlay …"
modprobe overlay 2>/dev/null || warn "modprobe overlay indisponible (noyau sans module ?)"
log "module br_netfilter …"
modprobe br_netfilter 2>/dev/null || warn "modprobe br_netfilter indisponible"

install -m 0644 "$SRC_DIR/forge-docker-modules.conf" "$MODULES_CONF"
install -m 0644 "$SRC_DIR/99-forge-docker.conf" "$SYSCTL_CONF"
sysctl -q --system || warn "sysctl --system a signalé une erreur"

# ---------------------------------------------------------------------------
# 2. Dépôt officiel Docker
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  log "docker déjà présent : $(docker --version 2>/dev/null || echo version inconnue)"
else
  log "ajout du dépôt officiel Docker…"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg

  install -m 0755 -d /etc/apt/keyrings
  if [ ! -s "$DOCKER_KEYRING" ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o "$DOCKER_KEYRING"
    chmod a+r "$DOCKER_KEYRING"
  fi

  . /etc/os-release
  ARCH="$(dpkg --print-architecture)"
  echo "deb [arch=${ARCH} signed-by=${DOCKER_KEYRING}] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > "$DOCKER_LIST"

  apt-get update -qq
  log "installation docker-ce + compose + buildx…"
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
                         docker-buildx-plugin docker-compose-plugin
fi

# ---------------------------------------------------------------------------
# 3. daemon.json (durcissement + réseau dédié + logs bornés + ulimits)
# ---------------------------------------------------------------------------
if [ -s "$DAEMON_JSON" ] && ! cmp -s "$SRC_DIR/daemon.json" "$DAEMON_JSON"; then
  BACKUP="${DAEMON_JSON}.bak.$(date +%Y%m%d%H%M%S)"
  cp -a "$DAEMON_JSON" "$BACKUP"
  warn "daemon.json existant sauvegardé : $BACKUP"
fi
install -D -m 0644 "$SRC_DIR/daemon.json" "$DAEMON_JSON"

# ---------------------------------------------------------------------------
# 4. Service
# ---------------------------------------------------------------------------
log "activation du service docker…"
systemctl enable docker.service >/dev/null 2>&1 || warn "enable docker.service a échoué"
systemctl restart docker.service || die "docker.service ne démarre pas — voir: journalctl -u docker -n 50"

for _ in $(seq 1 30); do
  docker info >/dev/null 2>&1 && break
  sleep 1
done
docker info >/dev/null 2>&1 || die "le démon Docker ne répond pas après 30 s"

# ---------------------------------------------------------------------------
# 5. Vérifications
# ---------------------------------------------------------------------------
log "docker info (extraits) :"
docker info --format '  version serveur : {{.ServerVersion}}' 2>/dev/null || true
docker info --format '  cgroup          : {{.CgroupVersion}} (driver {{.CgroupDriver}})' 2>/dev/null || true
docker info --format '  storage driver  : {{.Driver}}' 2>/dev/null || true
docker info --format '  live-restore    : {{.LiveRestoreEnabled}}' 2>/dev/null || true
docker info --format '  data-root       : {{.DockerRootDir}}' 2>/dev/null || true

log "vérification d'exécution (hello-world, supprimé aussitôt)…"
if docker run --rm hello-world >/dev/null 2>&1; then
  log "hello-world : OK"
else
  die "hello-world a échoué — le moteur ne peut pas exécuter de conteneur"
fi

# ---------------------------------------------------------------------------
# 6. Garde-fou : le backend ne doit PAS accéder au socket Docker en direct
# ---------------------------------------------------------------------------
if id -nG quentin 2>/dev/null | grep -qw docker; then
  warn "ATTENTION : l'utilisateur quentin appartient au groupe 'docker' (= root de fait)."
  warn "Retirer avec : sudo gpasswd -d quentin docker"
else
  log "groupe docker : quentin n'en est pas membre (conforme à D2)."
fi

log "D1 terminée."
