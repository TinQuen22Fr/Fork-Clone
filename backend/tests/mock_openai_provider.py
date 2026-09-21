"""
Faux provider compatible OpenAI, pour valider en sandbox la decouverte
dynamique des modeles, le streaming SSE et la cascade — sans aucune vraie cle.

Lancement : python3 /app/backend/tests/mock_openai_provider.py 9100
Endpoints : GET /v1/models, POST /v1/chat/completions (stream ou non)
Modes de panne pilotes par le nom du modele demande :
  - "mock-429"  -> 429 rate limit
  - "mock-503"  -> 503 indisponible
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MODELS = ["mock-fast-8b", "mock-smart-70b:free", "mock-vision-11b"]


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.path.endswith("/models"):
            return self._json(404, {"error": {"message": "not found"}})
        if "Bearer" not in self.headers.get("Authorization", ""):
            return self._json(401, {"error": {"message": "missing api key"}})
        self._json(200, {
            "object": "list",
            "data": [{"id": m, "object": "model"} for m in MODELS],
        })

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        model = req.get("model", "")

        if "429" in model:
            return self._json(429, {"error": {"message": "rate limit exceeded"}})
        if "503" in model:
            return self._json(503, {"error": {"message": "model unavailable"}})

        last = (req.get("messages") or [{}])[-1].get("content", "")
        if isinstance(last, list):
            last = " ".join(p.get("text", "") for p in last if isinstance(p, dict))
        reply = f"[{model}] MOCK_OK"

        if not req.get("stream"):
            return self._json(200, {
                "id": "chatcmpl-mock",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }],
            })

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for word in reply.split(" "):
            chunk = {"choices": [{"delta": {"content": word + " "}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9100
    print(f"mock openai provider sur http://localhost:{port}/v1")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
