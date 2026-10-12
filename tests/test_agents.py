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
    stage_guard,
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
        ("claude", {"ANTHROPIC_API_KEY": "a"}, None),
        ("codex", {"OPENAI_API_KEY": "o"}, None),
    ],
)
def test_reviewer_is_the_other_vendor(monkeypatch, principal, keys, expected):
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    reviewer = make_reviewer(principal)
    assert (reviewer.name if reviewer else None) == expected


def test_reviewer_override_and_off(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("HARNESS_REVIEWER", "none")
    assert make_reviewer("claude") is None
    monkeypatch.setenv("HARNESS_REVIEWER", "claude")
    assert make_reviewer("claude").name == "claude"
    monkeypatch.setenv("HARNESS_REVIEWER", "grok")
    with pytest.raises(AgentFailed):
        make_reviewer("claude")


def test_default_agent_name_and_describe(monkeypatch):
    assert default_agent_name() == "deterministic"
    monkeypatch.setenv("HARNESS_MODEL", "claude-opus-5-5")
    info = describe("claude")
    assert info["model"] == "claude-opus-5-5"
    assert info["engine"] == "Claude Code via the Claude Agent SDK"


@pytest.mark.parametrize(
    ("text", "ship", "findings"),
    [
        ('{"ship": true, "findings": []}', True, []),
        ('Verdict:\n```json\n{"ship": false, "findings": ["drops a test"]}\n```', False, ["drops a test"]),
        ("looks great to me", False, ["reviewer returned no readable verdict"]),
        ('{"ship": "yes"}', False, ["reviewer returned no readable verdict"]),
    ],
)
def test_parse_verdict_blocks_anything_unreadable(text, ship, findings):
    assert parse_verdict(text) == {"ship": ship, "findings": findings}


# ---------------------------------------------------------------- stage guard


def test_stage_guard_confines_paths_and_honours_stop(ctx: TurnContext):
    guard = stage_guard(ctx, writable=True)
    assert guard("Read", {"file_path": "tracker.py"}) is None
    assert guard("Read", {"file_path": str(ctx.stage.root / "tracker.py")}) is None
    assert "outside the workspace" in guard("Read", {"file_path": "/etc/passwd"})
    assert "outside the workspace" in guard("Edit", {"file_path": "../source/tracker.py"})
    assert "git internals" in guard("Write", {"file_path": ".git/config"})
    assert "outside the workspace" in guard("Grep", {"pattern": "x", "path": "/"})
    assert "not allowed" in guard("Read", {"file_path": "~/.ssh/id_rsa"})
    assert "not allowed" in guard("Write", {"file_path": "$HOME/.bashrc"})
    reviewer = stage_guard(ctx, writable=False)
    assert "not allowed for a reviewer" in reviewer("Edit", {"file_path": "tracker.py"})
    assert "not allowed for a reviewer" in reviewer("Bash", {"command": "ls"})
    ctx.store.request_stop(ctx.run_id)
    assert "stop was requested" in guard("Read", {"file_path": "tracker.py"})


# ---------------------------------------------------------------- claude


def _claude_messages(*, tool_input: dict, is_error: bool = False, errors=None):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

    return [
        AssistantMessage(
            content=[TextBlock(text="fixing"), ToolUseBlock(id="t1", name="Edit", input=tool_input)],
            model="claude",
        ),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=is_error,
            num_turns=2,
            session_id="session-1",
            total_cost_usd=0.25,
            result="Fixed the priority labels.",
            errors=errors,
        ),
    ]


def test_claude_agent_streams_actions_resumes_and_confines(monkeypatch, ctx: TurnContext):
    import claude_agent_sdk

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("OPENROUTER_API_KEY", "r")
    seen_options = []

    async def fake_query(*, prompt, options):
        seen_options.append(options)
        for message in _claude_messages(tool_input={"file_path": "tracker.py"}):
            yield message

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    agent = ClaudeAgent()

    assert agent.turn("fix it", ctx) == "Fixed the priority labels."
    assert agent.turn("again", ctx) == "Fixed the priority labels."

    first, second = seen_options
    assert first.cwd == str(ctx.stage.root)
    assert first.resume is None and second.resume == "session-1"
    assert first.setting_sources == []
    assert first.env["ANTHROPIC_API_KEY"] == "a"
    assert first.env["OPENAI_API_KEY"] == "" and first.env["OPENROUTER_API_KEY"] == ""
    assert "WebFetch" not in first.tools and "Bash" in first.tools
    assert agent.cost_usd == pytest.approx(0.5)
    events = [event["detail"] for event in ctx.store.get_run(ctx.run_id).events]
    assert "Edited tracker.py" in events

    import asyncio

    hook = first.hooks["PreToolUse"][0].hooks[0]
    denied = asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}, "t", None))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    allowed = asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "tracker.py"}}, "t", None))
    assert allowed == {}


def test_claude_agent_error_result_fails_the_turn(monkeypatch, ctx: TurnContext):
    import claude_agent_sdk

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")

    async def fake_query(*, prompt, options):
        for message in _claude_messages(tool_input={}, is_error=True, errors=["overloaded"]):
            yield message

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    with pytest.raises(AgentFailed, match="overloaded"):
        ClaudeAgent().turn("fix it", ctx)


def test_claude_agent_stops_mid_stream(monkeypatch, ctx: TurnContext):
    import claude_agent_sdk

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")

    async def fake_query(*, prompt, options):
        ctx.store.request_stop(ctx.run_id)
        for message in _claude_messages(tool_input={"file_path": "tracker.py"}):
            yield message

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
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


def test_claude_max_turns_is_a_step_limit(monkeypatch, ctx: TurnContext):
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage
    from harness.agents import StepLimit

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")

    async def fake_query(*, prompt, options):
        yield ResultMessage(
            subtype="error_max_turns", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=24,
            session_id="s",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    with pytest.raises(StepLimit):
        ClaudeAgent().turn("fix it", ctx)


def test_reviewers_never_inherit_the_principal_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("HARNESS_MODEL", "qwen/qwen3-coder")
    assert make_reviewer("openrouter").model is None
    assert make_reviewer("claude").model is None
    monkeypatch.setenv("HARNESS_REVIEWER_MODEL", "gpt-6")
    assert make_reviewer("claude").model == "gpt-6"
    assert ClaudeAgent().model == "qwen/qwen3-coder"


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
