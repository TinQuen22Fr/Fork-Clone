#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d13b.sh — Correction B : câblage des logs métier vers l'interface.
#   En mode docker, le process lancé via `docker exec --detach` n'écrit pas
#   dans `docker logs` (PID 1 = sleep infinity). La recette exige que la
#   sortie soit redirigée vers le journal partagé sous .forge-preview/ et
#   que logs() relise ce fichier côté hôte (source de vérité), `docker logs`
#   ne restant qu'un secours. Statique + unitaire, sans privilège ni conteneur.
# bash deploy/docker/verify-d13b.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

echo "== D13b : câblage des logs vers l'interface (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

# Corps de _script_run (de la signature jusqu'à la prochaine définition).
RUNBODY=$(sed -n '/^    def _script_run(/,/^    [^ ]/p' "$RUNTIME")
LOGSBODY=$(sed -n '/^    def logs(/,/^    [^ ]/p' "$RUNTIME")

# 1. Le script de lancement redirige stdout/stderr vers un journal.
printf '%s\n' "$RUNBODY" | grep -qE '>>|tee' \
  && ok "_script_run : redirection sortie vers journal (>> / tee)" \
  || ko "_script_run : aucune redirection de sortie (journal non câblé)"

# 2. Le journal cible le répertoire partagé .forge-preview (monté hôte/conteneur).
printf '%s\n' "$RUNBODY" | grep -qE '\.forge-preview|FORGE_PREVIEW_LOG|log_path' \
  && ok "_script_run : journal sous .forge-preview (partagé)" \
  || ko "_script_run : journal hors du répertoire partagé"

# 3. logs() lit le journal local AVANT tout repli docker (fichier = source de vérité).
LOGIDX=$(printf '%s\n' "$LOGSBODY" | grep -n 'log_path' | head -1 | cut -d: -f1)
DOCKIDX=$(printf '%s\n' "$LOGSBODY" | grep -nE 'docker|sudo -n|wrapper' | head -1 | cut -d: -f1)
if [ -n "$LOGIDX" ] && { [ -z "$DOCKIDX" ] || [ "$LOGIDX" -lt "$DOCKIDX" ]; }; then
  ok "logs() : lecture du journal local prioritaire sur docker logs"
else
  ko "logs() : journal local non prioritaire (docker logs encore seule source)"
fi

# 4. Le repli docker logs reste conservé en secours.
printf '%s\n' "$LOGSBODY" | grep -qE 'docker|wrapper' \
  && ok "logs() : repli docker logs conservé (secours)" \
  || ko "logs() : repli docker logs disparu"

# 5. La taille du journal est bornée (seek/tail/max_bytes).
printf '%s\n' "$LOGSBODY" | grep -qE 'seek|tail|max_bytes' \
  && ok "logs() : troncature bornée présente (seek/tail/max_bytes)" \
  || ko "logs() : aucune troncature (croissance illimitée)"

echo
echo "== D13b : comportement (unitaire, sans conteneur) =="
export D13B_BACKEND="$ROOTDIR/backend"
OUT=$(PREVIEW_DRIVER=docker python3 - <<'PY' 2>&1
import os, sys, tempfile
sys.path.insert(0, os.environ["D13B_BACKEND"])
from preview_runtime import PreviewManager

tmp = tempfile.mkdtemp(prefix=".verify-d13b-")
m = PreviewManager(tmp)
os.makedirs(m.state_dir, exist_ok=True)

# 1. Relire un journal pré-écrit, en mode docker, sans dépendre du wrapper sudo.
p = m.log_path("demo", "app")
p.write_text("vite dev server running\nlocal: http://127.0.0.1:8091/\n")
out = m.logs("demo", "app")
print("LOGS_REL", "vite dev server running" in out)
assert "vite dev server running" in out, "logs() docker doit relire le fichier local"

# 2. Masquage des secrets conservé sur le journal relu.
p2 = m.log_path("demo", "web")
p2.write_text("token sk-123456789012345678901234567890\n")
out2 = m.logs("demo", "web")
print("LOGS_REDACT", ("sk-123456" not in out2) and ("***REDACTED***" in out2))
assert "sk-123456" not in out2, "secret non masqué dans le journal relu"

# 3. Troncature bornée : un gros journal reste limité à max_bytes.
p3 = m.log_path("demo", "app")
p3.write_text("x" * 200000)
out3 = m.logs("demo", "app", max_bytes=1000)
print("LOGS_TRUNC", len(out3))
assert len(out3) <= 1100, f"journal non tronqué : {len(out3)}"

print("D13B_OK")
PY
) || true

printf '%s\n' "$OUT" | grep -q '^LOGS_REL True$' \
  && ok "logs docker relit le journal local (contenu remonté)" || ko "logs docker ne relit pas le journal local"
printf '%s\n' "$OUT" | grep -q '^LOGS_REDACT True$' \
  && ok "masquage des secrets conservé sur le journal" || ko "masquage des secrets en échec"
TRUNC=$(printf '%s\n' "$OUT" | sed -n 's/^LOGS_TRUNC //p' | tail -1)
if [ -n "$TRUNC" ] && [ "$TRUNC" -le 1100 ] 2>/dev/null; then
  ok "troncature bornée (taille relue <= 1100)"
else
  ko "troncature absente ou inefficace"
fi
printf '%s\n' "$OUT" | grep -q '^D13B_OK$' \
  && ok "assertions unitaires intégralement passées" || ko "assertions unitaires en échec"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D13b conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D13b conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D13b NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
