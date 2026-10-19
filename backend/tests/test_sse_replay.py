"""Tests du rejeu d'historique a la reconnexion (Brique A)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


def _drain_after(q: "server._SSEQueue", after_seq: int) -> list:
    async def _run():
        out = []
        async for ev in q.stream(ping_interval=0.0, after_seq=after_seq):
            out.append(ev)
        return out

    return asyncio.run(_run())


def test_replay_from_zero_returns_all_then_terminal():
    q = server._SSEQueue()
    q.publish({"type": "delta", "text": "a"})
    q.publish({"type": "delta", "text": "b"})
    q.finish("done")
    events = _drain_after(q, 0)
    assert [e["type"] for e in events] == ["delta", "delta", "finished"]
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_replay_after_seq_skips_already_seen():
    q = server._SSEQueue()
    q.publish({"type": "delta", "text": "a"})
    q.publish({"type": "delta", "text": "b"})
    q.publish({"type": "delta", "text": "c"})
    q.finish("done")
    # Le client a deja vu seq 1 et 2 : il ne doit recevoir que 3 puis finished.
    events = _drain_after(q, 2)
    assert [e["seq"] for e in events] == [3, 4]
    assert events[0]["text"] == "c"
    assert events[-1]["type"] == "finished"


def test_finished_after_seq_when_nothing_new():
    q = server._SSEQueue()
    q.publish({"type": "delta", "text": "a"})
    q.finish("done")
    events = _drain_after(q, 99)
    assert [e["type"] for e in events] == ["finished"]


def test_buffer_is_bounded():
    q = server._SSEQueue()
    limit = server._SSE_BUFFER_MAX
    for i in range(limit + 50):
        q.publish({"type": "delta", "text": str(i)})
    q.finish("done")
    events = _drain_after(q, 0)
    # Tampon borne : jamais plus de `limit` evenements rejoues, et le dernier
    # rejoue est bien le terminal `finished`.
    assert len(events) == limit
    assert events[-1]["type"] == "finished"
    total = limit + 50 + 1  # deltas + finished
    assert events[0]["seq"] == total - limit + 1


def test_sse_from_event_adds_seq():
    frame = server._sse_from_event({"type": "delta", "text": "x", "seq": 7})
    assert "event: delta" in frame
    assert '"seq": 7' in frame
    assert '"text": "x"' in frame
