from __future__ import annotations

from pathlib import Path

import pytest
from harness.config import add_extra_root, has_live_key, load_config, load_extra_roots
from harness.provider import get_provider


def test_add_extra_root_is_merged_into_loaded_workspace(tmp_path: Path, monkeypatch):
    repo = tmp_path / "money-repo"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HARNESS_PROVIDER", raising=False)
    added = add_extra_root(tmp_path / ".harness", repo)
    assert added == repo.resolve()
    assert repo.resolve() in load_extra_roots(tmp_path / ".harness")
    config = load_config(prefer_live=False)
    assert repo.resolve() in [path.resolve() for path in config.workspace_roots]


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ({"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "OPENROUTER_API_KEY": "r"}, "claude"),
        ({"OPENAI_API_KEY": "o", "OPENROUTER_API_KEY": "r"}, "codex"),
        ({"OPENROUTER_API_KEY": "r"}, "openrouter"),
        ({}, "deterministic"),
    ],
)
def test_prefer_live_picks_engines_in_a_fixed_order(tmp_path: Path, monkeypatch, keys, expected):
    monkeypatch.chdir(tmp_path)
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    config = load_config()
    assert config.provider_name == expected
    assert has_live_key() is bool(keys)


def test_explicit_provider_pin_beats_key_order(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "r")
    monkeypatch.setenv("HARNESS_PROVIDER", "openrouter")
    assert load_config().provider_name == "openrouter"


def test_demo_path_keeps_deterministic_without_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    config = load_config(prefer_live=False)
    assert config.provider_name == "deterministic"


def test_toml_provider_name_pins_openrouter(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "harness.toml"
    config_path.write_text('[provider]\nname = "openrouter"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    monkeypatch.setenv("HARNESS_MODEL", "qwen/qwen3-coder")
    requests = []

    def fake_post(url, payload, api_key, *, timeout):
        requests.append((url, payload, api_key, timeout))
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr("harness.provider._post_json", fake_post)

    config = load_config(config_path)
    completion = get_provider(config.provider_name).complete([{"role": "user", "content": "inspect"}], [])

    assert config.provider_name == "openrouter"
    assert completion.text == "ok"
    url, payload, api_key, _timeout = requests[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert api_key == "openrouter-test-key"
    assert payload["model"] == "qwen/qwen3-coder"


def test_check_command_and_timeout_come_from_workspace_config(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "harness.toml").write_text(
        '[workspace]\nroots = ["."]\ncheck_command = "npm test"\ncheck_timeout = 45\n',
        encoding="utf-8",
    )
    config = load_config(prefer_live=False)
    assert config.check_command == "npm test"
    assert config.check_timeout == 45
    assert load_config(prefer_live=False, path=None).check_command == "npm test"
