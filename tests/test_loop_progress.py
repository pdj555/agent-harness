"""Loop progress: stall detection and provider-failure recovery.

These drive LoopAgent.turn, the shipped entry point, with scripted
providers. A circling provider must be stopped as stalled long before the
step budget is gone; a provider that fails transiently must be retried,
with the recovery recorded, instead of killing the run.
"""

from __future__ import annotations

from dataclasses import replace
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
    from harness.provider import BUGGY_PRIORITY, FIXED_PRIORITY, ScriptedProvider

    agent = LoopAgent(
        ScriptedProvider(
            [
                _call("read_file", {"path": "tracker.py"}),
                _call("read_file", {"path": "test_tracker.py"}),
                _call("read_file", {"path": "README.md"}),
                _call(
                    "edit_file",
                    {"path": "tracker.py", "old": BUGGY_PRIORITY, "new": FIXED_PRIORITY},
                ),
                Completion(text="done"),
            ]
        )
    )
    assert agent.turn("fix it", ctx) == "done"
    assert agent.steps == 5


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


class RefusingProvider:
    name = "refusing"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        self.calls += 1
        raise RuntimeError("Anthropic response did not complete: refusal")


def test_a_terminal_response_is_not_retried(ctx: TurnContext):
    """A refusal is a completed response with a terminal stop reason, not
    a transient failure: one call, and the run fails with its text intact
    so the eval monitor can still count it as a refusal."""
    provider = RefusingProvider()
    agent = LoopAgent(provider)
    with pytest.raises(AgentFailed, match="did not complete: refusal"):
        agent.turn("fix it", ctx)
    assert provider.calls == 1


def test_a_circling_reviewer_uses_its_whole_step_budget(ctx: TurnContext):
    """The reviewer's bounded turn keeps its step-limit contract: no early
    stall stop, so a reviewer that never answers reports the step limit."""
    agent = LoopAgent(CirclingProvider(), role="reviewer")
    with pytest.raises(StepLimit, match="used all 6 steps without a final answer"):
        agent.turn("review it", replace(ctx, quiet=True, max_steps=6))
    assert agent.steps == 6


def test_provider_usage_is_recorded_on_the_run(ctx: TurnContext):
    class MeteredProvider:
        name = "metered"

        def __init__(self) -> None:
            self.usage: dict[str, int] = {}

        def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
            self.usage["input_tokens"] = self.usage.get("input_tokens", 0) + 100
            self.usage["output_tokens"] = self.usage.get("output_tokens", 0) + 25
            return Completion(text="done")

    agent = LoopAgent(MeteredProvider())
    assert agent.turn("fix it", ctx) == "done"
    run = ctx.store.get_run(ctx.run_id)
    assert run is not None
    assert run.usage == {"input_tokens": 100, "output_tokens": 25}


class BigOutputProvider:
    name = "big-output"

    def __init__(self, steps: int) -> None:
        self.steps_left = steps
        self.seen_tool_chars: list[int] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        self.seen_tool_chars.append(
            sum(len(m.get("content") or "") for m in messages if m["role"] == "tool")
        )
        if self.steps_left == 0:
            return Completion(text="done")
        self.steps_left -= 1
        return _call(
            "run_shell",
            {"command": f"python3 -c \"print('x' * 5000)\" # step {self.steps_left}"},
        )


def test_compaction_shrinks_old_tool_results_and_keeps_the_newest(ctx: TurnContext):
    provider = BigOutputProvider(steps=6)
    agent = LoopAgent(provider, compact_after_chars=6_000)
    assert agent.turn("fix it", ctx) == "done"
    # Without compaction the final call would re-send ~30k chars of tool output.
    assert provider.seen_tool_chars[-1] < 12_000
    tool_messages = [m for m in agent.messages if m["role"] == "tool"]
    assert any(m["content"].startswith("[compacted:") for m in tool_messages)
    assert len(tool_messages[-1]["content"]) > 4_000


def test_compaction_leaves_the_stored_trajectory_complete(ctx: TurnContext):
    provider = BigOutputProvider(steps=6)
    agent = LoopAgent(provider, compact_after_chars=6_000)
    assert agent.turn("fix it", ctx) == "done"
    stored = ctx.store.get_messages(ctx.run_id)
    assert len(stored) == len(agent.messages)
    stored_tools = [m for m in stored if m["role"] == "tool"]
    assert all(not m["content"].startswith("[compacted:") for m in stored_tools)
    restored = LoopAgent.restore(BigOutputProvider(steps=0), stored, steps=agent.steps)
    restored_tools = [m for m in restored.messages if m["role"] == "tool"]
    assert len(restored_tools[-1]["content"]) > 4_000


