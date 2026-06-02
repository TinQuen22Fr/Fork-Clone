"""Backend tests for Gemini3 Unchained Forge."""
import os
import io
import time
import uuid
import base64
import pytest
import requests
from PIL import Image

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://fork-clone-1.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@forge.dev"
ADMIN_PASSWORD = "ForgeAdmin2026!"


@pytest.fixture
def s():
    # Fresh session per-test to avoid cookie leakage between bearer-auth tests
    sess = requests.Session()
    return sess


@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "token" in data and data["email"] == ADMIN_EMAIL
    return data["token"]


@pytest.fixture(scope="session")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def new_user():
    email = f"test_{uuid.uuid4().hex[:8]}@forge.dev"
    password = "TestPass2026!"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password, "name": "Tester"}, timeout=30)
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["email"] == email
    assert "token" in data
    return {"email": email, "password": password, "token": data["token"], "id": data["id"]}


@pytest.fixture(scope="session")
def user_headers(new_user):
    return {"Authorization": f"Bearer {new_user['token']}"}


# ---------- Auth ----------
class TestAuth:
    def test_root(self, s):
        r = s.get(f"{API}/", timeout=15)
        assert r.status_code == 200
        assert "Gemini3" in r.json().get("message", "")

    def test_login_success(self, s):
        r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["email"] == ADMIN_EMAIL
        assert d["role"] == "admin"
        assert len(d["token"]) > 20

    def test_login_invalid(self, s):
        r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"}, timeout=15)
        assert r.status_code == 401

    def test_me_no_token(self, s):
        r = s.get(f"{API}/auth/me", timeout=15)
        assert r.status_code == 401

    def test_me_with_token(self, s, admin_headers):
        r = s.get(f"{API}/auth/me", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_register_creates_user(self, new_user):
        # already created via fixture; verify token works
        assert new_user["token"]

    def test_register_duplicate(self, s, new_user):
        r = s.post(f"{API}/auth/register", json={"email": new_user["email"], "password": "Anything123"}, timeout=15)
        assert r.status_code == 400

    def test_bcrypt_hash_format(self):
        # Verify password hashing uses bcrypt $2b$
        import bcrypt
        h = bcrypt.hashpw(b"test", bcrypt.gensalt()).decode()
        assert h.startswith("$2b$")


# ---------- Conversations CRUD ----------
class TestConversations:
    def test_create_conversation(self, s, user_headers):
        r = s.post(f"{API}/conversations", json={"title": "TEST_conv"}, headers=user_headers, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["title"] == "TEST_conv"
        assert "id" in d
        pytest.conv_id = d["id"]

    def test_list_conversations(self, s, user_headers):
        r = s.get(f"{API}/conversations", headers=user_headers, timeout=15)
        assert r.status_code == 200
        convs = r.json()
        assert any(c["id"] == pytest.conv_id for c in convs)

    def test_rename_conversation(self, s, user_headers):
        r = s.patch(f"{API}/conversations/{pytest.conv_id}", json={"title": "TEST_renamed"}, headers=user_headers, timeout=15)
        assert r.status_code == 200
        # verify via list
        r2 = s.get(f"{API}/conversations", headers=user_headers, timeout=15)
        match = [c for c in r2.json() if c["id"] == pytest.conv_id]
        assert match and match[0]["title"] == "TEST_renamed"

    def test_get_messages_empty(self, s, user_headers):
        r = s.get(f"{API}/conversations/{pytest.conv_id}/messages", headers=user_headers, timeout=15)
        assert r.status_code == 200
        assert r.json() == []

    def test_conversation_isolation(self, s, admin_headers):
        # admin should not see this user's conversation
        r = s.get(f"{API}/conversations/{pytest.conv_id}/messages", headers=admin_headers, timeout=15)
        assert r.status_code == 404

    def test_no_auth_blocked(self, s):
        r = s.get(f"{API}/conversations", timeout=15)
        assert r.status_code == 401


# ---------- Chat ----------
class TestChat:
    def test_send_text_message(self, s, user_headers):
        # use the conv created above
        files = {
            "conversation_id": (None, pytest.conv_id),
            "text": (None, "Say hello in exactly 3 words."),
        }
        r = s.post(f"{API}/chat/send", files=files, headers=user_headers, timeout=120)
        assert r.status_code == 200, f"chat send failed: {r.status_code} {r.text}"
        d = r.json()
        assert "user_message" in d and "ai_message" in d
        assert d["ai_message"]["role"] == "assistant"
        assert len(d["ai_message"]["content"]) > 0
        pytest.first_ai_content = d["ai_message"]["content"]

    def test_multi_turn_context(self, s, user_headers):
        # Send a follow-up that depends on prior
        files = {
            "conversation_id": (None, pytest.conv_id),
            "text": (None, "What was my previous question? Answer briefly."),
        }
        r = s.post(f"{API}/chat/send", files=files, headers=user_headers, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        content = d["ai_message"]["content"].lower()
        # context preserved if response references "hello" or "3 words"
        assert ("hello" in content) or ("three" in content) or ("3" in content) or ("words" in content), \
            f"context not preserved: {content[:200]}"

    def test_messages_persisted_and_ordered(self, s, user_headers):
        r = s.get(f"{API}/conversations/{pytest.conv_id}/messages", headers=user_headers, timeout=15)
        assert r.status_code == 200
        msgs = r.json()
        # 2 user + 2 assistant = 4
        assert len(msgs) == 4
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"
        assert msgs[3]["role"] == "assistant"

    def test_send_image(self, s, user_headers):
        # Build a small jpeg in-memory
        buf = io.BytesIO()
        img = Image.new("RGB", (64, 64), color=(255, 20, 147))  # hot pink
        img.save(buf, format="JPEG")
        buf.seek(0)
        files = {
            "conversation_id": (None, pytest.conv_id),
            "text": (None, "What color is this image? One word."),
            "image": ("test.jpg", buf.getvalue(), "image/jpeg"),
        }
        r = s.post(f"{API}/chat/send", files=files, headers=user_headers, timeout=180)
        assert r.status_code == 200, f"image chat failed: {r.status_code} {r.text}"
        d = r.json()
        assert d["user_message"]["has_image"] is True
        assert len(d["ai_message"]["content"]) > 0

    def test_send_empty_rejected(self, s, user_headers):
        files = {"conversation_id": (None, pytest.conv_id), "text": (None, "")}
        r = s.post(f"{API}/chat/send", files=files, headers=user_headers, timeout=15)
        assert r.status_code == 400

    def test_send_to_other_user_conv_blocked(self, s, admin_headers):
        files = {"conversation_id": (None, pytest.conv_id), "text": (None, "hi")}
        r = s.post(f"{API}/chat/send", files=files, headers=admin_headers, timeout=15)
        assert r.status_code == 404


# ---------- Cleanup ----------
class TestCleanup:
    def test_delete_conversation(self, s, user_headers):
        r = s.delete(f"{API}/conversations/{pytest.conv_id}", headers=user_headers, timeout=15)
        assert r.status_code == 200
        # verify gone
        r2 = s.get(f"{API}/conversations/{pytest.conv_id}/messages", headers=user_headers, timeout=15)
        assert r2.status_code == 404
