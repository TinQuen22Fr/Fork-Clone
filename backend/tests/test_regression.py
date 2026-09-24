"""Regression tests for Claude Unchained Forge post file restoration."""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fork-clone-1.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@forge.dev"
ADMIN_PASSWORD = "ForgeAdmin2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "access_token" in data or "token" in data
    return data.get("access_token") or data.get("token")


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_health_root():
    r = requests.get(f"{BASE_URL}/api/", timeout=10)
    assert r.status_code == 200
    j = r.json()
    assert j.get("engine") == "claude"
    assert j.get("model") == "claude-sonnet-5"
    assert j.get("db") == "up"
    assert j.get("registration_open") is False


def test_registration_closed():
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": "x@x.dev", "password": "abcdef", "name": "x"}, timeout=10)
    assert r.status_code in (403, 404, 405)


def test_login_bad():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": "wrong"}, timeout=10)
    assert r.status_code in (400, 401)


def test_auth_me(auth_headers):
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers, timeout=10)
    assert r.status_code == 200
    assert r.json()["email"] == ADMIN_EMAIL


def test_conversation_crud_rename(auth_headers):
    # Create
    r = requests.post(f"{BASE_URL}/api/conversations", headers=auth_headers,
                      json={"title": "TEST_regression"}, timeout=10)
    assert r.status_code in (200, 201), r.text
    conv = r.json()
    cid = conv["id"]

    # Rename via PATCH
    r = requests.patch(f"{BASE_URL}/api/conversations/{cid}", headers=auth_headers,
                       json={"title": "TEST_renamed"}, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True

    # Verify persistence via GET list
    r = requests.get(f"{BASE_URL}/api/conversations", headers=auth_headers, timeout=10)
    assert r.status_code == 200
    found = [c for c in r.json() if c["id"] == cid]
    assert found and found[0]["title"] == "TEST_renamed"

    # Cleanup delete
    r = requests.delete(f"{BASE_URL}/api/conversations/{cid}", headers=auth_headers, timeout=10)
    assert r.status_code in (200, 204)


def test_chat_send_claude(auth_headers):
    # Create conv
    r = requests.post(f"{BASE_URL}/api/conversations", headers=auth_headers,
                      json={"title": "TEST_chat"}, timeout=10)
    cid = r.json()["id"]
    try:
        r = requests.post(
            f"{BASE_URL}/api/chat/send",
            headers=auth_headers,
            data={"conversation_id": cid, "text": "Reply with the single word: PONG"},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        j = r.json()
        # Response should include an assistant message text
        assistant_text = (j.get("assistant") or {}).get("content") or j.get("text") or str(j)
        assert isinstance(assistant_text, str) and len(assistant_text) > 0
    finally:
        requests.delete(f"{BASE_URL}/api/conversations/{cid}", headers=auth_headers, timeout=10)
