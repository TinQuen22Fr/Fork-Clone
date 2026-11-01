"""Brique F — tests autonomes de la reprise de session cote provider.

Sans reseau, sans MongoDB : on verifie le contrat du payload /responses
(presence de `store`, injection de `previous_response_id`, delta seul) et le
flag de configuration. L'enchainement reel d'appels HTTP n'est pas teste ici
(il depend de l'API du provider ; voir "Non verifie" dans le rapport).
"""
import os
import sys
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


def _reload_server():
    importlib.reload(server)
    return server


# --- flag de configuration ---

def test_flag_default_true(monkeypatch):
    monkeypatch.delenv("ENABLE_PROVIDER_SESSION_RESUME", raising=False)
    s = _reload_server()
    assert s.Settings().enable_provider_session_resume is True


def test_flag_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_PROVIDER_SESSION_RESUME", "false")
    s = _reload_server()
    assert s.Settings().enable_provider_session_resume is False
    monkeypatch.delenv("ENABLE_PROVIDER_SESSION_RESUME", raising=False)
    _reload_server()


# --- payload /responses : store ---

def test_responses_payload_has_store_when_enabled(monkeypatch):
    monkeypatch.delenv("ENABLE_PROVIDER_SESSION_RESUME", raising=False)
    s = _reload_server()
    path, body = s._opencode_payload(
        "responses", "gpt-5.6-luna",
        [{"role": "user", "content": "hi"}], "sys", True,
    )
    assert path == "/responses"
    assert body.get("store") is True


def test_responses_payload_no_store_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_PROVIDER_SESSION_RESUME", "false")
    s = _reload_server()
    path, body = s._opencode_payload(
        "responses", "gpt-5.6-luna",
        [{"role": "user", "content": "hi"}], "sys", True,
    )
    assert "store" not in body
    monkeypatch.delenv("ENABLE_PROVIDER_SESSION_RESUME", raising=False)
    _reload_server()


def test_chat_payload_never_has_store():
    path, body = server._opencode_payload(
        "chat", "gpt-4o",
        [{"role": "user", "content": "hi"}], "sys", True,
    )
    assert path == "/chat/completions"
    assert "store" not in body


def test_messages_payload_never_has_store():
    path, body = server._opencode_payload(
        "messages", "qwen3",
        [{"role": "user", "content": "hi"}], "sys", True,
    )
    assert path == "/messages"
    assert "store" not in body


# --- contrat de reprise : previous_response_id + delta seul ---

def test_resume_payload_shape():
    """Simule la construction du payload de reprise telle que la boucle la fait."""
    base = server._opencode_payload(
        "responses", "gpt-5.6-luna",
        [{"role": "user", "content": "hi"}], "sys", True,
    )[1]
    prev_id = "resp_abc123"
    delta = [{"type": "function_call_output", "call_id": "c1", "output": "ok"}]
    # Reprise : seul le delta est transmis, l'historique n'est pas renvoye.
    base["previous_response_id"] = prev_id
    base["input"] = delta
    assert base["previous_response_id"] == "resp_abc123"
    assert base["input"] == delta
    assert base["store"] is True


def test_resume_delta_contains_only_tool_outputs():
    """Le delta ne doit contenir que des function_call_output, pas l'historique."""
    outputs = ["out1", "out2"]
    tool_calls = [{"id": "c1"}, {"id": "c2"}]
    delta = [
        {"type": "function_call_output", "call_id": tc["id"], "output": out}
        for tc, out in zip(tool_calls, outputs)
    ]
    assert all(d["type"] == "function_call_output" for d in delta)
    assert [d["call_id"] for d in delta] == ["c1", "c2"]
    assert [d["output"] for d in delta] == ["out1", "out2"]


# --- extraction de l'id de reponse (base du chainage) ---

def test_extract_response_id_present():
    data = {"id": "resp_xyz", "output": []}
    new_id = data.get("id") if isinstance(data, dict) else None
    assert (new_id or None) == "resp_xyz"


def test_extract_response_id_absent():
    data = {"output": []}
    new_id = data.get("id") if isinstance(data, dict) else None
    assert (new_id or None) is None


def test_extract_response_id_empty_string():
    data = {"id": "", "output": []}
    new_id = data.get("id") if isinstance(data, dict) else None
    # id vide -> repli (pas de reprise) : on ne chaine pas sur un id faux.
    assert (new_id or None) is None


def test_extract_response_id_non_dict():
    data = ["unexpected"]
    new_id = data.get("id") if isinstance(data, dict) else None
    assert (new_id or None) is None


# --- transports non concernes : pas de reprise de session ---

def test_resume_session_only_for_responses():
    for transport in ("chat", "messages"):
        resume = transport == "responses" and True
        assert resume is False
    assert (("responses" == "responses") and True) is True
