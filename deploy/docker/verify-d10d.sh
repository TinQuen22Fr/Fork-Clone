#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d10d.sh — Étape D10d : métriques conteneur (état, CPU/RAM) via
# docker inspect / docker stats, exposées par status() et active_previews().
#   Statique + unitaire (parsing sans conteneur) + bout-en-bout root réel.
# sudo bash deploy/docker/verify-d10d.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"
WRAPPER_SRC="$ROOTDIR/deploy/docker/forge-preview"
WRAPPER_DST=/usr/local/bin/forge-preview

PROJECT=verifyd10d
WSROOT=${FORGE_PREVIEW_WORKSPACE_ROOTS:-/var/www/forge/workspace}
WSROOT=${WSROOT%%:*}
[ -n "$WSROOT" ] && [ -d "$WSROOT" ] || WSROOT=/var/www/forge/workspace
WS="$WSROOT/.verify-d10d.$$"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

echo "== D10d : métriques conteneur CPU/RAM (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

for pat in \
  'def stats' \
  'def active_previews' \
  'def _container_running' \
  'cpu_percent' \
  'mem_usage' \
  'pids' ; do
  grep -qF -- "$pat" "$RUNTIME" 2>/dev/null \
    && ok "runtime présent : $pat" || ko "runtime absent : $pat"
done

grep -qF '"inspect"' "$RUNTIME" 2>/dev/null \
  && ok "runtime : état via docker inspect" || ko "runtime : inspect absent"

grep -qF 'stats)' "$WRAPPER_SRC" 2>/dev/null \
  && ok "wrapper présent : verbe stats" || ko "wrapper : verbe stats absent"
grep -qF 'stats --no-stream' "$WRAPPER_SRC" 2>/dev/null \
  && ok "wrapper : stats --no-stream (pas de boucle)" || ko "wrapper : --no-stream absent"
grep -qF '{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}' "$WRAPPER_SRC" 2>/dev/null \
  && ok "wrapper : format CPUPerc|MemUsage|PIDs" || ko "wrapper : format métriques absent"

echo
echo "== D10d : parsing des métriques (unitaire, sans conteneur) =="
OUT=$(python3 - "$RUNTIME" <<'PY' 2>&1
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("pr", sys.argv[1])
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)
os.environ["PREVIEW_DRIVER"] = "docker"

class FakeProc:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode

def make_run(responses):
    """Responses: dict clé -> (stdout, returncode). Clé = verbe (dernier argv)."""
    def run(argv, *a, **k):
        verb = argv[-2]  # ["sudo","-n",wrapper,<verbe>,<projet>]
        stdout, rc = responses.get(verb, ("", 0))
        return FakeProc(stdout, rc)
    return run

m = pr.PreviewManager("/tmp/.verify-d10d-unit")

# conteneur Running -> inspect OK, stats lu
pr.subprocess.run = make_run({
    "inspect": ('"Running": true', 0),
    "stats": ("0.01%|1.234MiB / 1.944GiB|7", 0),
})
s = m.stats("demo")
assert s["cpu_percent"] == "0.01%", s
assert s["mem_usage"] == "1.234MiB", s
assert s["mem_percent"] == "1.944GiB", s
assert s["pids"] == 7, s

# conteneur stoppé (non supprimé) -> inspect non Running -> None
pr.subprocess.run = make_run({
    "inspect": ('"Running": false', 0),
    "stats": ("0.00%|0B / 0B|0", 0),
})
s = m.stats("demo")
assert s["cpu_percent"] is None and s["pids"] is None and s["mem_usage"] is None, s

# sortie stats vide -> None sans lever
pr.subprocess.run = make_run({
    "inspect": ('"Running": true', 0),
    "stats": ("", 0),
})
s = m.stats("demo")
assert s["cpu_percent"] is None and s["pids"] is None, s

# echec wrapper (inspect) -> None sans lever
def boom(*a, **k):
    raise RuntimeError("x")
pr.subprocess.run = boom
s = m.stats("demo")
assert s["cpu_percent"] is None, s

m2 = pr.PreviewManager("/tmp/.verify-d10d-native")
m2.driver = "native"
s = m2.stats("demo")
assert s["cpu_percent"] is None and s["mem_usage"] is None and s["pids"] is None, s

print("D10D_OK")
PY
)
RC=$?
if [ $RC -eq 0 ] && printf '%s\n' "$OUT" | grep -qF 'D10D_OK'; then
  ok "parsing CPU/RAM/PIDs nominal (docker)"
  ok "parsing sortie vide -> None sans lever"
  ok "parsing échec wrapper -> None sans lever"
  ok "mode natif -> champs None"
  ok "assertions unitaires intégralement passées"
