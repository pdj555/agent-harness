from __future__ import annotations

import pytest
from harness.provider import (
    DeterministicProvider,
    OpenRouterProvider,
    ScriptedProvider,
    get_provider,
    openrouter_endpoint,
)
from harness.tools import tool_specs


def test_provider_boundary_substitutes_deterministic():
    provider = get_provider("deterministic")
    assert provider.name == "deterministic"
    assert isinstance(provider, DeterministicProvider)
    assert isinstance(get_provider("scripted"), ScriptedProvider)


@pytest.mark.parametrize("name", ["not-a-vendor", "ollama", "xai", "openai_compat"])
def test_unknown_and_removed_providers_are_rejected(name):
    with pytest.raises(ValueError, match="provider"):
        get_provider(name)


def test_openrouter_payload_uses_function_tools_and_translated_messages():
    payload = OpenRouterProvider.build_payload(
        [
            {"role": "system", "content": "principal"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "name": "read_file", "arguments": {"path": "tracker.py"}}],
            },
            {"role": "tool", "name": "read_file", "tool_call_id": "1", "content": "def classify_priority"},
        ],
        tool_specs("principal"),
        "qwen/qwen3-coder",
    )
    assert payload["model"] == "qwen/qwen3-coder"
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["function"]["parameters"].get("properties")
    assistant = payload["messages"][1]
    assert assistant["tool_calls"][0]["function"]["name"] == "read_file"
    assert payload["messages"][2] == {"role": "tool", "tool_call_id": "1", "content": "def classify_priority"}
    assert "reasoning" not in payload


def test_openrouter_reasoning_effort_is_opt_in(monkeypatch):
    monkeypatch.setenv("HARNESS_REASONING", "high")
    payload = OpenRouterProvider.build_payload([{"role": "user", "content": "x"}], [], "deepseek/deepseek-r1")
    assert payload["reasoning"] == {"effort": "high"}
    monkeypatch.setenv("HARNESS_REASONING", "turbo")
    payload = OpenRouterProvider.build_payload([{"role": "user", "content": "x"}], [], "deepseek/deepseek-r1")
    assert "reasoning" not in payload


@pytest.mark.parametrize(
    ("env", "missing"),
    [({}, "OPENROUTER_API_KEY"), ({"OPENROUTER_API_KEY": "k"}, "HARNESS_MODEL")],
)
def test_openrouter_names_the_one_setting_that_is_missing(monkeypatch, env, missing):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=missing):
        openrouter_endpoint()


def test_openrouter_request_and_tool_call_parsing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    monkeypatch.setenv("HARNESS_MODEL", "x-ai/grok-4")
    requests = []

    def fake_post(url, payload, api_key, *, timeout):
        requests.append((url, payload, api_key))
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
                            {"id": "c2", "function": {"name": "list_files", "arguments": "not json"}},
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr("harness.provider._post_json", fake_post)
    completion = OpenRouterProvider().complete([{"role": "user", "content": "inspect"}], [])

    url, payload, api_key = requests[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert api_key == "openrouter-test-key"
    assert payload["model"] == "x-ai/grok-4"
    assert [call.name for call in completion.tool_calls] == ["read_file", "list_files"]
    assert completion.tool_calls[0].arguments == {"path": "a.py"}
    assert completion.tool_calls[1].arguments == {}


def test_openrouter_error_body_fails_loudly(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("HARNESS_MODEL", "m")
    monkeypatch.setattr(
        "harness.provider._post_json", lambda *a, **k: {"error": {"message": "model not found"}}
    )
    with pytest.raises(RuntimeError, match="model not found"):
        OpenRouterProvider().complete([{"role": "user", "content": "x"}], [])


def test_openrouter_key_is_redacted_from_provider_messages(monkeypatch):
    secret = "openrouter-secret-value"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    payload = OpenRouterProvider.build_payload(
        [
            {"role": "user", "content": f"key is {secret}"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "name": "run_shell", "arguments": {"command": f"echo {secret}"}}],
            },
        ],
        [],
        "m",
    )
    assert secret not in str(payload)
