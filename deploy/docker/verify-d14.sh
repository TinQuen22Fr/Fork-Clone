#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d14.sh — Correction D14 : venv Python dédié conteneur (.venv-docker).
#   Statique + unitaire, sans privilège ni conteneur.
# bash deploy/docker/verify-d14.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

echo "== D14 : venv Python dédié conteneur (.venv-docker) — statique =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

# 1. Une source unique de vérité pour le nom du venv.
grep -qE 'def _venv_dir' "$RUNTIME" 2>/dev/null \
  && ok "méthode de résolution du venv présente : _venv_dir()" || ko "_venv_dir() absente"

# 2. Le venv docker est bien .venv-docker.
grep -qE '\.venv-docker' "$RUNTIME" 2>/dev/null \
  && ok "nom .venv-docker référencé" || ko ".venv-docker absent"

# 3. _detect_python passe par _venv_dir (plus de .venv hôte en dur dans la détection).
if grep -qE 'venv = d / "\.venv"' "$RUNTIME" 2>/dev/null; then
  ko "détection encore figée sur d/.venv (doit passer par _venv_dir)"
else
  ok "détection découplée : plus de d/.venv en dur"
fi
grep -qE 'venv = self\._venv_dir\(d\)' "$RUNTIME" 2>/dev/null \
  && ok "_detect_python utilise self._venv_dir(d)" || ko "_detect_python n'utilise pas self._venv_dir(d)"

# 4. Installation docker : création du venv conteneur avec --clear.
grep -qE 'python3", "-m", "venv", "--clear", "\.venv-docker"' "$RUNTIME" 2>/dev/null \
  && ok "création docker : venv --clear .venv-docker" || ko "création docker : venv --clear .venv-docker absente"

# 5. Pip docker via le venv conteneur.
grep -qE '\.venv-docker/bin/python", "-m", "pip"' "$RUNTIME" 2>/dev/null \
  && ok "pip docker via .venv-docker/bin/python" || ko "pip docker ne pointe pas sur .venv-docker"

# 6. Marqueur d'installation docker posé dans .venv-docker.
grep -qE 'Path\(plan\["cwd"\]\) / "\.venv-docker" / "\.forge-installed"' "$RUNTIME" 2>/dev/null \
  && ok "marqueur docker : .venv-docker/.forge-installed" || ko "marqueur docker .venv-docker/.forge-installed absent"

# 7. Le mode natif doit rester inchangé (conservation du .venv hôte).
grep -qE '"-m", "venv", "\.venv"' "$RUNTIME" 2>/dev/null \
  && ok "mode natif inchangé : venv .venv" || ko "mode natif : venv .venv absent (régression)"

echo
echo "== D14 : comportement (unitaire, résolution _venv_dir) =="
export D14_BACKEND="$ROOTDIR/backend"
OUT=$(python3 - <<'PY' 2>&1
import os, sys
sys.path.insert(0, os.environ["D14_BACKEND"])
from preview_runtime import PreviewManager

from pathlib import Path

os.environ["PREVIEW_DRIVER"] = "docker"
md = PreviewManager("/tmp/forge-d14")
assert md.driver == "docker"
assert md._venv_dir(Path("/proj/backend")) == Path("/proj/backend/.venv-docker"), "docker -> .venv-docker"
print("VENV_DOCKER", md._venv_dir(Path("/proj/backend")))

os.environ["PREVIEW_DRIVER"] = "native"
mn = PreviewManager("/tmp/forge-d14")
assert mn.driver == "native"
assert mn._venv_dir(Path("/proj/backend")) == Path("/proj/backend/.venv"), "natif -> .venv"
print("VENV_NATIVE", mn._venv_dir(Path("/proj/backend")))

print("D14_OK")
PY
) || true

printf '%s\n' "$OUT" | grep -q '^VENV_DOCKER .*\.venv-docker$' \
  && ok "unitaire : docker -> .venv-docker" || ko "unitaire : docker -> .venv-docker KO"
printf '%s\n' "$OUT" | grep -q '^VENV_NATIVE .*[^d]\.venv$' \
  && ok "unitaire : natif -> .venv" || ko "unitaire : natif -> .venv KO"
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
