from __future__ import annotations

from pathlib import Path

from harness.isolation import create_stage
from harness.verification import run_checks
from tests.helpers import copy_sample, git_init


def test_failing_fixture_does_not_pass_verification(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "run-v")
    result = run_checks(stage.root)
    assert result.passed is False
    assert result.exit_code != 0
    assert result.output
    assert "test" in result.command.lower() or "pytest" in result.command.lower()


def test_model_text_is_not_an_argument_to_verification(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "run-v2")
    result = run_checks(stage.root)
    assert not hasattr(result, "model_claim")
    assert result.passed is False


def test_real_fix_makes_verification_pass(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "run-v3")
    path = stage.root / "tracker.py"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace('return "low"', 'return "high"', 1).replace(
            'return "high"', 'return "low"'
        ),
        encoding="utf-8",
    )
    # The naive double-replace above can swap both; write the correct function instead.
    path.write_text(
        text.replace(
            """    if impact >= 4 and urgency >= 4:
        return "low"
    if impact >= 3 or urgency >= 3:
        return "medium"
    return "high"
""",
            """    if impact >= 4 and urgency >= 4:
        return "high"
    if impact >= 3 or urgency >= 3:
        return "medium"
    return "low"
""",
        ),
        encoding="utf-8",
    )
    result = run_checks(stage.root)
    assert result.passed is True
    assert result.exit_code == 0


def test_timeout_is_a_failed_verification_not_a_crash(tmp_path: Path, monkeypatch):
    import subprocess

    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "run-timeout")

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"], output="1 test collected")

    monkeypatch.setattr("harness.verification.subprocess.run", hang)
    result = run_checks(stage.root, timeout=7)
    assert result.passed is False
    assert result.exit_code != 0
    assert "timed out after 7s" in result.output
    assert "1 test collected" in result.output


def test_repository_interpreter_runs_the_checks_when_present(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    (source / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    git_init(source)
    fake = source / ".venv" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    # A stand-in interpreter: it reports its arguments and HOME, and writes the passing
    # JUnit report pytest would write, because run_checks requires one for pytest.
    fake.write_text(
        "#!/bin/sh\n"
        'echo "project-venv $*"\n'
        'echo "home=$HOME"\n'
        'for arg; do last="$arg"; done\n'
        "printf '<testsuite tests=\"1\" failures=\"0\" errors=\"0\" skipped=\"0\"/>' "
        '> "${last#--junitxml=}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    stage = create_stage(source, tmp_path / "stages", "run-venv")
    assert stage.python() == str(fake)
    result = run_checks(stage.root, python=stage.python())
    assert result.passed is True
    assert "project-venv -m pytest -q" in result.output
    assert f"home={stage.home()}" in result.output


def test_harness_interpreter_is_the_fallback(tmp_path: Path):
    import sys

    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "run-no-venv")
    assert stage.python() == sys.executable


def test_operator_check_command_decides_the_verdict(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "run-command")
    failing = run_checks(stage.root, command="sh -c 'echo custom-check; exit 3'")
    assert failing.passed is False
    assert failing.exit_code == 3
    assert "custom-check" in failing.output
    assert failing.command == "sh -c 'echo custom-check; exit 3'"
    passing = run_checks(stage.root, command="sh -c 'exit 0'")
    assert passing.passed is True


def test_malformed_check_command_fails_closed(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "run-bad-command")
    result = run_checks(stage.root, command="sh -c 'unterminated")
    assert result.passed is False
    assert "bad check command" in result.output


def _fixed_sample(tmp_path: Path, name: str) -> Path:
    """A staged copy of the sample repository with its bug fixed, so its suite passes."""
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", name)
    path = stage.root / "tracker.py"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            'urgency >= 4:\n        return "low"', 'urgency >= 4:\n        return "high"'
        ).replace('return "medium"\n    return "high"', 'return "medium"\n    return "low"'),
        encoding="utf-8",
    )
    assert run_checks(stage.root).passed is True
    return stage.root


def _buggy_sample(tmp_path: Path, name: str) -> Path:
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", name)
    assert run_checks(stage.root).passed is False
    return stage.root


def test_a_hook_that_resets_the_exit_status_does_not_pass(tmp_path: Path):
    root = _buggy_sample(tmp_path, "run-exitstatus")
    (root / "conftest.py").write_text(
        "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n", encoding="utf-8"
    )
    result = run_checks(root)
    assert result.exit_code == 0
    assert result.passed is False
    assert "failed" in result.output.splitlines()[-1]


