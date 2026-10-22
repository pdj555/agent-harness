from __future__ import annotations

from pathlib import Path

import pytest
from harness.__main__ import main
from harness.config import Config
from harness.provider import Completion, DeterministicProvider, ScriptedProvider
from harness.store import Store


@pytest.fixture
def cli_config(config: Config, monkeypatch) -> Config:
    config.provider_instance = DeterministicProvider()
    monkeypatch.setattr("harness.__main__.load_config", lambda *args, **kwargs: config)
    return config


def _latest_run(config: Config):
    store = Store(config.data_dir / "harness.db")
    store.initialize()
    user = store.get_user_by_username("local-cli")
    assert user is not None
    return store, store.list_runs(user.id)[0]


def test_run_prepares_proof_and_only_explicit_apply_changes_source(cli_config, capsys):
    source = cli_config.workspace_roots[0]
    before = (source / "tracker.py").read_bytes()
    cli_config.auto_publish = True

    assert main(["run", str(source), "Fix the failing priority behavior."]) == 0

    output = capsys.readouterr().out
    store, run = _latest_run(cli_config)
    assert run.status == "completed"
    assert run.verification["passed"] is True
    assert run.verification["stage_digest"]
    assert run.review["passed"] is True
    assert run.review["role"] == "reviewer"
    assert run.published_at is None
    assert (source / "tracker.py").read_bytes() == before
    assert f"run: {run.id}" in output
    assert "status: completed" in output
    assert "checks:" in output
    assert "review:" in output
    assert "diff --git" in output
    assert f"harness apply {run.id}" in output

    assert main(["apply", run.id]) == 0

    applied = capsys.readouterr().out
    assert f"Applied run {run.id}" in applied
    assert store.get_run(run.id).published_at
    assert (source / "tracker.py").read_bytes() == (Path(run.stage_path) / "tracker.py").read_bytes()
    assert (source / "tracker.py").read_bytes() != before
    assert cli_config.auto_publish is True


def test_run_rejects_repository_outside_allowlist(cli_config, tmp_path, capsys):
    outside = tmp_path / "another-repo"
    outside.mkdir()

    assert main(["run", str(outside), "Fix a bug."]) == 1

    output = capsys.readouterr().out
    assert "not allowlisted" in output
    assert f"harness repo add {outside}" in output
    store = Store(cli_config.data_dir / "harness.db")
    assert store.get_user_by_username("local-cli") is None


def test_failed_run_returns_nonzero_and_cannot_apply(cli_config, capsys):
    cli_config.provider_instance = ScriptedProvider([Completion(text="The fix is done.")])
    cli_config.max_repairs = 0

    assert main(["run", str(cli_config.workspace_roots[0]), "Fix the priority bug."]) == 1

    output = capsys.readouterr().out
    _, run = _latest_run(cli_config)
    assert run.status == "failed"
    assert run.verification["passed"] is False
    assert "status: failed" in output
    assert "Apply with:" not in output
    assert main(["apply", run.id]) == 1
    assert "Only a verified completed run" in capsys.readouterr().out


def test_apply_rejects_stage_changed_after_checks(cli_config, capsys):
    source = cli_config.workspace_roots[0]
    before = (source / "tracker.py").read_bytes()
    assert main(["run", str(source), "Fix the priority bug."]) == 0
    store, run = _latest_run(cli_config)
    (Path(run.stage_path) / "tracker.py").write_text("changed after verification\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["apply", run.id]) == 1

    assert "Cannot apply:" in capsys.readouterr().out
    assert store.get_run(run.id).published_at is None
    assert (source / "tracker.py").read_bytes() == before


@pytest.mark.parametrize("missing", ["review", "fingerprint", "stage", "allowlist", "stop"])
def test_apply_requires_all_persisted_gates(cli_config, capsys, missing):
    source = cli_config.workspace_roots[0]
    before = (source / "tracker.py").read_bytes()
    assert main(["run", str(source), "Fix the priority bug."]) == 0
    store, run = _latest_run(cli_config)
    if missing == "review":
        store.update_run(run.id, review=None)
    elif missing == "fingerprint":
        verification = dict(run.verification)
        verification.pop("stage_digest")
        store.update_run(run.id, verification=verification)
    elif missing == "stage":
        store.update_run(run.id, stage_path=None)
    elif missing == "allowlist":
        cli_config.workspace_roots = []
    else:
        store.update_run(run.id, stop_requested=True)
    capsys.readouterr()

    assert main(["apply", run.id]) == 1

    assert capsys.readouterr().out.strip()
    assert store.get_run(run.id).published_at is None
    assert (source / "tracker.py").read_bytes() == before


def test_unknown_apply_is_a_clear_error(cli_config, capsys):
    assert main(["apply", "missing-run"]) == 1
    assert "Run not found" in capsys.readouterr().out


def test_run_reuses_cli_identity_and_preserves_custom_config_in_apply_command(cli_config, capsys):
    source = cli_config.workspace_roots[0]
    for _ in range(2):
        assert main(["--config", "project settings.toml", "run", str(source), "Fix the priority bug."]) == 0
    store, _ = _latest_run(cli_config)
    user = store.get_user_by_username("local-cli")
    assert len(store.list_runs(user.id)) == 2
    assert "harness --config 'project settings.toml' apply" in capsys.readouterr().out


def test_interrupt_records_terminal_stop_and_returns_interrupt_exit_code(cli_config, capsys):
    class InterruptedAgent:
        name = "scripted"
        model = None

        def turn(self, prompt, ctx):
            raise KeyboardInterrupt

        def close(self):
            pass

    cli_config.provider_instance = InterruptedAgent()
    assert main(["run", str(cli_config.workspace_roots[0]), "Fix the priority bug."]) == 130

    _, run = _latest_run(cli_config)
    assert run.status == "stopped"
    assert run.stop_requested is True
    assert run.published_at is None
    assert run.verification is None
    assert "status: stopped" in capsys.readouterr().out


def test_blank_objective_is_rejected_before_execution(cli_config, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["run", str(cli_config.workspace_roots[0]), "   "])
    assert exc.value.code == 2
    assert "objective must not be blank" in capsys.readouterr().err
