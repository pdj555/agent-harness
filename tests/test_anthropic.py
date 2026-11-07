from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from harness.anthropic import AnthropicProvider
from harness.tools import tool_specs


class FakeClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = iter(responses)
        self.requests: list[dict] = []
        self.closed = False
        self.messages = self

    @contextmanager
    def stream(self, **payload):
        self.requests.append(copy.deepcopy(payload))
        yield SimpleNamespace(get_final_message=lambda: next(self.responses))

    def close(self) -> None:
        self.closed = True


def response(content=None, stop_reason="end_turn", **kwargs):
    return {"content": content or [], "stop_reason": stop_reason, **kwargs}


def configured(monkeypatch, responses, **kwargs):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("HARNESS_MODEL", "configured-model")
    client = FakeClient(responses)
    return AnthropicProvider(client=client, **kwargs), client


@pytest.mark.parametrize("missing", ["ANTHROPIC_API_KEY", "HARNESS_MODEL"])
def test_claude_requires_explicit_key_and_model(monkeypatch, missing):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("HARNESS_MODEL", "configured-model")
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match=missing):
        AnthropicProvider(client=FakeClient([]))


def test_claude_request_preserves_system_and_optional_tool_arguments(monkeypatch):
    provider, client = configured(monkeypatch, [response([{"type": "text", "text": "Ready"}])])
    specs = tool_specs("principal")
    original = copy.deepcopy(specs)
    result = provider.complete(
        [{"role": "system", "content": "Guard the stage"}, {"role": "user", "content": "Repair"}], specs
    )
    payload = client.requests[0]
    assert payload["model"] == "configured-model"
    assert payload["system"] == "Guard the stage"
    assert payload["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Repair"}]}]
    assert payload["cache_control"] == {"type": "ephemeral"}
    assert all(
        tool["strict"] and tool["input_schema"]["additionalProperties"] is False for tool in payload["tools"]
    )
    plan = next(tool for tool in payload["tools"] if tool["name"] == "set_plan")
    assert plan["input_schema"]["required"] == ["steps"]
    assert specs == original
    assert result.text == "Ready"
    assert not result.tool_calls


def test_parallel_tool_results_and_signed_thinking_survive_the_next_request(monkeypatch):
    blocks = [
        {"type": "thinking", "thinking": "Inspect the files first", "signature": "signed-content"},
        {"type": "redacted_thinking", "data": "opaque-content"},
        {"type": "tool_use", "id": "read-a", "name": "read_file", "input": {"path": "a.py"}},
        {"type": "tool_use", "id": "read-b", "name": "read_file", "input": {"path": "b.py"}},
    ]
    provider, client = configured(
        monkeypatch,
        [response(blocks, "tool_use"), response([{"type": "text", "text": "Inspected"}])],
    )
    messages = [{"role": "user", "content": "Inspect"}]
    completion = provider.complete(messages, tool_specs("principal"))
    assert [(call.id, call.arguments) for call in completion.tool_calls] == [
        ("read-a", {"path": "a.py"}),
        ("read-b", {"path": "b.py"}),
    ]
    messages.extend(
        [
            {"role": "assistant", "content": "", "content_blocks": completion.content_blocks},
            {"role": "tool", "tool_call_id": "read-a", "content": "first file"},
            {"role": "tool", "tool_call_id": "read-b", "content": "denied", "is_error": True},
        ]
    )
    provider.complete(messages, tool_specs("principal"))
    assert client.requests[1]["messages"][1] == {"role": "assistant", "content": blocks}
    assert client.requests[1]["messages"][2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "read-a", "content": "first file"},
            {"type": "tool_result", "tool_use_id": "read-b", "content": "denied", "is_error": True},
        ],
    }


@pytest.mark.parametrize(
    "reason", ["max_tokens", "refusal", "pause_turn", "model_context_window_exceeded", None]
)
def test_incomplete_or_unsupported_stop_reasons_never_return_tool_calls(monkeypatch, reason):
    call = {"type": "tool_use", "id": "a", "name": "edit_file", "input": {"path": "a.py"}}
    provider, _ = configured(monkeypatch, [response([call], reason)])
    with pytest.raises(RuntimeError, match="did not complete"):
        provider.complete([{"role": "user", "content": "Fix"}], tool_specs("principal"))


