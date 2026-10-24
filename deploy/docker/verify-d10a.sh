#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d10a.sh — Étape D10a : backoff entre deux démarrages d'un même projet.
#   Statique + unitaire, sans privilège ni conteneur (aucun appel Docker).
# bash deploy/docker/verify-d10a.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

echo "== D10a : backoff entre deux démarrages d'un même projet (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

for pat in \
  'RESTART_BACKOFF' \
  'self._started_at' \
  'def backoff_remaining' \
  'def mark_started' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null \
    && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

# Le refus doit être posé en tête de start(), avant tout lancement.
grep -qF 'backoff = self.backoff_remaining(project)' "$RUNTIME" 2>/dev/null \
  && ok "start() : contrôle backoff en tête" || ko "start() : contrôle backoff absent"

echo
echo "== D10a : comportement (unitaire, sans conteneur) =="
export D10A_BACKEND="$ROOTDIR/backend"
OUT=$(PREVIEW_DRIVER=docker python3 - <<'PY' 2>&1
import asyncio, os, sys, time
sys.path.insert(0, os.environ["D10A_BACKEND"])
from preview_runtime import PreviewManager, RESTART_BACKOFF, PHASE_ERROR

async def main():
    m = PreviewManager("/tmp/.verify-d10a")
    p = "demo"

    # 1. Avant tout démarrage : aucun backoff restant.
    b0 = m.backoff_remaining(p)
    print("BACKOFF0", b0)
    assert b0 == 0.0, f"backoff initial inattendu : {b0}"

    # 2. Après un premier démarrage : backoff actif.
    m.mark_started(p)
    b1 = m.backoff_remaining(p)
    print("BACKOFF1", round(b1, 1))
    assert b1 > 0, "backoff non actif après mark_started"
    assert b1 <= RESTART_BACKOFF, f"backoff > RESTART_BACKOFF : {b1}"

    # 3. Second start immédiat -> refus phase=error + mention backoff.
    r2 = await m.start(p, 8100)
    print("START2_PHASE", r2.get("phase"))
    print("START2_MSG", r2.get("message", ""))
    assert r2.get("phase") == PHASE_ERROR, f"second start non refusé : {r2.get('phase')}"
    msg = r2.get("message", "").lower()
    assert "trop rapproche" in msg and "attendre" in msg, f"message sans backoff : {msg}"

    # 4. Après écoulement du délai : backoff nul, démarrage autorisé (chemin débloqué).
    m._started_at[p] = time.time() - RESTART_BACKOFF - 1
    b2 = m.backoff_remaining(p)
    print("BACKOFF2", b2)
    assert b2 == 0.0, f"backoff non écoulé : {b2}"

    print("D10A_OK")

asyncio.run(main())
PY
) || true

printf '%s\n' "$OUT" | grep -q '^BACKOFF0 0\.0$' \
  && ok "backoff initial nul" || ko "backoff initial non nul"
printf '%s\n' "$OUT" | grep -qE '^BACKOFF1 (2[0-9](\.[0-9]+)?|[0-9]+(\.[0-9]+)?)$' \
  && ok "backoff actif après premier démarrage (<= 30s)" || ko "backoff non actif après mark_started"
printf '%s\n' "$OUT" | grep -q '^START2_PHASE error$' \
  && ok "second start rapproché refusé (phase=error)" || ko "second start rapproché non refusé"
printf '%s\n' "$OUT" | grep -qiE 'trop rapproche.*attendre' \
  && ok "message de refus mentionne le backoff" || ko "message sans mention backoff"
printf '%s\n' "$OUT" | grep -q '^BACKOFF2 0\.0$' \
  && ok "backoff écoulé après 30s" || ko "backoff non écoulé après 30s"
printf '%s\n' "$OUT" | grep -q '^D10A_OK$' \
  && ok "assertions unitaires intégralement passées" || ko "assertions unitaires en échec"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D10a conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D10a conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D10a NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
