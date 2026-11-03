"""Tests de la generation deterministe du message de commit conventionnel.

Couvre _commit_message_from_diff sans reseau ni MongoDB : on importe la
fonction via importlib pour eviter le cout de l'app FastAPI complete.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Variables minimales pour que l'import de server ne casse pas.
os.environ.setdefault("JWT_SECRET", "test-secret-for-commit-message")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_forge")

from server import _commit_message_from_diff, _commit_scope_from_paths  # noqa: E402


def test_docs_only():
    status = [" M RAPPORT.md", "?? docs/notes.md"]
    msg = _commit_message_from_diff(status, "")
    assert msg.split("\n", 1)[0].startswith("docs"), msg


def test_tests_only():
    status = ["?? backend/tests/test_new.py"]
    msg = _commit_message_from_diff(status, "")
    first = msg.split("\n", 1)[0]
    assert first.startswith(("test(", "test:")), first


def test_feat_new_files_only():
    status = ["A  backend/feature.py"]
    msg = _commit_message_from_diff(status, "")
    first = msg.split("\n", 1)[0]
    assert first.startswith("feat"), first


def test_mixed_defaults_chore():
    status = [" M backend/server.py", "?? frontend/src/x.jsx"]
    msg = _commit_message_from_diff(status, "")
    first = msg.split("\n", 1)[0]
    assert first.startswith(("chore", "feat", "docs", "test", "fix")), first


def test_subject_length_capped():
    status = [" M " + "a/" + "x" * 200 + ".py"]
    msg = _commit_message_from_diff(status, "")
    first = msg.split("\n", 1)[0]
    assert len(first) <= 72, (len(first), first)


def test_scope_single_top():
    assert _commit_scope_from_paths(["backend/server.py", "backend/x.py"]) == "backend"


def test_scope_multi_top_empty():
    assert _commit_scope_from_paths(["backend/a.py", "frontend/b.js"]) == ""


def test_body_lists_files():
    status = ["A  backend/new.py", " M backend/old.py", "D  backend/gone.py"]
    msg = _commit_message_from_diff(status, "")
    assert "- ajout: backend/new.py" in msg
    assert "- modif: backend/old.py" in msg
    assert "- suppr: backend/gone.py" in msg


def test_single_file_subject():
    status = [" M agents/vibe-coder.md"]
    msg = _commit_message_from_diff(status, "")
    first = msg.split("\n", 1)[0]
    assert "vibe-coder.md" in first, first


def test_no_trailing_period():
    status = [" M README.md"]
    msg = _commit_message_from_diff(status, "")
    first = msg.split("\n", 1)[0]
    assert not first.endswith("."), first