class SoakProvider:
    """Emits a distinct shell call per step; every tenth output is large."""

    name = "soak"

    def __init__(self, total: int) -> None:
        self.total = total
        self.issued = 0
        self.max_tool_chars_seen = 0

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        self.max_tool_chars_seen = max(
            self.max_tool_chars_seen,
            sum(len(m.get("content") or "") for m in messages if m["role"] == "tool"),
        )
        if self.issued >= self.total:
            return Completion(text="soak complete")
        self.issued += 1
        size = 5000 if self.issued % 10 == 0 else 100
        return _call(
            "run_shell",
            {"command": f"python3 -c \"print('x' * {size})\" # soak {self.issued}"},
        )


def test_long_run_wakes_midway_and_finishes_with_bounded_context(ctx: TurnContext):
    provider = SoakProvider(total=100)
    agent = LoopAgent(provider, compact_after_chars=20_000)
    # First leg: exhaust a 60-step budget, as a crashed or paused run would.
    with pytest.raises(StepLimit):
        agent.turn("fix it", replace(ctx, max_steps=60))
    assert agent.steps == 60
    # Wake: rebuild from the stored trajectory and session state, keep going.
    stored = ctx.store.get_messages(ctx.run_id)
    state = ctx.store.get_session_state(ctx.run_id)
    assert state is not None
    woken = LoopAgent.restore(provider, stored, steps=state["steps"], compact_after_chars=20_000)
    assert woken.turn("continue", replace(ctx, max_steps=120)) == "soak complete"
    assert woken.steps == 101  # 100 tool steps plus the final answer step
    # Compaction held the re-sent tool output near the budget all run long.
    assert provider.max_tool_chars_seen < 40_000


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
    agent = LoopAgent(
        ScriptedProvider(
            [
                Completion(tool_calls=calls),
                Completion(text="done"),
                # The no-edit stop above earns one nudge; answer it with a
                # mutating call so the run can finish.
                _call("edit_file", {"path": "tracker.py", "old": "a", "new": "b"}),
                Completion(text="done"),
            ]
        )
    )
    started = time.time()
    assert agent.turn("fix it", ctx) == "done"
    elapsed = time.time() - started
    # Serial would take 0.9s for the reads plus 0.3s for the edit.
    assert elapsed < 0.9
    tool_messages = [m for m in agent.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages[:3]] == ["c0", "c1", "c2"]
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


def _no_edit_nudges(agent: LoopAgent) -> list[dict]:
    return [
        message
        for message in agent.messages
        if message["role"] == "user" and "without having changed anything" in message["content"]
    ]


def test_no_edit_stop_is_nudged_once_and_the_edit_lands(ctx: TurnContext):
    """The premature-stop class: inspect, then finish in text with no edit.
    The first such stop earns one nudge; the run then makes its edit."""
    from harness.provider import BUGGY_PRIORITY, FIXED_PRIORITY, ScriptedProvider

    agent = LoopAgent(
        ScriptedProvider(
            [
                _call("read_file", {"path": "tracker.py"}),
                Completion(text="I would swap the priority labels in tracker.py."),
                _call(
                    "edit_file",
                    {"path": "tracker.py", "old": BUGGY_PRIORITY, "new": FIXED_PRIORITY},
                ),
                Completion(text="done"),
            ]
        )
    )
    assert agent.turn("fix it", ctx) == "done"
    assert agent.steps == 4
    assert len(_no_edit_nudges(agent)) == 1
    assert FIXED_PRIORITY in (ctx.stage.root / "tracker.py").read_text(encoding="utf-8")
    events = ctx.store.get_events(ctx.run_id)
    assert any("no-edit stop" in event["detail"] for event in events)


def test_a_second_no_edit_stop_stands(ctx: TurnContext):
    """One nudge per turn: a model that again stops in text, plainly saying
    no change is needed, is allowed to finish."""
    from harness.provider import ScriptedProvider

    agent = LoopAgent(
        ScriptedProvider(
            [
                _call("read_file", {"path": "tracker.py"}),
                Completion(text="looks fine"),
                Completion(text="no change needed: the code already matches the tests"),
            ]
        )
    )
    assert agent.turn("fix it", ctx) == "no change needed: the code already matches the tests"
    assert agent.steps == 3
    assert len(_no_edit_nudges(agent)) == 1


def test_an_immediate_text_answer_is_not_nudged(ctx: TurnContext):
    """A run that never touched a tool is answering, not stopping mid-work."""
    from harness.provider import ScriptedProvider

    agent = LoopAgent(ScriptedProvider([Completion(text="the answer")]))
    assert agent.turn("what model is this?", ctx) == "the answer"
    assert agent.steps == 1
    assert _no_edit_nudges(agent) == []


def test_a_reviewer_no_edit_stop_is_not_nudged(ctx: TurnContext):
    """A reviewer's text verdict is its whole job; it never edits."""
    from harness.provider import ScriptedProvider

    agent = LoopAgent(
        ScriptedProvider(
            [
                _call("read_file", {"path": "tracker.py"}),
                Completion(text='{"ship": true, "findings": []}'),
            ]
        ),
        role="reviewer",
    )
    assert agent.turn("review it", replace(ctx, quiet=True)) == '{"ship": true, "findings": []}'
    assert agent.steps == 2
    assert _no_edit_nudges(agent) == []