else
  ko "échec unitaire (rc=$RC) : $OUT"
fi

echo
if [ "$ROOT" -ne 1 ]; then
  skip "bout-en-bout réel : root requis (sudo bash deploy/docker/verify-d10d.sh)"
else
  echo "== D10d : bout-en-bout réel (root requis) =="
  cleanup() {
    "$WRAPPER_DST" rm "$PROJECT" >/dev/null 2>&1 || true
    rm -rf "$WS" 2>/dev/null || true
  }
  trap cleanup EXIT

  if ! grep -qF 'stats)' "$WRAPPER_DST" 2>/dev/null; then
    ko "wrapper déployé périmé (verbe stats absent) : exécuter d'abord sudo bash deploy/docker/install-d10.sh"
    exit 1
  fi
  mkdir -p "$WS/$PROJECT" && ok "workspace de test créé" || { ko "workspace non créé"; exit 1; }
  printf '<!doctype html><title>d10d</title><h1>D10d</h1>\n' > "$WS/$PROJECT/index.html"

  "$WRAPPER_DST" start "$PROJECT" --port 8115 --workspace "$WS/$PROJECT" >/dev/null 2>&1 \
    && ok "start (rc=0)" || ko "start en échec"

  ST=$("$WRAPPER_DST" stats "$PROJECT" 2>&1)
  RC=$?
  if [ $RC -eq 0 ] && printf '%s\n' "$ST" | grep -qE '^[0-9.]+%\|[0-9.]+[KMGT]?iB / [0-9.]+[KMGT]?iB\|[0-9]+$'; then
    ok "wrapper stats : CPU/RAM/PIDs (rc=0)"
  else
    ko "wrapper stats en échec (rc=$RC) : $ST"
  fi

  ROUT=$(PREVIEW_DRIVER=docker FORGE_PREVIEW_WRAPPER="$WRAPPER_DST" \
    python3 - "$RUNTIME" "$PROJECT" <<'PY' 2>&1
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("pr", sys.argv[1])
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)
os.environ["PREVIEW_DRIVER"] = "docker"
os.environ["FORGE_PREVIEW_WRAPPER"] = os.environ.get("FORGE_PREVIEW_WRAPPER", "/usr/local/bin/forge-preview")
m = pr.PreviewManager("/var/www/forge/workspace/.verify-d10d-runtime")
s = m.stats(sys.argv[2])
if s["cpu_percent"] is not None and s["mem_usage"] is not None and s["pids"] is not None:
    print("D10D_RUNTIME_OK")
else:
    print("D10D_RUNTIME_FAIL", s)
    sys.exit(1)
PY
)
  RC=$?
  if [ $RC -eq 0 ] && printf '%s\n' "$ROUT" | grep -qF 'D10D_RUNTIME_OK'; then
    ok "runtime stats() : cpu/mem/pids renseignés en mode docker réel"
  else
    ko "runtime stats() en échec (rc=$RC) : $ROUT"
  fi

  "$WRAPPER_DST" stop "$PROJECT" >/dev/null 2>&1
  ROUT2=$(PREVIEW_DRIVER=docker FORGE_PREVIEW_WRAPPER="$WRAPPER_DST" \
    python3 - "$RUNTIME" "$PROJECT" <<'PY' 2>&1
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("pr", sys.argv[1])
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)
os.environ["PREVIEW_DRIVER"] = "docker"
os.environ["FORGE_PREVIEW_WRAPPER"] = os.environ.get("FORGE_PREVIEW_WRAPPER", "/usr/local/bin/forge-preview")
m = pr.PreviewManager("/var/www/forge/workspace/.verify-d10d-runtime")
s = m.stats(sys.argv[2])
if s["cpu_percent"] is None and s["mem_usage"] is None and s["pids"] is None:
    print("D10D_ABSENT_OK")
else:
    print("D10D_ABSENT_FAIL", s)
    sys.exit(1)
PY
)
  RC=$?
  if [ $RC -eq 0 ] && printf '%s\n' "$ROUT2" | grep -qF 'D10D_ABSENT_OK'; then
    ok "runtime stats() après stop : conteneur absent -> None"
  else
    ko "runtime stats() après stop en échec (rc=$RC) : $ROUT2"
  fi
fi

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D10d conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D10d conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D10d NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
