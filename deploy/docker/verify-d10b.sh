#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d10b.sh — Étape D10b : compteur d'échecs consécutifs → désactivation.
#   Statique + unitaire, sans privilège ni conteneur (aucun appel Docker).
# bash deploy/docker/verify-d10b.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

echo "== D10b : compteur anti-boucle 3 échecs / 5 min → désactivation (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

for pat in \
  'RESTART_WINDOW' \
  'RESTART_MAX_FAILURES' \
  'self._disabled' \
  'self.failures' \
  'def _mark_failure' \
  'def _clear_failures' \
  'def is_disabled' \
  'def disable' \
  'def enable' \
  'def _record_failure' \
  'def _record_success' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null \
    && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

# Le refus d'une preview désactivée doit être posé en tête de start().
grep -qF 'if self.is_disabled(project) and not restart:' "$RUNTIME" 2>/dev/null \
  && ok "start() : refus preview désactivée (sauf restart=True)" \
  || ko "start() : contrôle is_disabled absent"
grep -qF 'if restart:' "$RUNTIME" 2>/dev/null \
  && grep -qF 'self.enable(project)' "$RUNTIME" 2>/dev/null \
  && ok "start(restart=True) : réautorise la preview" \
  || ko "start(restart=True) : réautorisation absente"

echo
echo "== D10b : comportement (unitaire, sans conteneur) =="
export D10B_BACKEND="$ROOTDIR/backend"
OUT=$(PREVIEW_DRIVER=docker python3 - <<'PY' 2>&1
import asyncio, os, sys, time
sys.path.insert(0, os.environ["D10B_BACKEND"])
from preview_runtime import PreviewManager, PreviewProcess, RESTART_WINDOW, RESTART_MAX_FAILURES

def fresh(m, project="demo", target="app", port=8200):
    st = PreviewProcess(project, target, port, m.log_path(project, target))
    m._procs.setdefault(project, {})[target] = st
    return st

async def main():
    m = PreviewManager("/tmp/.verify-d10b")
    p = "demo"

    # 1. Compteur simple : 2 échecs → pas encore désactivé, 3e → désactivé.
    st = fresh(m, p)
    for i in range(1, RESTART_MAX_FAILURES + 1):
        do_disable = m._mark_failure(st)
        print("MARK", i, st.failures, do_disable)
    assert st.failures == RESTART_MAX_FAILURES, "compteur incorrect"
    # _mark_failure renvoie True au 3e échec (seuil atteint).
    # _record_failure consomme ce True pour désactiver.
    st2 = fresh(m, "demo2")
    for _ in range(RESTART_MAX_FAILURES):
        m._record_failure(st2, "echec")
    print("DISABLED_AFTER", m.is_disabled("demo2"))
    assert m.is_disabled("demo2"), "preview non désactivée après 3 échecs"
    assert "desactivee" in st2.message, "message sans mention désactivation"

    # 2. Fenêtre glissante : au-delà de RESTART_WINDOW, le compteur repart de 0.
    st3 = fresh(m, "demo3")
    m._record_failure(st3, "echec1")
    m._record_failure(st3, "echec2")
    st3.first_failure_at = time.time() - RESTART_WINDOW - 1
    do_disable = m._mark_failure(st3)  # 3e échec mais fenêtre expirée
    print("WINDOW_RESET_FAILURES", st3.failures)
    print("WINDOW_RESET_DISABLE", do_disable)
    assert st3.failures == 1, "compteur non remis à zéro après expiration fenêtre"
    assert do_disable is False, "désactivation alors que la fenêtre a expiré"
    assert not m.is_disabled("demo3"), "preview désactivée à tort (fenêtre expirée)"

    # 3. Succès réarme le compteur.
    st4 = fresh(m, "demo4")
    m._record_failure(st4, "echec1")
    m._record_failure(st4, "echec2")
    m._record_success(st4)
    print("SUCCESS_RESET_FAILURES", st4.failures)
    assert st4.failures == 0, "compteur non réarmé après succès"
    assert st4.first_failure_at == 0.0, "first_failure_at non remis à zéro"

    # 4. enable() réautorise une preview désactivée.
    m.disable("demo5")
    assert m.is_disabled("demo5")
    m.enable("demo5")
    print("ENABLED", m.is_disabled("demo5"))
    assert not m.is_disabled("demo5"), "enable() inefficace"

    print("D10B_OK")

asyncio.run(main())
PY
) || true

printf '%s\n' "$OUT" | grep -qE '^MARK 3 3 True$' \
  && ok "3e échec consécutif atteint le seuil" || ko "seuil non atteint au 3e échec"
printf '%s\n' "$OUT" | grep -q '^DISABLED_AFTER True$' \
  && ok "preview désactivée après 3 échecs (_record_failure)" \
  || ko "preview non désactivée après 3 échecs"
printf '%s\n' "$OUT" | grep -q '^WINDOW_RESET_FAILURES 1$' \
  && ok "fenêtre glissante : compteur remis à zéro après 5 min" \
  || ko "compteur non remis à zéro après expiration"
printf '%s\n' "$OUT" | grep -q '^WINDOW_RESET_DISABLE False$' \
  && ok "pas de désactivation après expiration de fenêtre" \
  || ko "désactivation à tort après expiration de fenêtre"
printf '%s\n' "$OUT" | grep -q '^SUCCESS_RESET_FAILURES 0$' \
  && ok "succès réarme le compteur d'échecs" || ko "compteur non réarmé après succès"
printf '%s\n' "$OUT" | grep -q '^ENABLED False$' \
  && ok "enable() réautorise une preview désactivée" || ko "enable() inefficace"
printf '%s\n' "$OUT" | grep -q '^D10B_OK$' \
  && ok "assertions unitaires intégralement passées" || ko "assertions unitaires en échec"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D10b conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D10b conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D10b NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
