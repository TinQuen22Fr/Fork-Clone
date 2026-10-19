"""Tests de la fin de flux explicite (Brique B) : evenement terminal unique."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


def _drain(q: "server._SSEQueue") -> list:
    async def _run():
        out = []
        async for ev in q.stream(ping_interval=0.0):
            out.append(ev)
        return out

    return asyncio.run(_run())


def test_finish_emits_single_terminal_then_closes():
    q = server._SSEQueue()
    q.publish({"type": "delta", "text": "a"})
    q.finish("done")
    events = _drain(q)
    assert events[0]["type"] == "delta"
    assert events[-1]["type"] == "finished"
    assert events[-1]["status"] == "done"
    assert sum(1 for e in events if e["type"] == "finished") == 1


def test_finish_is_idempotent():
    q = server._SSEQueue()
    q.finish("done")
    q.finish("error")  # second appel : ne doit rien ajouter
    q.close()
    events = _drain(q)
    assert [e["type"] for e in events] == ["finished"]
    assert events[0]["status"] == "done"


def test_publish_after_close_is_ignored():
    q = server._SSEQueue()
    q.finish("done")
    q.publish({"type": "delta", "text": "tardif"})
    events = _drain(q)
    assert [e["type"] for e in events] == ["finished"]


def test_finish_carries_status_and_extra():
    q = server._SSEQueue()
    q.finish("error", detail="boom")
    events = _drain(q)
    assert len(events) == 1
    assert events[0]["type"] == "finished"
    assert events[0]["status"] == "error"
    assert events[0]["detail"] == "boom"
    # Brique A : chaque evenement porte un numero de sequence monotone.
    assert events[0]["seq"] == 1
