from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from harness.__main__ import main
from harness.config import Config
from harness.isolation import IsolationError, Stage, create_stage
from harness.provider import BUGGY_PRIORITY, FIXED_PRIORITY, Completion, ScriptedProvider, ToolCall
from harness.store import Store
from tests.helpers import copy_sample, git_init

COMMIT = (
    "git -c core.hooksPath=/dev/null -c user.name=Developer "
    "-c user.email=dev@example.com commit -am fix"
)


@pytest.mark.parametrize("amend", [False, True])
def test_cli_rejects_edits_hidden_by_a_commit(config: Config, monkeypatch, capsys, amend: bool):
    source = config.workspace_roots[0]
    before = (source / "tracker.py").read_bytes()
    config.max_repairs = 0
    config.provider_instance = ScriptedProvider([
        Completion(tool_calls=[
            ToolCall("edit_file", {"path": "tracker.py", "old": BUGGY_PRIORITY, "new": FIXED_PRIORITY}),
            ToolCall("run_shell", {"command": COMMIT + (" --amend" if amend else "")}),
            ToolCall("edit_file", {"path": "visible.txt", "old": "", "new": "Visible change.\n"}),
        ]),
        Completion(text="Fixed."),
    ])
    monkeypatch.setattr("harness.__main__.load_config", lambda *args, **kwargs: config)

    assert main(["run", str(source), "Fix the priority bug."]) == 1

    output = capsys.readouterr().out
    store = Store(config.data_dir / "harness.db")
    user = store.get_user_by_username("local-cli")
    run = store.list_runs(user.id)[0]
    assert run.status == "failed"
    assert run.verification is None
    assert run.review is None
    assert run.stage_path is None
    assert "Git history changed" in output
    assert "uncommitted" in output
    assert "Apply with:" not in output
    assert main(["apply", run.id]) == 1
    assert store.get_run(run.id).published_at is None
    assert (source / "tracker.py").read_bytes() == before
    assert not (source / "visible.txt").exists()


@pytest.mark.parametrize("git_source", [True, False])
def test_reconstructed_stage_rejects_a_committed_change(tmp_path: Path, git_source: bool):
    source = copy_sample(tmp_path / "source")
    if git_source:
        git_init(source)
    before = (source / "tracker.py").read_text()
    stage = create_stage(source, tmp_path / "stages", "history")
    (stage.root / "tracker.py").write_text(before.replace(BUGGY_PRIORITY, FIXED_PRIORITY))
    subprocess.run(
        [
            "git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Developer",
            "-c", "user.email=dev@example.com", "commit", "-am", "fix",
        ],
        cwd=stage.root,
        check=True,
        capture_output=True,
    )
    restored = Stage(stage.id, source, stage.root)

    with pytest.raises(IsolationError, match="Git history changed"):
        restored.content_digest()
    with pytest.raises(IsolationError, match="Git history changed"):
        restored.publish()

    assert (source / "tracker.py").read_text() == before


def test_stage_without_a_recorded_starting_commit_requires_a_new_run(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "legacy-history")
    baseline = stage.root.parent / f"{stage.root.name}.source-state.json"
    baseline.write_text(json.dumps({"tracker.py": ["file", False, "legacy-digest"]}))

    with pytest.raises(IsolationError, match="source baseline is missing or invalid; run it again"):
        stage.content_digest()
