"""Tests de la Brique C : transition de statut terminal idempotente."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


class _Res:
    def __init__(self, n):
        self.modified_count = n


def _match(doc, flt):
    for k, cond in flt.items():
        val = doc.get(k)
        if isinstance(cond, dict) and "$nin" in cond:
            if val in cond["$nin"]:
                return False
        elif val != cond:
            return False
    return True


class _FakeMessages:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(doc)

    async def find_one(self, flt, proj=None):
        for d in self.docs:
            if _match(d, flt):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    async def update_one(self, flt, upd):
        for d in self.docs:
            if _match(d, flt):
                d.update(upd["$set"])
                return _Res(1)
        return _Res(0)

    async def update_many(self, flt, upd):
        n = 0
        for d in self.docs:
            if _match(d, flt):
                d.update(upd["$set"])
                n += 1
        return _Res(n)


class _FakeDB:
    def __init__(self):
        self.messages = _FakeMessages()


META = {"provider": "p", "model": "m", "requested_provider": "p"}


def _run(coro):
    return asyncio.run(coro)


def test_new_turn_starts_running_without_completed_at():
    db = _FakeDB()
    doc = _run(server._ensure_assistant_doc(db, "c1"))
    assert doc["status"] == "running"
    assert doc["completed_at"] is None


def test_finalize_sets_done_and_completed_at():
    db = _FakeDB()
    doc = _run(server._ensure_assistant_doc(db, "c1"))
    out = _run(server._finalize_assistant(db, doc["id"], "ok", META, False))
    assert out["status"] == "done"
    assert out["stopped"] is False
    assert out["completed_at"]


def test_finalize_error_when_stopped():
    db = _FakeDB()
    doc = _run(server._ensure_assistant_doc(db, "c1"))
    out = _run(server._finalize_assistant(db, doc["id"], "x", META, True))
    assert out["status"] == "error"
    assert out["stopped"] is True


def test_terminal_transition_is_idempotent():
    db = _FakeDB()
    doc = _run(server._ensure_assistant_doc(db, "c1"))
    first = _run(server._finalize_assistant(db, doc["id"], "final", META, False))
    # Une seconde cloture (ex. filet de secours) ne change rien.
    second = _run(server._finalize_assistant(db, doc["id"], "AUTRE", META, True))
    assert second["status"] == "done"
    assert second["content"] == "final"
    assert second["completed_at"] == first["completed_at"]
    assert _run(server._settle_assistant(db, doc["id"])) is False
    assert db.messages.docs[0]["status"] == "done"


def test_settle_forces_error_on_running_turn_once():
    db = _FakeDB()
    doc = _run(server._ensure_assistant_doc(db, "c1"))
    assert _run(server._settle_assistant(db, doc["id"])) is True
    d = db.messages.docs[0]
    assert d["status"] == "error" and d["stopped"] is True and d["completed_at"]
    stamp = d["completed_at"]
    assert _run(server._settle_assistant(db, doc["id"])) is False
    assert d["completed_at"] == stamp


def test_settle_without_message_id_is_noop():
    assert _run(server._settle_assistant(_FakeDB(), None)) is False


def test_reap_orphans_only_touches_running_turns():
    db = _FakeDB()
    running = _run(server._ensure_assistant_doc(db, "c1"))
    done = _run(server._ensure_assistant_doc(db, "c2"))
    _run(server._finalize_assistant(db, done["id"], "ok", META, False))
    legacy = {"id": "old", "role": "assistant", "content": "x"}  # sans statut
    db.messages.docs.append(legacy)
    assert _run(server._reap_orphan_turns(db)) == 1
    by_id = {d["id"]: d for d in db.messages.docs}
    assert by_id[running["id"]]["status"] == "error"
    assert by_id[running["id"]]["orphaned"] is True
    assert by_id[done["id"]]["status"] == "done"
    assert "status" not in by_id["old"]


def test_persist_assistant_writes_terminal_status():
    db = _FakeDB()
    ok = _run(server._persist_assistant(db, "c1", "t", [], META, False))
    ko = _run(server._persist_assistant(db, "c1", "t", [], META, True))
    assert (ok["status"], ko["status"]) == ("done", "error")
    assert ok["completed_at"] and ko["completed_at"]
