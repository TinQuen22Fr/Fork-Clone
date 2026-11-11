"""Etape 4 (chantier rendu-blocs-style-emergent) — tests autonomes de `blocks[]`.

Sans reseau, sans MongoDB : on verifie :
- le schema initial d'`_ensure_assistant_doc` (`blocks: []` a cote de `content`),
- `_make_tool_blocks` (`tool_call` + `tool_output`, `seq`, `step_index`, `exit_code`, troncature a STEP_LOG_TRUNC),
- `_push_blocks` ($push `$each` incrémental),
- la boucle complete de `_run_generation` sur un tour intercalant texte -> outil -> texte -> outil -> texte,
- l'invariant §2.4 : `content == "".join(b["text"] for b in blocks if b["type"] == "text")`,
- la croissance stricte et l'unicite de `seq`.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import server  # noqa: E402


class _FakeCollection:
    def __init__(self, initial=None):
        self.docs = list(initial or [])

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    @staticmethod
    def _matches(doc, query):
        for k, v in query.items():
            if isinstance(v, dict) and "$nin" in v:
                if doc.get(k) in v["$nin"]:
                    return False
            elif doc.get(k) != v:
                return False
        return True

    async def find_one(self, query, projection=None):
        for d in self.docs:
            if self._matches(d, query):
                if projection and projection.get("_id") == 0:
                    return {k: v for k, v in d.items() if k != "_id"}
                return dict(d)
        return None

    async def update_one(self, query, update):
        class _Res:
            modified_count = 0
        res = _Res()
        for d in self.docs:
            if self._matches(d, query):
                if "$set" in update:
                    d.update(update["$set"])
                if "$push" in update:
                    for field, val in update["$push"].items():
                        lst = d.setdefault(field, [])
                        if isinstance(val, dict) and "$each" in val:
                            lst.extend(val["$each"])
                        else:
                            lst.append(val)
                res.modified_count = 1
                return res
        return res


class _FakeDB:
    def __init__(self):
        self.messages = _FakeCollection()
        self.conversations = _FakeCollection([{"id": "conv-1", "title": "t"}])


def test_ensure_assistant_doc_initializes_empty_blocks():
    db = _FakeDB()
    doc = asyncio.run(server._ensure_assistant_doc(db, "conv-1"))
    assert "blocks" in doc
    assert doc["blocks"] == []
    assert doc["content"] == ""
    assert doc["steps"] == []
    assert doc["tool_steps"] == []


def test_make_tool_blocks_bash_with_exit_code_and_truncation():
    step = {
        "tool": "bash",
        "input": {"command": "ls -la"},
        "output": "cwd: /tmp\nexit_code: 0\n--- stdout ---\nok\n--- stderr ---",
    }
    blocks = server._make_tool_blocks(2, 3, step, step_index=1)
    assert len(blocks) == 2
    call, out = blocks
    assert call == {
        "type": "tool_call",
        "seq": 2,
        "tool": "bash",
        "input": {"command": "ls -la"},
        "step_index": 1,
    }
    assert out["type"] == "tool_output"
    assert out["seq"] == 3
    assert out["tool"] == "bash"
    assert out["exit_code"] == 0
    assert out["truncated"] is False
    assert out["step_index"] == 1


def test_make_tool_blocks_truncates_large_output_like_tool_steps():
    big = "A" * (server.STEP_LOG_TRUNC + 250)
    step = {"tool": "read_file", "input": {"path": "x"}, "output": big}
    _, out = server._make_tool_blocks(0, 1, step, step_index=0)
    assert out["truncated"] is True
    assert len(out["output"]) < len(big)
    assert "sortie tronquee" in out["output"]
    assert "exit_code" not in out


def test_run_generation_populates_interleaved_blocks_and_preserves_invariant():
    """Simule un tour : delta, delta -> tool -> delta -> tool -> delta -> done."""
    db = _FakeDB()
    channel = server._SSEQueue()

    async def fake_stream(**_kwargs):
        yield {"type": "start", "provider": "opencode", "model": "m1"}
        yield {"type": "delta", "text": "Bonjour "}
        yield {"type": "delta", "text": "je verifie.\n"}
        yield {
            "type": "tool",
            "step": {
                "tool": "bash",
                "input": {"command": "echo 1"},
                "output": "cwd: /w\nexit_code: 0\n--- stdout ---\n1\n--- stderr ---",
            },
        }
        yield {"type": "delta", "text": "Milieu "}
        yield {"type": "delta", "text": "du tour.\n"}
        yield {
            "type": "tool",
            "step": {
                "tool": "read_file",
                "input": {"path": "a.txt"},
                "output": "contenu a",
            },
        }
        yield {"type": "delta", "text": "Conclusion finale."}
        yield {
            "type": "done",
            "provider": "opencode",
            "model": "m1",
            "requested_provider": "opencode",
            "fallback_used": False,
            "attempts": [],
        }

    async def _noop(*_a, **_kw):
        return None

    async def _run():
        with patch.object(server, "stream_ai_response", side_effect=fake_stream), \
             patch.object(server, "prepare_turn_context", side_effect=_noop), \
             patch.object(server, "_autotitle", side_effect=_noop):
            await server._run_generation(
                database=db,
                conversation_id="conv-1",
                history=[],
                prompt_text="go",
                images=[],
                attachments=[],
                provider="opencode",
                model="m1",
                project=None,
                channel=channel,
                user_id=None,
            )

    asyncio.run(_run())

    assert len(db.messages.docs) == 1
    msg = db.messages.docs[0]
    blocks = msg["blocks"]

    # Types dans l'ordre chronologique exact :
    # text -> tool_call -> tool_output -> text -> tool_call -> tool_output -> text
    assert [b["type"] for b in blocks] == [
        "text",
        "tool_call",
        "tool_output",
        "text",
        "tool_call",
        "tool_output",
        "text",
    ]

    # Règle §2.1 : seq strictement croissant et unique (0..N-1).
    seqs = [b["seq"] for b in blocks]
    assert seqs == list(range(len(blocks)))

    # Règle §2.2 : step_index relie chaque outil à son étape dans msg["steps"].
    step_indices = {s["index"] for s in msg["steps"]}
    for b in (blocks[1], blocks[2], blocks[4], blocks[5]):
        assert b["step_index"] in step_indices

    # Règle §2.4 (INVARIANT) : concat(blocks[type=="text"]) == content.
    concat_text = "".join(b["text"] for b in blocks if b["type"] == "text")
    assert concat_text == msg["content"]
    assert msg["content"] == (
        "Bonjour je verifie.\nMilieu du tour.\nConclusion finale."
    )


def test_run_generation_text_only_without_start_event_still_persists_block():
    """Cas limite : flux qui émet des deltas sans événement `start` ni outil."""
    db = _FakeDB()
    channel = server._SSEQueue()

    async def fake_stream(**_kwargs):
        yield {"type": "delta", "text": "Réponse directe "}
        yield {"type": "delta", "text": "sans outil."}

    async def _noop(*_a, **_kw):
        return None

    async def _run():
        with patch.object(server, "stream_ai_response", side_effect=fake_stream), \
             patch.object(server, "prepare_turn_context", side_effect=_noop), \
             patch.object(server, "_autotitle", side_effect=_noop):
            await server._run_generation(
                database=db,
                conversation_id="conv-1",
                history=[],
                prompt_text="go",
                images=[],
                attachments=[],
                provider="opencode",
                model="m1",
                project=None,
                channel=channel,
                user_id=None,
            )

    asyncio.run(_run())
    msg = db.messages.docs[0]
    assert [b["type"] for b in msg["blocks"]] == ["text"]
    assert msg["blocks"][0]["text"] == msg["content"] == "Réponse directe sans outil."


def test_build_messages_pipeline_exposes_blocks_count_and_drops_blocks():
    """Etape 5 : le listing expose blocks_count, jamais blocks[] complet."""
    flt = {"conversation_id": "c1"}
    pipeline = server._build_messages_pipeline(flt, limit=10)

    # Les etapes attendues, dans l'ordre.
    assert [st.keys() for st in pipeline if isinstance(st, dict)] and all(
        isinstance(st, dict) for st in pipeline
    )
    assert pipeline[0] == {"$match": flt}
    assert pipeline[1] == {"$sort": {"created_at": -1}}
    assert pipeline[2] == {"$limit": 10}

    set_stage = pipeline[3]["$set"]
    assert set_stage["blocks_count"] == {"$size": {"$ifNull": ["$blocks", []]}}

    unset = pipeline[4]["$unset"]
    assert "blocks" in unset
    assert "image_b64" in unset
    assert "file_text" in unset
    assert "images_b64" in unset
    assert "prompt_override" in unset
    assert "_id" in unset


def _run_pipeline(docs, pipeline):
    """Mini-executeur des etages utilises par le listing (sans MongoDB)."""
    out = list(docs)
    for stage in pipeline:
        if "$match" in stage:
            q = stage["$match"]
            out = [d for d in out if all(d.get(k) == v for k, v in q.items())]
        elif "$sort" in stage:
            key, order = next(iter(stage["$sort"].items()))
            out = sorted(out, key=lambda d: d.get(key, ""), reverse=(order == -1))
        elif "$limit" in stage:
            out = out[: stage["$limit"]]
        elif "$set" in stage:
            for d in out:
                for field, expr in stage["$set"].items():
                    if isinstance(expr, dict) and "$size" in expr:
                        inner = expr["$size"]
                        if isinstance(inner, dict) and "$ifNull" in inner:
                            ref = inner["$ifNull"][0]
                            # Une reference de champ Mongo est prefixee par "$".
                            arr = d.get(ref[1:] if ref.startswith("$") else ref,
                                       inner["$ifNull"][1])
                        else:
                            ref = inner
                            arr = d.get(ref[1:] if ref.startswith("$") else ref, [])
                        d[field] = len(arr)
                    else:
                        d[field] = expr
        elif "$unset" in stage:
            for d in out:
                for field in stage["$unset"]:
                    d.pop(field, None)
    return out


def test_listing_pipeline_semantics_blocks_count_and_no_blocks():
    """Comportement reel du pipeline : blocks_count = len(blocks), blocks absent."""
    docs = [
        {
            "_id": "id1",
            "conversation_id": "c1",
            "role": "assistant",
            "content": "hello",
            "created_at": "2026-01-01T00:00:01",
            "blocks": [{"type": "text", "seq": 0, "text": "a"},
                       {"type": "text", "seq": 1, "text": "b"}],
            "image_b64": "AAA",
        },
        {
            "_id": "id2",
            "conversation_id": "c1",
            "role": "assistant",
            "content": "old message sans blocks",
            "created_at": "2026-01-01T00:00:00",
        },
    ]
    pipeline = server._build_messages_pipeline({"conversation_id": "c1"}, limit=10)
    out = _run_pipeline(docs, pipeline)

    # Tri desc : le plus recent (blocks) d'abord.
    assert out[0]["blocks_count"] == 2
    assert "blocks" not in out[0]
    assert out[0]["content"] == "hello"

    # Ancien message sans blocks -> blocks_count == 0 (fallback binaire).
    assert out[1]["blocks_count"] == 0
    assert "blocks" not in out[1]

    # Les champs lourds sont retires.
    assert "image_b64" not in out[0]
    assert "_id" not in out[0]