@pytest.mark.parametrize(
    "blocks,reason",
    [
        ([{"type": "tool_use", "id": "a", "name": "read_file", "input": []}], "tool_use"),
        ([{"type": "tool_use", "id": "", "name": "read_file", "input": {}}], "tool_use"),
        ([{"type": "tool_use", "id": "a", "name": "unknown", "input": {}}], "tool_use"),
        ([{"type": "tool_use", "id": "a", "name": "read_file", "input": {}}], "end_turn"),
        ([], "tool_use"),
        ([{"type": "server_tool_use", "id": "a", "name": "web_search", "input": {}}], "end_turn"),
        (
            [
                {"type": "tool_use", "id": "a", "name": "read_file", "input": {}},
                {"type": "tool_use", "id": "a", "name": "read_file", "input": {}},
            ],
            "tool_use",
        ),
    ],
)
def test_malformed_or_unavailable_tool_calls_fail_closed(monkeypatch, blocks, reason):
    provider, _ = configured(monkeypatch, [response(blocks, reason)])
    with pytest.raises(RuntimeError):
        provider.complete([{"role": "user", "content": "Inspect"}], tool_specs("principal"))


def test_reviewer_request_uses_structured_outputs_with_read_only_tools(monkeypatch):
    schema = {
        "type": "object",
        "properties": {"ship": {"type": "boolean"}},
        "required": ["ship"],
        "additionalProperties": False,
    }
    provider, client = configured(
        monkeypatch, [response([{"type": "text", "text": '{"ship": true}'}])], output_schema=schema
    )
    result = provider.complete([{"role": "user", "content": "Review"}], tool_specs("reviewer"))
    payload = client.requests[0]
    assert payload["output_config"] == {"format": {"type": "json_schema", "schema": schema}}
    assert {tool["name"] for tool in payload["tools"]} == {spec["name"] for spec in tool_specs("reviewer")}
    assert "edit_file" not in {tool["name"] for tool in payload["tools"]}
    assert result.text == '{"ship": true}'


def test_usage_accumulates_and_client_closes(monkeypatch):
    provider, client = configured(
        monkeypatch,
        [
            response(usage={"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 4}),
            response(usage={"input_tokens": 20, "output_tokens": 3, "cache_creation_input_tokens": 8}),
        ],
    )
    for _ in range(2):
        provider.complete([{"role": "user", "content": "Inspect"}], [])
    assert provider.usage == {
        "input_tokens": 30,
        "output_tokens": 5,
        "cache_read_input_tokens": 4,
        "cache_creation_input_tokens": 8,
    }
    assert "tools" not in client.requests[0]
    provider.close()
    assert client.closed


def test_outbound_messages_redact_configured_keys(monkeypatch):
    provider, client = configured(monkeypatch, [response()])
    provider.complete(
        [
            {"role": "system", "content": "Never print test-key"},
            {"role": "user", "content": "test-key"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "a", "name": "read_file", "arguments": {"path": "test-key"}}],
            },
            {"role": "tool", "tool_call_id": "a", "content": "test-key"},
        ],
        [],
    )
    assert "test-key" not in str(client.requests)
    assert "[redacted]" in str(client.requests)


def test_raw_assistant_blocks_redact_keys_without_changing_signed_thinking(monkeypatch):
    provider, client = configured(monkeypatch, [response()])
    provider.complete(
        [
            {"role": "user", "content": "Inspect"},
            {
                "role": "assistant",
                "content_blocks": [
                    {"type": "thinking", "thinking": "Need to inspect", "signature": "signed-content"},
                    {"type": "text", "text": "test-key"},
                    {"type": "tool_use", "id": "a", "name": "read_file", "input": {"path": "test-key"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "result"},
        ],
        [],
    )
    assert "test-key" not in str(client.requests)
    assert client.requests[0]["messages"][1]["content"][0]["signature"] == "signed-content"


def test_signed_thinking_containing_credentials_is_rejected_before_request(monkeypatch):
    provider, client = configured(monkeypatch, [response()])
    with pytest.raises(RuntimeError, match="configured credential"):
        provider.complete(
            [
                {"role": "user", "content": "Inspect"},
                {
                    "role": "assistant",
                    "content_blocks": [
                        {"type": "thinking", "thinking": "test-key", "signature": "signed-content"}
                    ],
                },
            ],
            [],
        )
    assert client.requests == []


def test_official_sdk_serializes_and_consumes_a_stream_without_network(monkeypatch):
    import httpx2

    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("HARNESS_MODEL", "configured-model")
    requests = []
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "message-test",
                "type": "message",
                "role": "assistant",
                "model": "configured-model",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Ready"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    ]

    def handle(request):
        requests.append(json.loads(request.content))
        body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    client = anthropic.Anthropic(http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
    provider = AnthropicProvider(client=client)
    try:
        result = provider.complete([{"role": "user", "content": "Inspect"}], tool_specs("principal"))
    finally:
        provider.close()
    assert result.text == "Ready"
    assert requests[0]["stream"] is True
    assert requests[0]["cache_control"] == {"type": "ephemeral"}
    assert requests[0]["tools"][0]["strict"] is True
    assert provider.usage == {"input_tokens": 10, "output_tokens": 1}
