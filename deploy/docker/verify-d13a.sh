#!/usr/bin/env bash
#
# Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
# Logiciel libre sous GNU GPL v3 — voir LICENSE.
#
# verify-d13a.sh — Correction A : healthcheck HTTP (Vite) au lieu du simple test TCP.
#   Statique + unitaire avec serveur HTTP local en mémoire, sans privilège ni conteneur.
# bash deploy/docker/verify-d13a.sh
set -u -o pipefail
cd "$(dirname "$0")" || exit 2
ROOTDIR="$(cd ../.. && pwd)"
RUNTIME="$ROOTDIR/backend/preview_runtime.py"

fail=0
unverified=0
ok() { printf ' \033[1;32mOK\033[0m %s\n' "$*"; }
ko() { printf ' \033[1;31mKO\033[0m %s\n' "$*"; fail=1; }
skip() { printf ' \033[1;33m??\033[0m %s\n' "$*"; unverified=1; }

echo "== D13a : healthcheck HTTP Vite (statique) =="
[ -f "$RUNTIME" ] && ok "preview_runtime.py présent" || ko "preview_runtime.py absent"

# 1. Une sonde HTTP dédiée existe.
grep -qE '^def _http_ready' "$RUNTIME" 2>/dev/null \
  && ok "sonde HTTP présente : _http_ready()" || ko "sonde HTTP _http_ready() absente"

# 2. Timeout explicite dans la signature (pas de connect sans borne).
grep -qE 'def _http_ready\([^)]*timeout' "$RUNTIME" 2>/dev/null \
  && ok "timeout explicite dans la signature de _http_ready()" || ko "timeout explicite absent de _http_ready()"

# 3. Vraie requête HTTP (urllib/http.client) — pas un simple connect TCP.
grep -qE 'urllib|http\.client|HTTPConnection|urlopen' "$RUNTIME" 2>/dev/null \
  && ok "vraie requête HTTP référencée (urllib/http.client)" || ko "aucune requête HTTP référencée"

# 4. Seuil de readiness HTTP : un statut 500 marque le refus.
grep -qE 'status *[<>]=? *500|500 *[<>]' "$RUNTIME" 2>/dev/null \
  && ok "seuil HTTP 500 référencé (>= 500 = refus)" || ko "seuil HTTP 500 absent"

# 5. Le simple test TCP _port_open ne doit plus exister (détection remplacée).
if grep -qE '_port_open' "$RUNTIME" 2>/dev/null; then
  ko "_port_open encore présent (doit être remplacé par _http_ready)"
else
  ok "_port_open supprimé (détection de readiness remplacée par HTTP)"
fi

# 6. La sonde HTTP doit être effectivement appelée (readiness + status).
grep -qE '_http_ready\(' "$RUNTIME" 2>/dev/null \
  && ok "sonde HTTP appelée (boucle de readiness et status)" || ko "sonde HTTP jamais appelée"

echo
echo "== D13a : comportement (unitaire, serveur HTTP local) =="
export D13A_BACKEND="$ROOTDIR/backend"
OUT=$(python3 - <<'PY' 2>&1
import os, sys, threading, http.server, socketserver, time
sys.path.insert(0, os.environ["D13A_BACKEND"])
from preview_runtime import _http_ready

class Handler(http.server.BaseHTTPRequestHandler):
    code = 200
    def do_GET(self):
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a):
        pass

def serve(code, port):
    Handler.code = code
    srv = socketserver.TCPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

# 1. Réponse 200 -> prêt.
srv = serve(200, 18091)
time.sleep(0.2)
r = _http_ready(18091)
print("HTTP_200", r)
assert r is True, "200 doit être prêt"
srv.shutdown(); srv.server_close()

# 2. Réponse 404 (Vite renvoie 404 sur /) -> serveur répond -> prêt.
srv = serve(404, 18092)
time.sleep(0.2)
r = _http_ready(18092)
print("HTTP_404", r)
assert r is True, "404 doit être prêt (serveur qui répond)"
srv.shutdown(); srv.server_close()

# 3. Réponse 500 -> refus.
srv = serve(500, 18093)
time.sleep(0.2)
r = _http_ready(18093)
print("HTTP_500", r)
assert r is False, "500 doit être refusé"
srv.shutdown(); srv.server_close()

# 4. Port fermé -> refus (connexion refusée).
r = _http_ready(18094)
print("CLOSED", r)
assert r is False, "port fermé doit être refusé"

print("D13A_OK")
PY
) || true

printf '%s\n' "$OUT" | grep -q '^HTTP_200 True$' \
  && ok "sonde : 200 -> prêt" || ko "sonde : 200 -> prêt KO"
printf '%s\n' "$OUT" | grep -q '^HTTP_404 True$' \
  && ok "sonde : 404 -> prêt (Vite répond)" || ko "sonde : 404 -> prêt KO"
printf '%s\n' "$OUT" | grep -q '^HTTP_500 False$' \
  && ok "sonde : 500 -> refus" || ko "sonde : 500 -> refus KO"
printf '%s\n' "$OUT" | grep -q '^CLOSED False$' \
  && ok "sonde : port fermé -> refus" || ko "sonde : port fermé -> refus KO"
printf '%s\n' "$OUT" | grep -q '^D13A_OK$' \
  && ok "assertions unitaires intégralement passées" || ko "assertions unitaires en échec"

echo
if [ "$fail" -eq 0 ] && [ "$unverified" -eq 0 ]; then
  echo "RÉSULTAT : D13a conforme."
  exit 0
elif [ "$fail" -eq 0 ]; then
  echo "RÉSULTAT : D13a conforme (partiellement non vérifié)."
  exit 0
else
  echo "RÉSULTAT : D13a NON conforme — corriger les lignes KO ci-dessus."
  exit 1
fi