def test_exiting_zero_from_conftest_before_any_test_does_not_pass(tmp_path: Path):
    root = _buggy_sample(tmp_path, "run-os-exit")
    (root / "conftest.py").write_text("import os\n\nos._exit(0)\n", encoding="utf-8")
    result = run_checks(root)
    assert result.exit_code == 0
    assert result.passed is False
    assert "no test report" in result.output


def test_collect_only_in_project_config_does_not_pass(tmp_path: Path):
    root = _buggy_sample(tmp_path, "run-collect-only")
    (root / "pytest.ini").write_text("[pytest]\naddopts = --co\n", encoding="utf-8")
    result = run_checks(root)
    assert result.exit_code == 0
    assert result.passed is False
    assert "no test ran" in result.output


def test_deselecting_every_test_does_not_pass(tmp_path: Path):
    root = _buggy_sample(tmp_path, "run-deselect")
    (root / "conftest.py").write_text(
        "def pytest_collection_modifyitems(config, items):\n"
        "    config.hook.pytest_deselected(items=list(items))\n"
        "    items[:] = []\n\n\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    session.exitstatus = 0\n",
        encoding="utf-8",
    )
    result = run_checks(root)
    assert result.exit_code == 0
    assert result.passed is False
    assert "no test ran" in result.output


def test_a_suite_that_only_skips_does_not_pass(tmp_path: Path):
    root = _buggy_sample(tmp_path, "run-all-skipped")
    (root / "conftest.py").write_text(
        "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
        "    for item in items:\n        item.add_marker(pytest.mark.skip(reason='later'))\n",
        encoding="utf-8",
    )
    result = run_checks(root)
    assert result.exit_code == 0
    assert result.passed is False
    assert "no test ran" in result.output


def test_a_real_passing_suite_still_passes_through_pytest_entry_points(tmp_path: Path):
    root = _fixed_sample(tmp_path, "run-entry-points")
    assert run_checks(root, command="python -m pytest -q").passed is True
    assert run_checks(root, command="python -m pytest -q -p no:cacheprovider").passed is True


def test_commands_that_are_not_pytest_keep_exit_code_semantics(tmp_path: Path):
    root = _buggy_sample(tmp_path, "run-not-pytest")
    result = run_checks(root, command="sh -c 'echo pytest; exit 0'")
    assert result.passed is True
    assert "--junitxml" not in result.output


def test_pytest_is_recognized_behind_wrappers_and_flags(tmp_path: Path):
    root = _buggy_sample(tmp_path, "run-wrapped")
    (root / "conftest.py").write_text("import os\n\nos._exit(0)\n", encoding="utf-8")
    for command in ("python -I -m pytest -q", "python -m pytest -q --"):
        result = run_checks(root, command=command)
        assert result.passed is False, (command, result.output)
        assert "no test report" in result.output


def test_a_trailing_double_dash_still_gets_a_report(tmp_path: Path):
    root = _fixed_sample(tmp_path, "run-double-dash")
    assert run_checks(root, command="python -m pytest -q --").passed is True


def test_known_gap_pytest_exit_zero_after_a_failure_still_passes(tmp_path: Path):
    """Pinned so the docs stay true: a hook inside pytest can still end it with status 0.

    The report is written by the process the stage's conftest runs in, so this route is
    left to the review gate (docs/architecture.md). If this starts failing, the gap is
    closed: update the docs and turn this into a test that it fails verification.
    """
    root = _buggy_sample(tmp_path, "run-pytest-exit")
    (root / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.hookimpl(tryfirst=True)\ndef pytest_runtest_logreport(report):\n"
        "    if report.failed:\n        pytest.exit('done', returncode=0)\n\n\n"
        "def pytest_collection_modifyitems(items):\n    items.reverse()\n",
        encoding="utf-8",
    )
    result = run_checks(root)
    assert result.exit_code == 0
    assert result.passed is True


def test_which_commands_count_as_pytest():
    from harness.verification import _runs_pytest

    for command in (
        "pytest -q",
        "/venv/bin/py.test",
        "python -m pytest -q",
        "/stage/.venv/bin/python -I -m pytest",
        "python3 -m coverage run --branch -m pytest",
        "uv run pytest -q",
        "poetry run python -m pytest",
    ):
        assert _runs_pytest(command.split()), command
    for command in ("sh -c pytest", "make test", "tox", "python script.py", "python -m mypkg pytest"):
        assert not _runs_pytest(command.split()), command
