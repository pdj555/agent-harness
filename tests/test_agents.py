from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from harness.agents import (
    AgentFailed,
    ClaudeAgent,
    CodexAgent,
    LoopAgent,
    RunStopped,
    TurnContext,
    default_agent_name,
    describe,
    make_agent,
    make_reviewer,
    parse_verdict,
)
from harness.isolation import create_stage
from harness.provider import DeterministicProvider, OpenRouterProvider
from harness.store import Store
from tests.helpers import copy_sample, git_init


@pytest.fixture
def ctx(tmp_path: Path) -> TurnContext:
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "agent-test")
    store = Store(tmp_path / "harness.db")
    store.initialize()
    user = store.create_user("ada", "scrypt$not-used$not-used")
    repo = store.list_repos([source])[0]
    run = store.create_run(user.id, repo.id, "fix it")
    return TurnContext(stage=stage, store=store, run_id=run.id)


# ---------------------------------------------------------------- selection


def test_make_agent_wraps_loop_providers_and_builds_frontier_agents(monkeypatch):
    assert isinstance(make_agent("deterministic"), LoopAgent)
    assert isinstance(make_agent("anything", DeterministicProvider()), LoopAgent)
    assert isinstance(make_agent("openrouter").provider, OpenRouterProvider)
    with pytest.raises(AgentFailed, match="OPENAI_API_KEY"):
        make_agent("codex")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    assert isinstance(make_agent("codex"), CodexAgent)
    with pytest.raises(AgentFailed, match="ANTHROPIC_API_KEY"):
        make_agent("claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("HARNESS_MODEL", "claude-test")
    assert isinstance(make_agent("claude"), ClaudeAgent)
    with pytest.raises(ValueError, match="provider"):
        make_agent("ollama")


@pytest.mark.parametrize(
    ("principal", "keys", "expected"),
    [
        ("claude", {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"}, "codex"),
        ("codex", {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"}, "claude"),
        ("openrouter", {"ANTHROPIC_API_KEY": "a"}, "claude"),
        ("openrouter", {"OPENAI_API_KEY": "o"}, "codex"),
        ("claude", {"ANTHROPIC_API_KEY": "a"}, "claude"),
        ("codex", {"OPENAI_API_KEY": "o"}, None),
    ],
)
def test_reviewer_is_the_other_vendor(monkeypatch, principal, keys, expected):
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HARNESS_MODEL", "claude-test")
    monkeypatch.setenv("HARNESS_REVIEWER_MODEL", "claude-review-test")
    reviewer = make_reviewer(principal)
    assert (reviewer.name if reviewer else None) == expected


def test_reviewer_override_and_off(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("HARNESS_REVIEWER", "none")
    assert make_reviewer("claude") is None
    monkeypatch.setenv("HARNESS_REVIEWER", "claude")
    monkeypatch.setenv("HARNESS_MODEL", "claude-test")
    assert make_reviewer("claude").name == "claude"
    monkeypatch.setenv("HARNESS_REVIEWER", "grok")
    with pytest.raises(AgentFailed):
        make_reviewer("claude")


def test_default_agent_name_and_describe(monkeypatch):
    assert default_agent_name() == "deterministic"
    monkeypatch.setenv("HARNESS_MODEL", "claude-opus-5-5")
    info = describe("claude")
    assert info["model"] == "claude-opus-5-5"
    assert info["engine"] == "Anthropic Messages API on authority-checked harness tools"


@pytest.mark.parametrize(
    ("text", "ship", "findings"),
    [
        ('{"ship": true, "findings": []}', True, []),
        ('Verdict:\n```json\n{"ship": false, "findings": ["drops a test"]}\n```', False, ["drops a test"]),
        ("looks great to me", False, ["reviewer returned no readable verdict"]),
        ('{"ship": "yes"}', False, ["reviewer returned no readable verdict"]),
        ('{"ship": true}', False, ["reviewer returned no readable verdict"]),
        ('{"ship": true, "findings": "unsafe"}', False, ["reviewer returned no readable verdict"]),
        ('{"ship": true, "findings": ["unsafe"]}', False, ["unsafe"]),
        ('{"ship": true, "findings": [42]}', False, ["reviewer returned no readable verdict"]),
    ],
)
def test_parse_verdict_blocks_anything_unreadable(text, ship, findings):
    assert parse_verdict(text) == {"ship": ship, "findings": findings}


# ---------------------------------------------------------------- claude


def _claude_setup(monkeypatch, replies):
    from harness.anthropic import AnthropicProvider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("HARNESS_MODEL", "claude-test")
    payloads = []

    def request(self, payload):
        payloads.append(payload)
        return replies.pop(0)

    monkeypatch.setattr(AnthropicProvider, "_request", request)
    return payloads


def _tool(name, arguments, call_id="tool-1"):
    return {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": call_id, "name": name, "input": arguments}],
    }


def _text(text):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def test_claude_uses_harness_authority_and_keeps_history(monkeypatch, ctx: TurnContext):
    before = (ctx.stage.source / "tracker.py").read_text()
    payloads = _claude_setup(monkeypatch, [
        _tool("edit_file", {"path": "../escape.py", "old": "", "new": "escaped"}),
        _tool("edit_file", {"path": "tracker.py", "old": 'return "low"', "new": 'return "high"'}),
        _text("Ready for checks."),
        _text("Repair considered."),
    ])
    agent = ClaudeAgent()
    assert isinstance(agent, LoopAgent)
    assert agent.turn("fix it", ctx) == "Ready for checks."
    assert agent.turn("check this finding", ctx) == "Repair considered."
    assert (ctx.stage.source / "tracker.py").read_text() == before
    assert (ctx.stage.root / "tracker.py").read_text() != before
    assert not (ctx.stage.root.parent / "escape.py").exists()
    denied = payloads[1]["messages"][-1]["content"][0]
    assert denied["is_error"] is True
    assert "outside the workspace" in denied["content"]
    assert payloads[-1]["messages"][0]["content"][0]["text"] == "fix it"
    assert payloads[-1]["messages"][-1]["content"][0]["text"] == "check this finding"
    events = [event["detail"] for event in ctx.store.get_run(ctx.run_id).events]
    assert "Edited tracker.py" in events


def test_claude_review_is_fresh_structured_and_read_only(monkeypatch, ctx: TurnContext):
    payloads = _claude_setup(monkeypatch, [
        _text("principal private history"),
        _tool("read_file", {"path": "tracker.py"}),
        _text('{"ship": true, "findings": []}'),
    ])
    agent = ClaudeAgent()
    agent.turn("principal instruction", ctx)
    assert agent.review("independent diff evidence", ctx) == {"ship": True, "findings": []}
    first_review = payloads[1]
    assert "principal private history" not in str(first_review)
    assert "principal instruction" not in str(first_review)
    assert first_review["output_config"]["format"]["type"] == "json_schema"
    assert {tool["name"] for tool in first_review["tools"]} == {
        "read_file", "search", "list_files", "git_diff", "git_status"
    }
    assert "classify_priority" in payloads[2]["messages"][-1]["content"][0]["content"]


def test_reviewer_cannot_mutate_or_change_plan_even_if_provider_requests_it(ctx: TurnContext):
    from dataclasses import replace

    from harness.provider import ScriptedProvider

    agent = LoopAgent(ScriptedProvider([]), role="reviewer")
    before = (ctx.stage.root / "tracker.py").read_text()
    quiet = replace(ctx, quiet=True)
    result = agent._run_tool("edit_file", "edit", {
        "path": "tracker.py", "old": 'return "low"', "new": 'return "high"'
    }, quiet)
    assert result["is_error"] is True
    assert (ctx.stage.root / "tracker.py").read_text() == before
    result = agent._run_tool("set_plan", "plan", {"steps": ["wrong"]}, quiet)
    assert result["is_error"] is True
    assert ctx.store.get_run(ctx.run_id).plan == []


def test_claude_stops_before_executing_response_tools(monkeypatch, ctx: TurnContext):
    from harness.anthropic import AnthropicProvider

    _claude_setup(monkeypatch, [])
    before = (ctx.stage.root / "tracker.py").read_text()

    def stop_during_request(self, payload):
        ctx.store.request_stop(ctx.run_id)
        return _tool("edit_file", {"path": "tracker.py", "old": 'return "low"', "new": 'return "high"'})

    monkeypatch.setattr(AnthropicProvider, "_request", stop_during_request)
    with pytest.raises(RunStopped):
        ClaudeAgent().turn("fix it", ctx)
    assert (ctx.stage.root / "tracker.py").read_text() == before
    assert any(
        "edit_file skipped because stop was requested" in event["detail"]
        for event in ctx.store.get_run(ctx.run_id).events
    )


def test_claude_step_budget_is_enforced(monkeypatch, ctx: TurnContext):
    from harness.agents import StepLimit

    _claude_setup(monkeypatch, [_tool("read_file", {"path": "tracker.py"})])
    ctx.max_steps = 1
    with pytest.raises(StepLimit):
        ClaudeAgent().turn("fix it", ctx)


def test_stop_during_api_failure_finishes_stopped(monkeypatch, ctx: TurnContext):
    from harness.anthropic import AnthropicProvider

    _claude_setup(monkeypatch, [])

    def stop_then_fail(self, payload):
        ctx.store.request_stop(ctx.run_id)
        raise RuntimeError("connection failed")

    monkeypatch.setattr(AnthropicProvider, "_request", stop_then_fail)
    with pytest.raises(RunStopped):
        ClaudeAgent().turn("fix it", ctx)


# ---------------------------------------------------------------- codex


def _event(method: str, item=None, turn=None):
    payload = SimpleNamespace(item=item, turn=turn)
    return SimpleNamespace(method=method, payload=payload)


class FakeHandle:
    def __init__(self, events, on_event=None):
        self.events = events
        self.on_event = on_event
        self.interrupted = False

    def stream(self):
        for event in self.events:
            if self.on_event:
                self.on_event(event)
            yield event

    def interrupt(self):
        self.interrupted = True


class FakeCodex:
    def __init__(self, handle):
        self.handle = handle
        self.thread_kwargs = []
        self.turns = []

    def thread_start(self, **kwargs):
        self.thread_kwargs.append(kwargs)
        return SimpleNamespace(turn=lambda prompt: self.turns.append(prompt) or self.handle)

    def close(self):
        pass


def test_codex_agent_runs_in_a_workspace_sandbox_and_records_work(monkeypatch, ctx: TurnContext):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    from openai_codex import ApprovalMode, Sandbox

    events = [
        _event("item/started", SimpleNamespace(type="commandExecution", command="pytest -q")),
        _event("item/completed", SimpleNamespace(type="commandExecution")),
        _event(
            "item/completed",
            SimpleNamespace(type="fileChange", changes=[SimpleNamespace(path="tracker.py")]),
        ),
        _event("item/completed", SimpleNamespace(type="agentMessage", text="Fixed it.")),
        _event("turn/completed", turn=SimpleNamespace(error=None, status=SimpleNamespace(value="completed"))),
    ]
    fake = FakeCodex(FakeHandle(events))
    agent = CodexAgent()
    monkeypatch.setattr(agent, "_client", lambda _root: fake)

    assert agent.turn("fix it", ctx) == "Fixed it."
    agent.turn("again", ctx)

    assert len(fake.thread_kwargs) == 1  # one thread, resumed across turns
    kwargs = fake.thread_kwargs[0]
    assert kwargs["cwd"] == str(ctx.stage.root)
    assert kwargs["sandbox"] is Sandbox.workspace_write
    assert kwargs["approval_mode"] is ApprovalMode.deny_all
    assert fake.turns == ["fix it", "again"]
    events_seen = [event["detail"] for event in ctx.store.get_run(ctx.run_id).events]
    assert "Ran pytest -q" in events_seen and "Edited tracker.py" in events_seen


def test_codex_agent_interrupts_on_stop(monkeypatch, ctx: TurnContext):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    handle = FakeHandle(
        [_event("item/started", SimpleNamespace(type="commandExecution", command="rm -rf build"))],
        on_event=lambda _e: ctx.store.request_stop(ctx.run_id),
    )
    agent = CodexAgent()
    monkeypatch.setattr(agent, "_client", lambda _root: FakeCodex(handle))
    with pytest.raises(RunStopped):
        agent.turn("fix it", ctx)
    assert handle.interrupted


def test_codex_turn_error_fails_the_turn(monkeypatch, ctx: TurnContext):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    failed = SimpleNamespace(error=SimpleNamespace(message="quota"))
    handle = FakeHandle([_event("turn/completed", turn=failed)])
    agent = CodexAgent()
    monkeypatch.setattr(agent, "_client", lambda _root: FakeCodex(handle))
    with pytest.raises(AgentFailed, match="quota"):
        agent.turn("fix it", ctx)


def test_codex_turn_that_is_not_completed_fails(monkeypatch, ctx: TurnContext):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    ended = SimpleNamespace(error=None, status=SimpleNamespace(value="interrupted"))
    agent = CodexAgent()
    fake = FakeCodex(FakeHandle([_event("turn/completed", turn=ended)]))
    monkeypatch.setattr(agent, "_client", lambda _root: fake)
    with pytest.raises(AgentFailed, match="interrupted"):
        agent.turn("fix it", ctx)


def test_cross_vendor_review_never_inherits_the_principal_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("HARNESS_MODEL", "qwen/qwen3-coder")
    with pytest.raises(AgentFailed, match="HARNESS_REVIEWER_MODEL"):
        make_reviewer("openrouter")
    assert make_reviewer("claude").model is None  # Codex keeps its vendor default
    monkeypatch.setenv("HARNESS_REVIEWER_MODEL", "claude-review-test")
    assert make_reviewer("openrouter").model == "claude-review-test"
    assert ClaudeAgent().model == "qwen/qwen3-coder"


def test_anthropic_only_run_uses_same_model_in_a_separate_reviewer(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("HARNESS_MODEL", "claude-test")
    reviewer = make_reviewer("claude")
    assert reviewer.model == "claude-test"
    assert reviewer.role == "reviewer"


def test_agent_env_blanks_host_secrets_and_home(monkeypatch, ctx: TurnContext):
    from harness.agents import agent_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    env = agent_env(ctx.stage, keep="ANTHROPIC_API_KEY")
    assert env["ANTHROPIC_API_KEY"] == "a"
    assert env["OPENAI_API_KEY"] == "" and env["GITHUB_TOKEN"] == "" and env["AWS_SECRET_ACCESS_KEY"] == ""
    assert env["HTTPS_PROXY"] == "http://proxy:3128"
    assert env["HOME"] == str(ctx.stage.home())
    assert env["PATH"]


def test_codex_login_lives_beside_the_stage(monkeypatch, ctx: TurnContext):
    import openai_codex

    monkeypatch.setenv("OPENAI_API_KEY", "o")
    made = {}

    class FakeClient:
        def __init__(self, config):
            made["config"] = config

        def login_api_key(self, key):
            made["key"] = key

    monkeypatch.setattr(openai_codex, "Codex", FakeClient)
    CodexAgent()._client(ctx.stage)
    assert made["key"] == "o"
    assert made["config"].env["CODEX_HOME"].startswith(str(ctx.stage.home()))
    assert made["config"].env["OPENAI_API_KEY"] == ""
