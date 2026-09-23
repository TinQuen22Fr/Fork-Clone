"""Validate that Kokoro/soundfile are OPTIONAL and never break install/upgrade.

Bug context (iteration 10): on Ubuntu 26.04 / Python 3.14, upgrade.sh failed at
`pip install -r backend/requirements.txt` because kokoro-onnx pins
`Requires-Python >=3.10,<3.14`. Fix moved kokoro-onnx + soundfile to a separate
requirements-kokoro.txt installed conditionally by deploy/install-kokoro.sh
with `--ignore-requires-python`, and upgrade.sh now only reports Kokoro
presence without failing.
"""
import os
import re
import subprocess
import pytest
import requests

REPO = "/app"
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_EMAIL = "admin@forge.dev"
ADMIN_PASSWORD = "ForgeAdmin2026!"


# ---------------- deps files ----------------

def test_requirements_has_no_kokoro():
    with open(f"{REPO}/backend/requirements.txt") as fh:
        content = fh.read().lower()
    assert "kokoro" not in content
    assert "soundfile" not in content


def test_requirements_kokoro_file_exists_and_pins():
    p = f"{REPO}/backend/requirements-kokoro.txt"
    assert os.path.isfile(p)
    with open(p) as fh:
        c = fh.read()
    assert re.search(r"^kokoro-onnx", c, re.MULTILINE)
    assert re.search(r"^soundfile", c, re.MULTILINE)


def test_pip_dry_run_requirements_ok():
    r = subprocess.run(
        ["python3", "-m", "pip", "install", "--dry-run", "-r",
         f"{REPO}/backend/requirements.txt"],
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    # ensure resolver did not even consider kokoro
    assert "kokoro" not in (r.stdout + r.stderr).lower()


# ---------------- shell scripts ----------------

def test_install_kokoro_syntax():
    r = subprocess.run(["bash", "-n", f"{REPO}/deploy/install-kokoro.sh"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_install_kokoro_uses_ignore_requires_python_and_exits_0_on_fail():
    with open(f"{REPO}/deploy/install-kokoro.sh") as fh:
        c = fh.read()
    assert "--ignore-requires-python" in c
    assert "requirements-kokoro.txt" in c
    # graceful exit 0 with warning on failure
    assert "exit 0" in c


def test_upgrade_syntax():
    r = subprocess.run(["bash", "-n", f"{REPO}/upgrade.sh"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_upgrade_does_not_hard_depend_on_kokoro():
    with open(f"{REPO}/upgrade.sh") as fh:
        c = fh.read()
    # kokoro is only mentioned as an optional presence check, never installed
    # and never causes die()
    assert "import kokoro_onnx" in c
    # No die() on kokoro-related lines
    for line in c.splitlines():
        if "kokoro" in line.lower():
            assert "die " not in line and "die(" not in line, line


# ---------------- no FreeTTS trace ----------------

def test_no_freetts_reference():
    for rel in ("backend/server.py", "backend/env.example"):
        p = os.path.join(REPO, rel)
        if not os.path.isfile(p):
            continue
        with open(p) as fh:
            assert "freetts" not in fh.read().lower(), f"FreeTTS found in {rel}"
    # frontend src
    for root, _, files in os.walk(f"{REPO}/frontend/src"):
        for f in files:
            if f.endswith((".js", ".jsx", ".ts", ".tsx")):
                with open(os.path.join(root, f)) as fh:
                    assert "freetts" not in fh.read().lower(), \
                        f"FreeTTS found in {os.path.join(root, f)}"


# ---------------- runtime backend ----------------

@pytest.fixture(scope="module")
def auth_headers():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=30)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=10)
    assert r.status_code == 200


def test_conversations(auth_headers):
    r = requests.get(f"{BASE_URL}/api/conversations",
                     headers=auth_headers, timeout=15)
    assert r.status_code == 200


def test_tts_voices_no_kokoro_engine(auth_headers):
    r = requests.get(f"{BASE_URL}/api/tts/voices?locale=fr",
                     headers=auth_headers, timeout=20)
    assert r.status_code == 200
    data = r.json()
    engines = data.get("engines") or []
    assert "edge-tts" in engines
    # Kokoro models not installed here → engine must be absent
    assert "kokoro" not in engines, engines
    voices = data.get("voices") or []
    assert any("fr-FR" in (v.get("short_name") or "") for v in voices)
    # No entry advertised with kokoro: prefix
    assert not any((v.get("short_name") or "").startswith("kokoro:")
                   for v in voices)


def test_tts_edge_denise(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/tts",
        headers=auth_headers,
        json={"text": "Bonjour, ceci est un test.",
              "voice": "fr-FR-DeniseNeural"},
        timeout=45,
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("X-TTS-Provider") == "edge-tts", r.headers
    assert r.headers.get("content-type", "").startswith("audio/")
    assert len(r.content) > 5000


def test_tts_kokoro_fallback_to_edge(auth_headers):
    """When user asks for kokoro:ff_siwis but Kokoro isn't installed,
    backend MUST silently fall back to edge-tts (never 5xx)."""
    r = requests.post(
        f"{BASE_URL}/api/tts",
        headers=auth_headers,
        json={"text": "Repli sur edge-tts.", "voice": "kokoro:ff_siwis"},
        timeout=45,
    )
    assert r.status_code == 200, r.text
    assert r.status_code < 500
    assert r.headers.get("content-type", "").startswith("audio/")
    assert len(r.content) > 5000
    # Must have been served by edge-tts
    assert r.headers.get("X-TTS-Provider") == "edge-tts", r.headers
