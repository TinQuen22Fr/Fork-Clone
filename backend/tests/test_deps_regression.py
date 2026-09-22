"""Regression tests after requirements.txt restoration.

Ensures no import broke and dependency-sensitive features still work:
- /api/health (backend imports OK)
- /api/auth/login + /api/conversations (bcrypt, motor, pyjwt)
- /api/models (anthropic, google-genai imports)
- /api/tts/voices, /api/tts (edge-tts)
- Granular DELETE message
- redact_secrets + _tool_read_file guardrails
- pypdf import
"""
import os
import re
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8001").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@forge.dev"
ADMIN_PASSWORD = "ForgeAdmin2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def h(token):
    return {"Authorization": f"Bearer {token}"}


# --- imports sanity ---
def test_edge_tts_import():
    import edge_tts  # noqa: F401


def test_pypdf_import():
    import pypdf  # noqa: F401


def test_anthropic_import():
    import anthropic  # noqa: F401


def test_google_genai_import():
    import google.genai  # noqa: F401


def test_playwright_import():
    import playwright  # noqa: F401


# --- health / auth / conversations ---
def test_health():
    r = requests.get(f"{API}/health", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("status") in ("ok", "degraded")


def test_login_and_list_conversations(h):
    r = requests.get(f"{API}/conversations", headers=h, timeout=15)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# --- /api/models ---
def test_models_endpoint(h):
    r = requests.get(f"{API}/models", headers=h, timeout=20)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "providers" in d and isinstance(d["providers"], list)
    ids = [p["id"] for p in d["providers"]]
    # Must include claude at minimum (from anthropic import)
    assert "claude" in ids


# --- TTS voices + synth (edge-tts) ---
def test_tts_voices_fr(h):
    r = requests.get(f"{API}/tts/voices", params={"locale": "fr"}, headers=h, timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("provider") == "edge-tts", f"provider={d.get('provider')} (FREETTS_API_KEY must be empty)"
    voices = d.get("voices", [])
    assert len(voices) > 0, f"aucune voix retournee: {d}"
    # Denise-Neural doit être présente
    names = [v.get("short_name") for v in voices]
    assert any("Denise" in n for n in names), f"DeniseNeural absent: {names[:5]}"


def test_tts_synth_returns_mp3(h):
    payload = {"text": "Bonjour la Forge", "voice": "fr-FR-DeniseNeural"}
    r = requests.post(f"{API}/tts", json=payload, headers=h, timeout=90)
    assert r.status_code == 200, f"tts failed: {r.status_code} {r.text[:300]}"
    assert r.headers.get("content-type", "").startswith("audio/mpeg")
    assert r.headers.get("x-tts-provider") == "edge-tts"
    body = r.content
    assert len(body) > 1024, f"audio trop petit: {len(body)} octets"
    # MP3 magic: ID3 tag or 0xFF 0xFB sync
    assert body[:3] == b"ID3" or (body[0] == 0xFF and (body[1] & 0xE0) == 0xE0), \
        f"pas un MP3: {body[:8]!r}"


# --- Granular DELETE message ---
def test_delete_single_message(h):
    # Create conversation
    r = requests.post(f"{API}/conversations", json={"title": "TEST_del_msg"},
                      headers=h, timeout=15)
    assert r.status_code == 200
    conv_id = r.json()["id"]

    # Since we may not have LLM keys, we can't easily create a message via /chat/send.
    # Instead, insert a message directly via a fake path? No -- rely on chat/send which
    # will return 503 if no key. Try it: if it fails, skip.
    files = {"conversation_id": (None, conv_id), "text": (None, "hello test")}
    rs = requests.post(f"{API}/chat/send", files=files, headers=h, timeout=60)
    if rs.status_code != 200:
        pytest.skip(f"chat/send indispo ({rs.status_code}) — impossible de creer un message pour tester DELETE")

    d = rs.json()
    msg_id = d["user_message"]["id"]

    # DELETE it
    r1 = requests.delete(f"{API}/conversations/{conv_id}/messages/{msg_id}",
                         headers=h, timeout=15)
    assert r1.status_code == 200
    assert r1.json().get("ok") is True

    # Second DELETE → 404
    r2 = requests.delete(f"{API}/conversations/{conv_id}/messages/{msg_id}",
                         headers=h, timeout=15)
    assert r2.status_code == 404

    # Cleanup conversation
    requests.delete(f"{API}/conversations/{conv_id}", headers=h, timeout=15)


def test_delete_message_bad_conv_returns_404(h):
    r = requests.delete(f"{API}/conversations/nonexistent/messages/nope",
                        headers=h, timeout=15)
    assert r.status_code == 404


# --- Secrets guardrails ---
def test_redact_secrets_function():
    from backend.server import redact_secrets  # type: ignore
    txt = "sk-ant-oat01-abcdef1234567890 and gh_pat_ABCDEF and secret_key=xyz"
    out = redact_secrets(txt)
    assert "sk-ant-oat01-abcdef1234567890" not in out
    assert "gh_pat_ABCDEF" not in out or "***" in out


def test_tool_read_file_refuses_env():
    from backend.server import _tool_read_file  # type: ignore
    out = _tool_read_file("/app/backend/.env")
    # Must NOT contain actual secret values; either refuses or redacts.
    # The function likely raises or returns a message. Just ensure no obvious secret leak.
    assert "ForgeAdmin" not in out
    # Ideally starts with an error indicator
    lower = out.lower()
    assert ("refus" in lower or "interdit" in lower or "denied" in lower
            or "forbidden" in lower or "not allowed" in lower or "***" in out), \
        f"read_file n'a pas refuse: {out[:300]}"
