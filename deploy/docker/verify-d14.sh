#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d14.sh — Correction D14 : venv Python conteneur HORS du workspace.
#   Le workspace reste l'atelier de dev (versionne) ; le venv du conteneur
#   vit dans /forge-state (monte depuis l'etat du projet). Git ne le voit pas.
#   Statique + unitaire, sans privilege ni conteneur.
# bash deploy/docker/verify-d14.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
WRAPPER="$ROOTDIR/deploy/docker/forge-preview"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

echo "== D14 : venv conteneur hors workspace (/forge-state) — statique =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"
[ -f "$WRAPPER" ] && ok "forge-preview présent" || ko "forge-preview absent"

# 1. Source unique de vérité pour le nom du venv.
grep -qE 'def _venv_dir' "$RUNTIME" 2>/dev/null \
  && ok "_venv_dir() présente" || ko "_venv_dir() absente"

# 2. En docker, le venv est HORS workspace (jamais .venv ni .venv-docker dans le projet).
if grep -qE 'd / "\.venv-docker"' "$RUNTIME" 2>/dev/null; then
  ko "référence .venv-docker résiduelle (doit être /forge-state)"
else
  ok "aucun .venv-docker résiduel"
fi
grep -qE 'self\.state_root / project / "venv"' "$RUNTIME" 2>/dev/null \
  && ok "venv docker = state_root/<projet>/venv" || ko "venv docker non résolu vers state_root/<projet>/venv"

# 3. state_root pilotable par env (même racine que le wrapper).
grep -qE 'FORGE_PREVIEW_STATE_ROOT' "$RUNTIME" 2>/dev/null \
  && ok "runtime : FORGE_PREVIEW_STATE_ROOT pris en compte" || ko "runtime : FORGE_PREVIEW_STATE_ROOT absent"

# 4. _docker_cmd convertit le venv conteneur en /forge-state/<rel>.
grep -qE '"/forge-state/" \+ rel\.as_posix\(\)' "$RUNTIME" 2>/dev/null \
  && ok "_docker_cmd : conversion vers /forge-state/<rel>" || ko "_docker_cmd : conversion /forge-state absente"

# 5. Installation docker : venv --clear dans /forge-state.
grep -qE 'python3", "-m", "venv", "--clear", "/forge-state/venv"' "$RUNTIME" 2>/dev/null \
  && ok "création docker : venv --clear /forge-state/venv" || ko "création docker /forge-state/venv absente"

# 6. Pip docker via le venv conteneur.
grep -qE '"/forge-state/venv/bin/python", "-m", "pip"' "$RUNTIME" 2>/dev/null \
  && ok "pip docker via /forge-state/venv/bin/python" || ko "pip docker hors /forge-state"

# 7. Marqueur posé via _venv_dir (donc /forge-state en docker, .venv en natif).
grep -qE 'marker = self\._venv_dir\(Path\(plan\["cwd"\]\)\) / "\.forge-installed"' "$RUNTIME" 2>/dev/null \
  && ok "marqueur d'installation via _venv_dir" || ko "marqueur d'installation non centralisé"

# 8. Wrapper : montage de l'état du projet sur /forge-state.
grep -qE 'FORGE_PREVIEW_STATE_ROOT' "$WRAPPER" 2>/dev/null \
  && ok "wrapper : FORGE_PREVIEW_STATE_ROOT pris en compte" || ko "wrapper : FORGE_PREVIEW_STATE_ROOT absent"
grep -qE '\-v "\$\{STATEDIR\}:/forge-state:rw"' "$WRAPPER" 2>/dev/null \
  && ok "wrapper : -v STATEDIR:/forge-state:rw" || ko "wrapper : montage /forge-state absent"
grep -qE 'chown "\$\{_ws_uid\}:\$\{_ws_gid\}" "\$STATEDIR"' "$WRAPPER" 2>/dev/null \
  && ok "wrapper : propriété du dossier d'état alignée sur le workspace" || ko "wrapper : alignement de propriété absent"

# 9. Mode natif inchangé (.venv dans le dossier projet).
grep -qE '"-m", "venv", "\.venv"' "$RUNTIME" 2>/dev/null \
  && ok "mode natif inchangé : venv .venv" || ko "mode natif : venv .venv absent (régression)"

echo
echo "== D14 : comportement (unitaire, résolution venv + docker_cmd) =="
export D14_BACKEND="$ROOTDIR/backend"
OUT=$(python3 - <<'PY' 2>&1
import os, sys
sys.path.insert(0, os.environ["D14_BACKEND"])
from pathlib import Path
from preview_runtime import PreviewManager

os.environ["PREVIEW_DRIVER"] = "docker"
md = PreviewManager("/var/www/forge/workspace")
assert md.driver == "docker"
venv = md._venv_dir(Path("/var/www/forge/workspace/p1/backend"))
assert venv == Path("/var/lib/forge-preview/p1/venv"), venv
print("VENV_DOCKER", venv)
assert md._docker_cmd([str(venv / "bin/uvicorn"), "app:app"], "p1") == \
    ["/forge-state/venv/bin/uvicorn", "app:app"]
assert md._docker_cmd(["/var/www/forge/workspace/p1/backend/main.py"], "p1") == \
    ["/workspace/backend/main.py"]
assert md._docker_cmd(["/usr/bin/python3"], "p1") == ["python3"]

os.environ["PREVIEW_DRIVER"] = "native"
mn = PreviewManager("/var/www/forge/workspace")
assert mn.driver == "native"
nvenv = mn._venv_dir(Path("/var/www/forge/workspace/p1/backend"))
assert nvenv == Path("/var/www/forge/workspace/p1/backend/.venv"), nvenv
print("VENV_NATIVE", nvenv)

print("D14_OK")
PY
) || true

printf '%s\n' "$OUT" | grep -q '^VENV_DOCKER /var/lib/forge-preview/p1/venv$' \
  && ok "unitaire : docker -> /var/lib/forge-preview/<projet>/venv" || ko "unitaire : venv docker KO"
printf '%s\n' "$OUT" | grep -q '^VENV_NATIVE .*\.venv$' \
  && ok "unitaire : natif -> .venv" || ko "unitaire : natif KO"
printf '%s\n' "$OUT" | grep -q '^D14_OK$' \
  && ok "assertions unitaires intégralement passées" || ko "assertions unitaires en échec"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D14 conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D14 conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D14 NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
