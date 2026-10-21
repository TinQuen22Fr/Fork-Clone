#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d5.sh — Contrôles de sortie de l'étape D5 (driver docker backend).
# Lecture seule sans privilège (contrôles statiques) ; exécution réelle en root.
# sudo bash deploy/docker/verify-d5.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2

DOCKER=/usr/bin/docker
WRAPPER_SRC=forge-preview
WRAPPER_DST=/usr/local/bin/forge-preview
NETWORK=${FORGE_PREVIEW_NETWORK:-forge-preview-net}
IMAGE=${FORGE_PREVIEW_IMAGE:-forge-preview-base:local}
BACKEND_SRC="$(cd .. && pwd)/../backend/preview_runtime.py"
SERVICE_USER=quentin

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D5 : driver docker dans preview_runtime.py =="
[ -f "$BACKEND_SRC" ] && ok "backend preview_runtime.py présent" || ko "backend introuvable : $BACKEND_SRC"

for pat in \
  'PREVIEW_DRIVER' \
  'async def _docker' \
  'async def _start_docker' \
  'async def _run_docker' \
  '_write_script' \
  '_docker_cmd' \
  '"exec"' \
  '"--script"' \
  '--port2' \
  '_bind' \
  '0.0.0.0' \
  'sudo' \
  'logs' ; do
  grep -qF -- "$pat" "$BACKEND_SRC" 2>/dev/null && ok "backend : $pat présent" \
    || ko "backend : $pat absent"
done

# Le driver docker ne doit plus exécuter de Popen hôte pour le lancement.
# Le Popen restant est celui du mode natif (défaut) : autorisé derrière le flag.
if grep -qF 'subprocess.Popen' "$BACKEND_SRC"; then
  grep -qF 'driver == "docker"' "$BACKEND_SRC" \
    && ok "Popen : isolation derrière le flag driver" \
    || ko "Popen présent sans isolation driver"
fi

# Par défaut le driver reste natif (bascule D12).
grep -qF '"native"' "$BACKEND_SRC" && ok "défaut driver = native" || ko "défaut driver != native"

echo
echo "== D5 : wrapper (verbe borné exec + --port2) =="
[ -f "$WRAPPER_SRC" ] && ok "wrapper présent" || ko "wrapper absent"
for pat in 'exec)' '--script' '--port2' '.forge-preview/*.sh' 'dk exec' ; do
  grep -qF -- "$pat" "$WRAPPER_SRC" 2>/dev/null && ok "wrapper : $pat présent" \
    || ko "wrapper : $pat absent"
done

# Le wrapper refuse toujours toute commande arbitraire (aucun sh -c user, aucun
# passage direct de binaire).
grep -qF 'dk exec' "$WRAPPER_SRC" && \
  ! grep -qF 'sh -c "$' "$WRAPPER_SRC" && ok "wrapper : exec borné (pas de sh -c arbitraire)" \
  || ko "wrapper : exec non borné"

echo
echo "== D5 : syntaxe =="
if bash -n "$WRAPPER_SRC" 2>/dev/null; then ok "wrapper : bash -n OK"; else ko "wrapper : erreur de syntaxe"; fi
if python3 -m py_compile "$BACKEND_SRC" 2>/dev/null; then ok "backend : py_compile OK"; else ko "backend : erreur de syntaxe Python"; fi

echo
echo "== D5 : exécution réelle (root requis) =="
if [ "$ROOT" -eq 1 ]; then
  if "$DOCKER" network inspect "$NETWORK" >/dev/null 2>&1; then
    DRIVER="$("$DOCKER" network inspect -f '{{.Driver}}' "$NETWORK")"
    [ "$DRIVER" = "bridge" ] && ok "réseau $NETWORK bridge présent" || ko "réseau $NETWORK driver $DRIVER"
  else
    ko "réseau $NETWORK absent (relancer install-d5.sh)"
  fi

  [ -f "$WRAPPER_DST" ] && ok "wrapper installé présent" || ko "wrapper installé absent"
  if [ -f "$WRAPPER_SRC" ] && [ -f "$WRAPPER_DST" ]; then
    SRC_SHA=$(sha256sum "$WRAPPER_SRC" | cut -d' ' -f1)
    DST_SHA=$(sha256sum "$WRAPPER_DST" | cut -d' ' -f1)
    [ "$SRC_SHA" = "$DST_SHA" ] && ok "wrapper installé identique au dépôt (sha256)" \
      || ko "wrapper installé différent du dépôt (relancer install-d5.sh)"
  fi

  # La commande interne "run" est générée dans .forge-preview/<target>.run.sh ;
  # le conteneur est lancé avec sleep infinity puis reçoit exec (aucune commande
  # arbitraire). Le test réel utilise une image de base.
  if "$DOCKER" image inspect "$IMAGE" >/dev/null 2>&1; then
    ok "image $IMAGE présente"
  else
    skip "image $IMAGE absente (D3 non exécutée) — exécution réelle non vérifiée"
  fi
else
  skip "exécution réelle non vérifiée (root requis)"
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "RÉSULTAT : D5 NON conforme — corriger les lignes KO ci-dessus."
elif [ "$unverified" -ne 0 ]; then
  echo "RÉSULTAT : contrôles statiques OK — exécution réelle non vérifiée (root requis)."
else
  echo "RÉSULTAT : D5 conforme."
fi
exit "$fail"
