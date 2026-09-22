"""TTS bug fixes regression tests (BUG 1/2/3 + non-regression)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fork-clone-1.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@forge.dev"
ADMIN_PASSWORD = "ForgeAdmin2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# Non-regression
def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=10)
    assert r.status_code == 200


def test_conversations_list(auth_headers):
    r = requests.get(f"{BASE_URL}/api/conversations", headers=auth_headers, timeout=15)
    assert r.status_code == 200


def test_tts_voices_locale_fr(auth_headers):
    r = requests.get(f"{BASE_URL}/api/tts/voices?locale=fr", headers=auth_headers, timeout=20)
    assert r.status_code == 200
    data = r.json()
    assert data.get("provider") == "edge-tts"
    voices = data.get("voices") or []
    assert len(voices) > 0
    # At least one French voice
    assert any("fr-FR" in (v.get("short_name") or "") for v in voices)


# BUG 3 — Voice normalization + non-empty audio
@pytest.mark.parametrize("voice_in,expected_out", [
    ("fr-FR-Denise:DragonLatestNeural", "fr-FR-DeniseNeural"),
    ("fr-FR-DeniseNeural", "fr-FR-DeniseNeural"),
    ("fr-FR-CelesteNeural", "fr-FR-CelesteNeural"),
])
def test_tts_voices_valid(auth_headers, voice_in, expected_out):
    r = requests.post(
        f"{BASE_URL}/api/tts",
        headers=auth_headers,
        json={"text": "Bonjour, ceci est un test de la Forge.", "voice": voice_in},
        timeout=45,
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("X-TTS-Voice") == expected_out, r.headers
    assert r.headers.get("content-type", "").startswith("audio/")
    assert len(r.content) > 5000, f"audio too small: {len(r.content)}"


def test_tts_bogus_voice_no_empty_stream(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/tts",
        headers=auth_headers,
        json={"text": "Test de repli.", "voice": "fr-FR-NexistePasNeural"},
        timeout=45,
    )
    # Must not return silent empty audio: either fallback audio OR clean error
    if r.status_code == 200:
        assert len(r.content) > 5000, f"empty audio for bogus voice: {len(r.content)}"
    else:
        assert r.status_code >= 400
        assert r.status_code < 600
