"""Loop progress: stall detection and provider-failure recovery.

These drive LoopAgent.turn, the shipped entry point, with scripted
providers. A circling provider must be stopped as stalled long before the
step budget is gone; a provider that fails transiently must be retried,
with the recovery recorded, instead of killing the run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness.agents import AgentFailed, LoopAgent, StepLimit, TurnContext
from harness.isolation import create_stage
from harness.provider import Completion, ToolCall
from harness.store import Store
from tests.helpers import copy_sample, git_init


@pytest.fixture
def ctx(tmp_path: Path) -> TurnContext:
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "loop-progress")
    store = Store(tmp_path / "harness.db")
    store.initialize()
    user = store.create_user("ada", "scrypt$not-used$not-used")
    repo = store.list_repos([source])[0]
    run = store.create_run(user.id, repo.id, "fix it")
    return TurnContext(stage=stage, store=store, run_id=run.id)


def _call(name: str, arguments: dict) -> Completion:
    return Completion(tool_calls=[ToolCall(name=name, arguments=arguments, id="c1")])


class CirclingProvider:
    name = "circling"

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        return _call("read_file", {"path": "tracker.py"})


class FlakyProvider:
    name = "flaky"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("connection reset")
        return Completion(text="recovered")


def test_circling_run_is_stopped_as_stalled_before_budget(ctx: TurnContext):
    agent = LoopAgent(CirclingProvider())
    with pytest.raises(StepLimit, match="stalled"):
        agent.turn("fix it", ctx)
    assert agent.steps == 5
    assert agent.steps < ctx.max_steps


def test_circling_run_gets_one_warning_before_the_stop(ctx: TurnContext):
    agent = LoopAgent(CirclingProvider())
    with pytest.raises(StepLimit):
        agent.turn("fix it", ctx)
    warnings = [
        message
        for message in agent.messages
        if message["role"] == "user" and "repeated the same tool calls" in message["content"]
    ]
    assert len(warnings) == 1


def test_progressing_run_is_not_flagged_as_stalled(ctx: TurnContext):
    from harness.provider import ScriptedProvider

    agent = LoopAgent(
        ScriptedProvider(
            [
                _call("read_file", {"path": "tracker.py"}),
                _call("read_file", {"path": "test_tracker.py"}),
                _call("read_file", {"path": "README.md"}),
                Completion(text="done"),
            ]
        )
    )
    assert agent.turn("fix it", ctx) == "done"
    assert agent.steps == 4


def test_one_provider_failure_is_retried_and_recovers(ctx: TurnContext):
    provider = FlakyProvider(failures=1)
    agent = LoopAgent(provider)
    assert agent.turn("fix it", ctx) == "recovered"
    assert provider.calls == 2
    events = ctx.store.get_events(ctx.run_id)
    assert any("retry 1 of 2" in event["detail"] for event in events)


def test_two_failures_recover_with_a_note_in_the_conversation(ctx: TurnContext):
    provider = FlakyProvider(failures=2)
    agent = LoopAgent(provider)
    assert agent.turn("fix it", ctx) == "recovered"
    assert provider.calls == 3
    notes = [
        message
        for message in agent.messages
        if message["role"] == "user" and "previous model call failed" in message["content"]
    ]
    assert len(notes) == 1


def test_three_failures_still_fail_the_run(ctx: TurnContext):
    provider = FlakyProvider(failures=99)
    agent = LoopAgent(provider)
    with pytest.raises(AgentFailed):
        agent.turn("fix it", ctx)
    assert provider.calls == 3


def test_read_only_step_runs_concurrently_and_records_in_order(ctx: TurnContext, monkeypatch):
    import time

    import harness.agents as agents_module
    from harness.provider import ScriptedProvider

    def slow_execute(name, arguments, **kwargs):
        time.sleep(0.3)
        return f"contents of {arguments['path']}"

    monkeypatch.setattr(agents_module, "execute", slow_execute)
    calls = [
        ToolCall(name="read_file", arguments={"path": path}, id=f"c{i}")
        for i, path in enumerate(("tracker.py", "test_tracker.py", "README.md"))
    ]
    agent = LoopAgent(ScriptedProvider([Completion(tool_calls=calls), Completion(text="done")]))
    started = time.time()
    assert agent.turn("fix it", ctx) == "done"
    elapsed = time.time() - started
    assert elapsed < 0.6  # serial would take 0.9s of tool time alone
    tool_messages = [m for m in agent.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c0", "c1", "c2"]
    assert tool_messages[0]["content"] == "contents of tracker.py"


def test_step_with_a_mutating_call_stays_serial(ctx: TurnContext, monkeypatch):
    import time

    import harness.agents as agents_module
    from harness.provider import ScriptedProvider

    def slow_execute(name, arguments, **kwargs):
        time.sleep(0.3)
        return "ok"

    monkeypatch.setattr(agents_module, "execute", slow_execute)
    calls = [
        ToolCall(name="read_file", arguments={"path": "tracker.py"}, id="c0"),
        ToolCall(name="edit_file", arguments={"path": "tracker.py", "old": "a", "new": "b"}, id="c1"),
    ]
    agent = LoopAgent(ScriptedProvider([Completion(tool_calls=calls), Completion(text="done")]))
    started = time.time()
    assert agent.turn("fix it", ctx) == "done"
    assert time.time() - started >= 0.6
