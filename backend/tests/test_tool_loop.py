"""Tests sans réseau des relances de boucle d'outils pour les providers."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace

import pytest

from backend import server


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _collect(async_iterator):
    async def run():
        return [item async for item in async_iterator]

    return asyncio.run(run())


def _patch_tool(monkeypatch):
    monkeypatch.setattr(server, "_run_tool", lambda name, inputs: "tool result")


def test_openai_compat_retries_empty_turn_after_tool(monkeypatch):
    monkeypatch.setattr(server.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(server, "_provider_available", lambda pid: True)
    monkeypatch.setattr(server, "_resolve_free_model", lambda pid, override: _async_value("model"))
    monkeypatch.setattr(server, "_free_provider_headers", lambda pid: {})
    monkeypatch.setattr(server, "MAX_TOOL_ITERS", 5)
    monkeypatch.setattr(server, "EMPTY_TURN_RETRIES", 1)
    _patch_tool(monkeypatch)
    requests = []

    async def fake_turn(http, url, headers, payload):
        requests.append(payload)
        if len(requests) == 1:
            yield {"final": {"content": "", "tool_calls": [{"id": "c1", "name": "read_file", "arguments": "{}"}]}}
        else:
            yield {"final": {"content": "", "tool_calls": []}}

    monkeypatch.setattr(server, "_stream_openai_turn", fake_turn)
    events = _collect(server._stream_openai_compat("groq", [], "hello", None, None, {}))

    assert [event["tool"]["tool"] for event in events if "tool" in event] == ["read_file"]
    assert len(requests) == 3
    assert server.EMPTY_TURN_NUDGE in requests[2]["messages"][-1]["content"]


def _async_value(value):
    async def result():
        return value

    return result()


def test_claude_retries_empty_turn_after_tool(monkeypatch):
    monkeypatch.setattr(server.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(server.settings, "claude_token", "test-token")
    monkeypatch.setattr(server, "_resolve_claude_model", lambda: "model")
    monkeypatch.setattr(server, "MAX_TOOL_ITERS", 5)
    monkeypatch.setattr(server, "EMPTY_TURN_RETRIES", 1)
    _patch_tool(monkeypatch)
    requests = []

    async def fake_turn(http, url, headers, payload):
        requests.append(payload)
        if len(requests) == 1:
            yield {"final": {"stop_reason": "tool_use", "blocks": [{"type": "tool_use", "id": "c1", "name": "read_file", "input": {}}]}}
        else:
            yield {"final": {"stop_reason": "end_turn", "blocks": []}}

    monkeypatch.setattr(server, "_stream_anthropic_turn", fake_turn)
    events = _collect(server._stream_claude([], "hello", None, {}))

    assert [event["tool"]["tool"] for event in events if "tool" in event] == ["read_file"]
    assert len(requests) == 3
    nudge = requests[2]["messages"][-1]["content"][-1]["text"]
    assert nudge == server.EMPTY_TURN_NUDGE


def test_gemini_retries_empty_turn_after_tool(monkeypatch):
    from google import genai

    monkeypatch.setattr(server.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(server, "MAX_TOOL_ITERS", 5)
    monkeypatch.setattr(server, "EMPTY_TURN_RETRIES", 1)
    monkeypatch.setattr(server, "_gemini_contents", lambda history, text, images: [SimpleNamespace(role="user", parts=[])])
    monkeypatch.setattr(server, "_gemini_tool_declaration", lambda: {})
    _patch_tool(monkeypatch)
    requests = []
    calls_left = [True]

    class FakeModels:
        async def generate_content_stream(self, **kwargs):
            requests.append(kwargs["contents"])

            async def chunks():
                if len(requests) == 1:
                    chunk = SimpleNamespace(text=None, candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))])
                    yield chunk
                elif len(requests) == 2:
                    yield SimpleNamespace(text=None, candidates=[])
                else:
                    yield SimpleNamespace(text="done", candidates=[])

            return chunks()

    client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)

    def function_calls(chunk):
        if calls_left[0] and chunk.candidates:
            calls_left[0] = False
            return [SimpleNamespace(name="read_file", args={})]
        return []

    monkeypatch.setattr(server, "_gemini_function_calls", function_calls)
    events = _collect(server._stream_gemini([], "hello", None, {}))

    assert [event["tool"]["tool"] for event in events if "tool" in event] == ["read_file"]
    assert len(requests) == 3
    assert requests[2][-1].parts[-1].text == server.EMPTY_TURN_NUDGE


@pytest.mark.parametrize("provider", ["ollama", "ollama_cloud"])
def test_ollama_providers_retry_empty_turn_after_tool(monkeypatch, provider):
    monkeypatch.setattr(server, "MAX_TOOL_ITERS", 5)
    monkeypatch.setattr(server, "EMPTY_TURN_RETRIES", 1)
    _patch_tool(monkeypatch)
    monkeypatch.setattr(server, "forge_system_prompt", lambda: "")
    monkeypatch.setattr(server.settings, "enable_tools", True)
    monkeypatch.setattr(server.settings, "ollama_cloud_api_key", "test-key")
    monkeypatch.setattr(server.settings, "ollama_cloud_fallback_model", "")
    requests = []

    async def fake_ndjson(url, headers, payload, timeout):
        requests.append(payload)
        if len(requests) == 1:
            yield {"tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": {}}}]}

    monkeypatch.setattr(server, "_stream_ndjson_ollama", fake_ndjson)
    if provider == "ollama":
        events = _collect(server._stream_ollama([], "hello", {}))
    else:
        events = _collect(server._stream_ollama_cloud([], "hello", None, None, {}))

    assert [event["tool"]["tool"] for event in events if "tool" in event] == ["read_file"]
    assert len(requests) == 3
    assert server.EMPTY_TURN_NUDGE in requests[2]["messages"][-1]["content"]


def test_empty_turn_retries_are_bounded(monkeypatch):
    monkeypatch.setattr(server, "MAX_TOOL_ITERS", 10)
    monkeypatch.setattr(server, "EMPTY_TURN_RETRIES", 1)
    monkeypatch.setattr(server, "forge_system_prompt", lambda: "")
    requests = []

    async def fake_ndjson(url, headers, payload, timeout):
        requests.append(payload)
        if len(requests) == 1:
            yield {"tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": {}}}]}

    monkeypatch.setattr(server, "_stream_ndjson_ollama", fake_ndjson)
    _patch_tool(monkeypatch)
    _collect(server._stream_ollama([], "hello", {}))

    assert len(requests) == 3


def test_tool_iteration_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(server, "MAX_TOOL_ITERS", 1)
    monkeypatch.setattr(server, "EMPTY_TURN_RETRIES", 2)
    monkeypatch.setattr(server, "forge_system_prompt", lambda: "")
    _patch_tool(monkeypatch)
    requests = []

    async def fake_ndjson(url, headers, payload, timeout):
        requests.append(payload)
        yield {"tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": {}}}]}

    monkeypatch.setattr(server, "_stream_ndjson_ollama", fake_ndjson)
    events = _collect(server._stream_ollama([], "hello", {}))

    assert len(requests) == 1
    assert len([event for event in events if "tool" in event]) == 1
