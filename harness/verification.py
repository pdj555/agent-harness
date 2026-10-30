from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from harness.isolation import command_env, stage_home

DEFAULT_TIMEOUT = 300
DEFAULT_COMMAND = "python -m pytest -q"


@dataclass
class Verification:
    passed: bool
    command: str
    exit_code: int
    output: str

    def as_dict(self) -> dict:
        return asdict(self)


def run_checks(
    stage_root: Path,
    *,
    python: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    command: str = "",
    env: dict[str, str] | None = None,
) -> Verification:
    """Run the project's checks in the stage. Only the process decides the verdict.

    ``command`` is the operator's ``workspace.check_command`` or, by default,
    pytest. It is split with ``shlex`` and never handed to a shell. ``python``
    is the repository's own interpreter when it has one; otherwise the harness
    interpreter, and ``python``/``python3`` in the command resolve to it. A
    timeout is a failed verification, never an exception, so a slow or hung
    suite cannot leave a run stuck in ``running``.
    ``env`` adds variables on top of the filtered stage environment.

    When the command runs pytest (see ``_runs_pytest``), exit 0 is not enough:
    pytest also writes a JUnit report to a directory outside the stage, and the
    checks pass only if that report exists, shows at least one test that ran, and
    shows no failure or error. That closes these ways stage code can end pytest with
    status 0 and no passing run: ``os._exit(0)`` in a conftest, a hook that resets
    ``session.exitstatus``, ``--collect-only`` in the project's config, deselecting
    or skipping every test. It does not close hooks that change results inside the
    pytest process: ``pytest.exit(returncode=0)`` when a test fails, a
    ``pytest_runtest_makereport`` wrapper that flips the outcome, xfail or deselect
    of only the failing tests, or rewriting the report. The review gate has to
    catch those. Other commands are judged by exit code alone.
    """
    interpreter = python or sys.executable
    command = command.strip() or DEFAULT_COMMAND
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return Verification(passed=False, command=command, exit_code=-1, output=f"bad check command: {exc}")
    if not argv:
        return Verification(passed=False, command=command, exit_code=-1, output="empty check command")
    if argv[0] in {"python", "python3"}:
        argv[0] = interpreter
    env = {**command_env(stage_home(stage_root), interpreter), **(env or {})}
    with tempfile.TemporaryDirectory(prefix="harness-checks-") as reports:
        report = Path(reports) / "junit.xml" if _runs_pytest(argv) else None
        if report is not None:
            # Before a bare "--", so pytest reads it as an option, not a path.
            at = argv.index("--") if "--" in argv else len(argv)
            argv = [*argv[:at], f"--junitxml={report}", *argv[at:]]
        return _run(argv, command, stage_root, env, timeout, report)


def _run(
    argv: list[str], command: str, stage_root: Path, env: dict[str, str], timeout: int, report: Path | None
) -> Verification:
    try:
        proc = subprocess.run(
            argv,
            cwd=stage_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = _text(exc.stdout) + _text(exc.stderr)
        return Verification(
            passed=False,
            command=command,
            exit_code=-1,
            output=(f"checks timed out after {timeout}s\n" + partial.strip())[-8000:],
        )
    except OSError as exc:
        return Verification(
            passed=False,
            command=command,
            exit_code=-1,
            output=f"checks could not start: {exc}",
        )
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    passed = proc.returncode == 0
    if passed and report is not None:
        problem = _report_problem(report)
        if problem:
            passed = False
            output = f"{output}\nchecks exited 0 but {problem}".strip()
    return Verification(
        passed=passed,
        command=command,
        exit_code=proc.returncode,
        output=output[-8000:],
    )


def _runs_pytest(argv: list[str]) -> bool:
    """True when the command's program is pytest.

    Recognized: a ``pytest`` or ``py.test`` executable (``pytest -q``), a Python
    interpreter running ``-m pytest`` after any interpreter flags
    (``python -I -m pytest``), ``-m coverage run -m pytest``, and any of those after
    ``uv run``, ``poetry run`` or ``pdm run`` with no options of their own. A
    command that hides pytest behind a shell or a build tool (``sh -c "pytest"``,
    ``make test``, ``tox``) is not recognized and is judged by exit code alone.
    """
    names = {"pytest", "py.test"}
    if not argv:
        return False
    program = Path(argv[0]).name
    if program in names:
        return True
    launcher = program in {"uv", "poetry", "pdm"} and len(argv) > 2 and argv[1] == "run"
    if launcher and not argv[2].startswith("-"):
        return _runs_pytest(argv[2:])
    if not program.startswith("python"):
        return False
    rest = argv[1:]
    while rest and rest[0] != "-m" and rest[0].startswith("-"):
        rest = rest[1:]
    if len(rest) < 2 or rest[0] != "-m":
        return False
    if rest[1] in names:
        return True
    if rest[1] == "coverage" and len(rest) > 2 and rest[2] == "run":
        tail = rest[3:]
        while tail and tail[0] != "-m" and tail[0].startswith("-"):
            tail = tail[1:]
        return len(tail) >= 2 and tail[0] == "-m" and tail[1] in names
    return False


def _report_problem(report: Path) -> str:
    """Why the JUnit report does not show a passing run, or "" when it does."""
    if not report.is_file():
        return (
            "pytest wrote no test report: the run ended before pytest finished, "
            "or the project disables the junitxml plugin (-p no:junitxml)"
        )
    try:
        root = ET.parse(report).getroot()
    except (ET.ParseError, OSError) as exc:
        return f"the test report is unreadable: {exc}"
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        return "the test report has no test suite"
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    try:
        for suite in suites:
            for key in totals:
                totals[key] += int(suite.get(key, "0"))
    except ValueError:
        return "the test report has non-numeric counts"
    if totals["failures"] or totals["errors"]:
        return f"the test report shows {totals['failures']} failed and {totals['errors']} errors"
    if totals["tests"] - totals["skipped"] < 1:
        return "the test report shows no test ran"
    return ""


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
