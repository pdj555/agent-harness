"""Wakeable sessions: the conversation survives the process.

A run's messages live in the store as an append-only log, not only in
the agent's memory. These tests kill an agent mid-conversation, rebuild
it from the store alone, and show the resumed conversation is the one
that was interrupted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness.agents import ClaudeAgent, LoopAgent, TurnContext
from harness.config import Config
from harness.isolation import create_stage
from harness.provider import Completion, DeterministicProvider, ScriptedProvider, ToolCall
from harness.runtime import _wake_agent, execute_run
from harness.store import Store
from tests.helpers import copy_sample, git_init


@pytest.fixture
def ctx(tmp_path: Path) -> TurnContext:
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "wake-test")
    store = Store(tmp_path / "harness.db")
    store.initialize()
    user = store.create_user("ada", "scrypt$not-used$not-used")
    repo = store.list_repos([source])[0]
    run = store.create_run(user.id, repo.id, "fix it")
    return TurnContext(stage=stage, store=store, run_id=run.id)


class RecordingProvider:
    """Delegates to a provider and snapshots every message list it is shown."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = getattr(inner, "model", None)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools):
        self.seen.append([dict(message) for message in messages])
        return self.inner.complete(messages, tools)


def _one_tool_call_script() -> ScriptedProvider:
    return ScriptedProvider(
        [
            Completion(
                tool_calls=[ToolCall(name="list_files", arguments={"pattern": "*.py"}, id="c1")]
            ),
            Completion(text="finished"),
        ]
    )


# ---------------------------------------------------------------------- store


def test_run_events_have_positional_reads(ctx: TurnContext):
    store = ctx.store
    for index in range(5):
        store.add_event(ctx.run_id, "evidence", f"note {index}")
    events = store.get_events(ctx.run_id)
    assert [event["detail"] for event in events] == [f"note {index}" for index in range(5)]
    assert store.get_events(ctx.run_id, offset=3) == events[3:]
    assert store.get_events(ctx.run_id, offset=1, limit=2) == events[1:3]
    run = store.get_run(ctx.run_id)
    assert run is not None
    assert [(e["kind"], e["detail"]) for e in run.events] == [
        (e["kind"], e["detail"]) for e in events
    ]


def test_loop_conversation_is_persisted_and_readable_in_slices(ctx: TurnContext):
    agent = LoopAgent(_one_tool_call_script())
    assert agent.turn("fix it", ctx) == "finished"

    stored = ctx.store.get_messages(ctx.run_id)
    assert stored == agent.messages
    assert [message["role"] for message in stored] == ["system", "user", "assistant", "tool"]
    assert ctx.store.get_messages(ctx.run_id, offset=2) == stored[2:]
    assert ctx.store.get_messages(ctx.run_id, offset=1, limit=2) == stored[1:3]
    assert ctx.store.get_session_state(ctx.run_id) == {"steps": 2}


# ----------------------------------------------------------------------- wake


def test_kill_and_wake_restores_the_conversation(ctx: TurnContext, tmp_path: Path):
    agent = LoopAgent(_one_tool_call_script())
    agent.turn("fix it", ctx)
    before = [dict(message) for message in agent.messages]
    steps = agent.steps

    # The process dies here. What follows shares nothing with the objects
    # above except the database file: a new store connection, a new agent.
    store2 = Store(tmp_path / "harness.db")
    store2.initialize()
    run = store2.get_run(ctx.run_id)
    assert run is not None
    recorder = RecordingProvider(ScriptedProvider([Completion(text="resumed and done")]))
    woken, resumed = _wake_agent(LoopAgent(recorder), run=run, store=store2)
    assert resumed is True
    assert isinstance(woken, LoopAgent)
    assert woken.messages == before
    assert woken.steps == steps

    ctx2 = TurnContext(stage=ctx.stage, store=store2, run_id=ctx.run_id)
    assert woken.turn("continue", ctx2) == "resumed and done"
    assert recorder.seen[0][: len(before)] == before
    assert recorder.seen[0][len(before)] == {"role": "user", "content": "continue"}
    assert store2.get_messages(ctx.run_id) == woken.messages


def test_terminal_runs_are_not_woken(ctx: TurnContext):
    ctx.store.update_run(ctx.run_id, status="completed")
    run = ctx.store.get_run(ctx.run_id)
    assert run is not None
    agent = LoopAgent(ScriptedProvider([]))
    same, resumed = _wake_agent(agent, run=run, store=ctx.store)
    assert same is agent
    assert resumed is False


def test_vendor_session_id_is_recorded_and_restored(ctx: TurnContext, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    agent = ClaudeAgent()
    agent.session_id = "sess-123"
    agent._record_session(ctx)
    run = ctx.store.get_run(ctx.run_id)
    assert run is not None
    assert run.vendor_session_id == "sess-123"

    fresh = ClaudeAgent()
    assert fresh.session_id is None
    woken, resumed = _wake_agent(fresh, run=run, store=ctx.store)
    assert resumed is True
    assert isinstance(woken, ClaudeAgent)
    assert woken.session_id == "sess-123"


# -------------------------------------------------------------------- runtime


class CrashOnSecondCall:
    """Dies the way a killed process dies: mid-turn, no cleanup, no status write."""

    name = "crash"

    def __init__(self) -> None:
        self.inner = ScriptedProvider(
            [
                Completion(
                    tool_calls=[ToolCall(name="list_files", arguments={"pattern": "*.py"}, id="c1")]
                )
            ]
        )
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 2:
            raise KeyboardInterrupt
        return self.inner.complete(messages, tools)


def _setup(tmp_path: Path) -> tuple[Store, Config, str]:
    source = copy_sample(tmp_path / "sample-repo")
    git_init(source)
    config = Config(
        host="127.0.0.1",
        port=7465,
        workspace_roots=[source],
        provider_name="scripted",
        data_dir=tmp_path / "data",
        auto_publish=False,
    )
    store = Store(config.data_dir / "harness.db")
    store.initialize()
    user = store.create_user("ada", "scrypt$not-used$not-used")
    repo = store.list_repos(config.workspace_roots)[0]
    run = store.create_run(user.id, repo.id, "Find the reliability bug, fix it, and prove it.")
    return store, config, run.id


def test_execute_run_wakes_a_crashed_run_without_repeating_tool_calls(tmp_path: Path):
    store, config, run_id = _setup(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        execute_run(run_id, store=store, config=config, provider=CrashOnSecondCall())

    crashed = store.get_run(run_id)
    assert crashed is not None
    assert crashed.status == "running"
    before = store.get_messages(run_id)
    assert [message["role"] for message in before] == ["system", "user", "assistant", "tool"]

    recorder = RecordingProvider(DeterministicProvider())
    execute_run(run_id, store=store, config=config, provider=recorder)

    finished = store.get_run(run_id)
    assert finished is not None
    assert finished.status == "completed"
    # The woken agent was shown the crashed conversation, not a fresh start.
    assert recorder.seen[0][: len(before)] == before
    # The tool call that completed before the crash was not repeated.
    messages = store.get_messages(run_id)
    list_results = [
        message
        for message in messages
        if message.get("role") == "tool" and message.get("name") == "list_files"
    ]
    assert len(list_results) == 1
    assert any("Woke the run" in event["detail"] for event in store.get_events(run_id))
