#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d1.sh — Contrôles de sortie de l'étape D1 (lecture seule).
#
#   bash deploy/docker/verify-d1.sh
#
# N'installe rien, ne modifie rien. Code retour 0 = D1 conforme.
#
set -uo pipefail

fail=0
ok()   { printf '  \033[1;32mOK\033[0m   %s\n' "$*"; }
ko()   { printf '  \033[1;31mKO\033[0m   %s\n' "$*"; fail=1; }
info() { printf '       %s\n' "$*"; }

echo "== D1 : prérequis noyau =="
grep -qw cgroup2 /proc/filesystems && ok "cgroup v2 supporté" || ko "cgroup v2 absent du noyau"
[ "$(stat -fc %T /sys/fs/cgroup 2>/dev/null)" = "cgroup2fs" ] \
  && ok "cgroup monté en v2" || ko "cgroup non monté en v2"
if lsmod 2>/dev/null | grep -qw br_netfilter; then ok "module br_netfilter chargé"; elif find /lib/modules/"$(uname -r)" -name "br_netfilter*" -print -quit 2>/dev/null | grep -q .; then info "br_netfilter disponible mais non chargé (D1 le charge)"; else ko "br_netfilter introuvable"; fi
if lsmod 2>/dev/null | grep -qw overlay; then ok "module overlay chargé"; elif find /lib/modules/"$(uname -r)" -name "overlay*" -print -quit 2>/dev/null | grep -q .; then info "overlay disponible mais non chargé (D1 le charge)"; else ko "overlay introuvable"; fi
if [ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)" = "1" ]; then ok "ip_forward actif"; else info "ip_forward=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null) (D1 l active)"; fi

echo "== D1 : moteur =="
if command -v docker >/dev/null 2>&1; then
  ok "docker présent : $(docker --version 2>/dev/null)"
  docker info >/dev/null 2>&1 && ok "démon répond" || ko "démon ne répond pas"
  v="$(docker info --format '{{.CgroupVersion}}' 2>/dev/null)"
  [ "$v" = "2" ] && ok "cgroup v2 côté moteur" || ko "cgroup moteur = $v (attendu 2)"
  d="$(docker info --format '{{.Driver}}' 2>/dev/null)"
  [ "$d" = "overlay2" ] && ok "storage-driver overlay2" || ko "storage-driver = $d (attendu overlay2)"
  lr="$(docker info --format '{{.LiveRestoreEnabled}}' 2>/dev/null)"
  [ "$lr" = "true" ] && ok "live-restore actif" || ko "live-restore = $lr (attendu true)"
else
  ko "docker absent"
fi

echo "== D1 : configuration =="
if [ -f /etc/docker/daemon.json ]; then
  ok "/etc/docker/daemon.json présent"
  if command -v jq >/dev/null 2>&1; then
    jq -e . /etc/docker/daemon.json >/dev/null 2>&1 && ok "JSON valide" || ko "JSON invalide"
    jq -e '.iptables == true and ."live-restore" == true' /etc/docker/daemon.json >/dev/null 2>&1 \
      && ok "clés durcissement présentes" || ko "clés durcissement manquantes"
    if jq -e 'has("no-new-privileges")' /etc/docker/daemon.json >/dev/null 2>&1; then
      ko "clé interdite 'no-new-privileges' dans daemon.json (dockerd refusera de démarrer)"
    else
      ok "aucune clé invalide dans daemon.json"
    fi
  else
    info "jq absent : validation JSON non effectuée"
  fi
else
  ko "/etc/docker/daemon.json absent"
fi
[ -f /etc/sysctl.d/99-forge-docker.conf ] && ok "sysctl persisté" || ko "sysctl non persisté"
[ -f /etc/modules-load.d/forge-docker.conf ] && ok "modules persistés" || ko "modules non persistés"

echo "== D1 : isolation de l'accès =="
if command -v systemctl >/dev/null 2>&1; then
  systemctl is-enabled --quiet docker.service 2>/dev/null && ok "docker.service activé au boot" || ko "docker.service non activé"
  systemctl is-active --quiet docker.service 2>/dev/null && ok "docker.service actif" || ko "docker.service inactif"
fi
if id -nG quentin 2>/dev/null | grep -qw docker; then
  ko "quentin est membre du groupe docker (interdit — équivaut à root)"
else
  ok "quentin hors du groupe docker"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D1 conforme."
else
  echo "RÉSULTAT : D1 NON conforme — corriger les lignes KO ci-dessus."
fi
exit "$fail"
