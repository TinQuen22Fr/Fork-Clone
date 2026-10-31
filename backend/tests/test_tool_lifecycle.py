"""Brique E — tests autonomes du cycle de vie d'outil (statut par entree).

Sans reseau, sans MongoDB : on verifie uniquement la derivation du statut
`success` / `error` par entree d'outil (`_derive_tool_status`) et sa
preservation a travers la troncature des logs (`_truncate_step_logs`).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


def _bash(output):
    return {"tool": "bash", "input": {"command": "x"}, "output": output}


def test_bash_exit_zero_is_success():
    entry = _bash("cwd: /x\nexit_code: 0\n--- stdout ---\nok\n--- stderr ---")
    assert server._derive_tool_status(entry) == "success"


def test_bash_exit_nonzero_is_error():
    entry = _bash("cwd: /x\nexit_code: 1\n--- stdout ---\n\n--- stderr ---\nboom")
    assert server._derive_tool_status(entry) == "error"


def test_bash_exit_negative_is_error():
    entry = _bash("cwd: /x\nexit_code: -9\n--- stdout ---\n\n--- stderr ---\nkilled")
    assert server._derive_tool_status(entry) == "error"


def test_bash_timeout_without_exit_is_error():
    # Message de timeout : pas de ligne exit_code -> analyse texte -> error.
    entry = _bash("Erreur: timeout de 30 secondes dépassé.")
    assert server._derive_tool_status(entry) == "error"


def test_read_file_not_found_is_error():
    entry = {"tool": "read_file", "input": {"path": "/nope"},
             "output": "Erreur: fichier introuvable: /nope"}
    assert server._derive_tool_status(entry) == "error"


def test_read_file_ok_is_success():
    entry = {"tool": "read_file", "input": {"path": "/x"},
             "output": "contenu normal du fichier"}
    assert server._derive_tool_status(entry) == "success"


def test_sensitive_file_block_is_error():
    entry = _bash("Accès aux fichiers de configuration sensibles/secrets interdit via bash.")
    assert server._derive_tool_status(entry) == "error"


def test_unknown_tool_error_message_is_error():
    entry = {"tool": "read_file", "input": {}, "output": "Erreur d'exécution: boom"}
    assert server._derive_tool_status(entry) == "error"


def test_empty_output_defaults_to_success():
    entry = {"tool": "web_search", "input": {"query": "x"}, "output": ""}
    assert server._derive_tool_status(entry) == "success"


def test_missing_output_never_raises():
    assert server._derive_tool_status({"tool": "bash"}) == "success"
    assert server._derive_tool_status({}) == "success"


def test_error_word_mid_output_is_not_error():
    # Le mot "erreur" au milieu d'une sortie legitime ne doit pas faire basculer.
    entry = _bash("cwd: /x\nexit_code: 0\n--- stdout ---\naucune erreur detectee\n--- stderr ---")
    assert server._derive_tool_status(entry) == "success"


def test_status_survives_truncation():
    # Le statut par entree d'outil doit survivre a _truncate_step_logs.
    big = "x" * (server.STEP_LOG_TRUNC + 500)
    step = {
        "index": 0,
        "intention": "test",
        "tools": [
            {"tool": "bash", "input": {}, "output": big, "status": "error"},
            {"tool": "read_file", "input": {}, "output": "ok", "status": "success"},
        ],
    }
    out = server._truncate_step_logs(step)
    assert out["tools"][0]["status"] == "error"
    assert out["tools"][0]["truncated"] is True
    assert out["tools"][1]["status"] == "success"
    assert out["tools"][1]["truncated"] is False


def test_new_step_has_running_status():
    step = server._new_step(3, "  intent  ")
    assert step["status"] == "running"
    assert step["index"] == 3
    assert step["intention"] == "intent"
    assert step["tools"] == []
