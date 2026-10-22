from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness.anthropic import AnthropicProvider
from harness.config import Config
from harness.isolation import Stage
from harness.provider import BUGGY_PRIORITY, FIXED_PRIORITY
from harness.runtime import execute_run
from harness.store import Store
from harness.verification import run_checks


def tool(name: str, arguments: dict, call_id: str) -> dict:
    return {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": call_id, "name": name, "input": arguments}],
    }


def text(value: str) -> dict:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": value}]}


@pytest.mark.parametrize("verdict", [
    {"ship": True, "findings": []},
    {"ship": True, "findings": ["The implementation still has a defect."]},
])
def test_anthropic_only_runtime_requires_real_checks_and_separate_review(
    workspace: Path, config: Config, monkeypatch, verdict: dict
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-runtime-test-key")
    monkeypatch.setenv("HARNESS_MODEL", "claude-test")
    config.provider_name = "claude"
    config.max_repairs = 0
    before = (workspace / "tracker.py").read_text()
    baseline = run_checks(workspace)
    assert baseline.passed is False
    store = Store(config.data_dir / "harness.db")
    store.initialize()
    user = store.create_user("ada", "disabled")
    repo = store.list_repos(config.workspace_roots)[0]
    run = store.create_run(user.id, repo.id, "Fix priority labels and add boundary regression coverage.")
    regression = (
        "from tracker import classify_priority\n\n"
        "def test_all_boundary_pairs():\n"
        "    for impact in range(1, 6):\n"
        "        for urgency in range(1, 6):\n"
        "            expected = ('high' if impact >= 4 and urgency >= 4 else\n"
        "                        'medium' if impact >= 3 or urgency >= 3 else 'low')\n"
        "            assert classify_priority(impact, urgency) == expected\n"
    )
    principal = [
        tool("read_file", {"path": "tracker.py"}, "read"),
        tool("edit_file", {"path": "tracker.py", "old": BUGGY_PRIORITY, "new": FIXED_PRIORITY}, "fix"),
        tool("edit_file", {"path": "test_boundaries.py", "old": "", "new": regression}, "test"),
        text("The change is ready for independent verification."),
    ]
    reviewer = [tool("read_file", {"path": "tracker.py"}, "review-read"), text(json.dumps(verdict))]
    requests = []

    def request(self, payload):
        is_review = "output_config" in payload
        requests.append((is_review, payload))
        return (reviewer if is_review else principal).pop(0)

    monkeypatch.setattr(AnthropicProvider, "_request", request)
    execute_run(run.id, store=store, config=config)
    finished = store.get_run(run.id)
    assert not principal and not reviewer
    assert finished.verification["passed"] is True
    assert finished.verification["exit_code"] == 0
    assert "5 passed" in finished.verification["output"]
    assert finished.review["role"] == "reviewer"
    assert finished.review["reviewer"] == "software + model"
    assert (workspace / "tracker.py").read_text() == before
    assert not (workspace / "test_boundaries.py").exists()
    review_request = next(payload for is_review, payload in requests if is_review)
    assert "The change is ready for independent verification." not in str(review_request)
    assert {spec["name"] for spec in review_request["tools"]}.isdisjoint({"edit_file", "run_shell"})
    if verdict["findings"]:
        assert finished.status == "failed"
        assert finished.review["passed"] is False
        assert finished.blockers == ["review blocked"]
    else:
        assert finished.status == "completed"
        assert finished.review["passed"] is True
        assert len(finished.verification["stage_digest"]) == 64
        stage = Stage(id=run.id, source=workspace, root=Path(finished.stage_path))
        stage.publish(expected_digest=finished.verification["stage_digest"])
        assert (workspace / "tracker.py").read_text() != before
        assert (workspace / "test_boundaries.py").read_text() == regression
