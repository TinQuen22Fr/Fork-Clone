#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d8.sh — Étape D8 : secrets et masquage.
#   Statique sans privilège ; bout-en-bout (lecture du .env, injection --env
#   masquée, interdiction de monter le .env) vérifié réellement.
# sudo bash deploy/docker/verify-d8.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
SERVER="$ROOTDIR/backend/server.py"
WRAPPER_SRC="$ROOTDIR/deploy/docker/forge-preview"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D8 : secrets et masquage (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

# 1. Lecture du .env filtrée par liste blanche, injection en --env.
for pat in \
  'def _project_env' \
  'PUBLIC_ENV_PREFIXES' \
  'VITE_' \
  'NEXT_PUBLIC_' \
  'def _is_public_env_key' \
  'SECRET_ENV_HINT' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

# 2. Interdiction de monter le .env global de la Forge.
if grep -qE '(^|[[:space:]])-v[[:space:]].*\.env' "$WRAPPER_SRC" 2>/dev/null; then
  ko "wrapper monte un .env (interdit)"
else
  ok "wrapper : aucun montage de .env"
fi
if grep -qF 'docker.sock' "$WRAPPER_SRC" 2>/dev/null; then
  ko "wrapper référence docker.sock (interdit)"
else
  ok "wrapper : aucun socket docker"
fi

# 3. Toute sortie passe par redact_secrets() avant affichage/stockage.
for pat in \
  'def redact_secrets' \
  '***REDACTED***' \
  '_SECRET_ASSIGN' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done
grep -qF -- 'redact_secrets((out' "$RUNTIME" 2>/dev/null \
  && ok "runtime : sortie _docker masquée" || ko "runtime : sortie _docker non masquée"
grep -qF -- 'return redact_secrets(out.stdout' "$RUNTIME" 2>/dev/null \
  && ok "runtime : logs docker masqués" || ko "runtime : logs docker non masqués"
grep -qF -- 'return redact_secrets(fh.read' "$RUNTIME" 2>/dev/null \
  && ok "runtime : logs natifs masqués" || ko "runtime : logs natifs non masqués"

echo "== D8 : bout-en-bout réel =="
export D8_BACKEND="$ROOTDIR/backend"
OUT=$(python3 - <<'PY' 2>&1
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["D8_BACKEND"])
from preview_runtime import PreviewManager, redact_secrets, _is_public_env_key

root = tempfile.mkdtemp()
proj = "verifyd8"
d = Path(root) / proj
d.mkdir(parents=True, exist_ok=True)
(d / ".env").write_text(
    "VITE_API_URL=/api\n"
    "VITE_API_KEY=hunter2secret\n"
    "NEXT_PUBLIC_TITLE=Mon App\n"
    "DATABASE_URL=postgres://u:supersecret@host/db\n"
    "API_KEY=topsecret123\n"
)

m = PreviewManager(root)
env = m._project_env(proj)
assert "VITE_API_URL" in env and env["VITE_API_URL"] == "/api", env
assert "NEXT_PUBLIC_TITLE" in env, env
assert "VITE_API_KEY" not in env, env
assert "API_KEY" not in env, env
assert "DATABASE_URL" not in env, env
print("ENV_KEYS", " ".join(sorted(env)))

txt = "API_KEY=topsecret123\nDATABASE_URL=postgres://u:supersecret@host/db\nVITE_API_KEY=hunter2secret\n"
r = redact_secrets(txt)
assert "topsecret123" not in r, r
assert "supersecret" not in r, r
assert "hunter2secret" not in r, r
print("REDACTED", "oui" if "***REDACTED***" in r else "non")
print("PUBLIC_FILTER", "ok" if (_is_public_env_key("VITE_API_URL") and not _is_public_env_key("VITE_API_KEY")) else "ko")
PY
) || true
printf '%s\n' "$OUT" | grep -q '^ENV_KEYS NEXT_PUBLIC_TITLE VITE_API_URL$' \
  && ok "injection : seules les clés publiques retenues" || ko "injection : clés inattendues ($OUT)"
printf '%s\n' "$OUT" | grep -q '^REDACTED oui$' \
  && ok "masquage : valeurs sensibles remplacées" || ko "masquage : valeurs sensibles non masquées"
printf '%s\n' "$OUT" | grep -q '^PUBLIC_FILTER ok$' \
  && ok "filtre : clé publique acceptée, clé secrète refusée" || ko "filtre : comportement inattendu"

# 5. Un .env contenant API_KEY=xxx ne doit jamais apparaître en clair dans les logs.
printf '%s\n' "$OUT" | grep -q 'topsecret123' \
  && ko "fuite : API_KEY en clair dans la sortie" \
  || ok "aucune fuite de secret en clair (API_KEY)"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D8 conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D8 conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D8 NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
