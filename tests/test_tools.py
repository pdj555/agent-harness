from __future__ import annotations

from pathlib import Path

import pytest
from harness.authority import PathDenied, PermissionDenied
from harness.isolation import create_stage
from harness.tools import ToolError, execute, software_helper
from tests.helpers import copy_sample, git_init


def _ctx(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "run-tools")
    return stage


def _symlink_or_skip(link: Path, target: Path | str) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")


def test_list_search_read_edit_and_shell_under_authority(tmp_path: Path):
    stage = _ctx(tmp_path)
    listed = execute("list_files", {"pattern": "*.py"}, stage=stage, role="principal")
    assert "tracker.py" in listed
    found = execute("search", {"query": "classify_priority"}, stage=stage, role="principal")
    assert "tracker.py" in found
    text = execute("read_file", {"path": "tracker.py"}, stage=stage, role="principal")
    assert "classify_priority" in text
    execute(
        "edit_file",
        {"path": "tracker.py", "old": 'return "low"', "new": 'return "high"'},
        stage=stage,
        role="principal",
    )
    edited = (stage.root / "tracker.py").read_text(encoding="utf-8")
    assert 'return "high"' in edited
    shell = execute("run_shell", {"command": "python3 -m pytest -q"}, stage=stage, role="principal")
    assert "test" in shell.lower() or "fail" in shell.lower() or "pass" in shell.lower()
    status = execute("git_status", {}, stage=stage, role="principal")
    diff = execute("git_diff", {}, stage=stage, role="principal")
    assert "tracker.py" in status or "tracker.py" in diff


def test_tools_reject_path_outside_stage(tmp_path: Path):
    stage = _ctx(tmp_path)
    try:
        execute("read_file", {"path": "/etc/passwd"}, stage=stage, role="principal")
    except (PathDenied, ToolError) as exc:
        assert "deny" in str(exc).lower() or "path" in str(exc).lower() or "outside" in str(exc).lower()
    else:
        raise AssertionError("read of /etc/passwd must be denied")


def test_read_file_can_page_beyond_initial_output_limit(tmp_path: Path):
    stage = _ctx(tmp_path)
    lines = [f"line {number}: " + "x" * 80 + "\n" for number in range(1, 301)]
    (stage.root / "long.py").write_text("".join(lines), encoding="utf-8")
    assert len("".join(lines[:200])) > 8000
    assert execute(
        "read_file",
        {"path": "long.py", "start_line": 201, "end_line": 203},
        stage=stage,
        role="reviewer",
    ) == "".join(lines[200:203])
    assert execute(
        "read_file", {"path": "long.py", "end_line": 2}, stage=stage, role="reviewer"
    ) == "".join(lines[:2])
    assert execute(
        "read_file", {"path": "long.py", "start_line": 299}, stage=stage, role="reviewer"
    ) == "".join(lines[298:])


@pytest.mark.parametrize(
    "line_range",
    [
        {"start_line": 0},
        {"start_line": True},
        {"start_line": "1"},
        {"start_line": None},
        {"end_line": 0},
        {"end_line": False},
        {"end_line": None},
        {"start_line": 3, "end_line": 2},
    ],
)
def test_read_file_rejects_invalid_line_ranges(tmp_path: Path, line_range: dict):
    stage = _ctx(tmp_path)
    with pytest.raises(ToolError, match="positive integer line numbers"):
        execute("read_file", {"path": "tracker.py", **line_range}, stage=stage, role="reviewer")


@pytest.mark.parametrize("tool", ["read_file", "edit_file"])
def test_binary_file_is_a_recoverable_tool_error(tmp_path: Path, tool: str):
    stage = _ctx(tmp_path)
    data = b"\xff\xfe\x00binary"
    (stage.root / "binary.dat").write_bytes(data)
    arguments = {"path": "binary.dat", "old": "old", "new": "new"}
    with pytest.raises(ToolError, match="could not read file"):
        execute(tool, arguments, stage=stage, role="principal")
    assert (stage.root / "binary.dat").read_bytes() == data
    assert "classify_priority" in execute(
        "read_file", {"path": "tracker.py"}, stage=stage, role="principal"
    )


def test_file_read_os_error_is_recoverable(tmp_path: Path, monkeypatch):
    stage = _ctx(tmp_path)

    def denied(_path, **_kwargs):
        raise PermissionError("file is not readable")

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(ToolError, match="file is not readable"):
        execute("read_file", {"path": "tracker.py"}, stage=stage, role="reviewer")


def test_edit_creates_nested_module_and_regression_test_only_in_stage(tmp_path: Path):
    stage = _ctx(tmp_path)
    files = {
        "package/feature.py": "def value():\n    return 42\n",
        "tests/test_feature.py": (
            "from package.feature import value\n\ndef test_value():\n    assert value() == 42\n"
        ),
    }
    for rel, content in files.items():
        result = execute(
            "edit_file", {"path": rel, "old": "", "new": content}, stage=stage, role="principal"
        )
        assert result == f"created {rel}"
        assert execute("read_file", {"path": rel}, stage=stage, role="principal") == content
        assert not (stage.source / rel).exists()
    assert set(stage.changed_files()) == set(files)


def test_edit_cannot_create_missing_file_with_expected_old_contents(tmp_path: Path):
    stage = _ctx(tmp_path)
    with pytest.raises(ToolError, match="file not found"):
        execute(
            "edit_file",
            {"path": "missing/module.py", "old": "previous", "new": "replacement"},
            stage=stage,
            role="principal",
        )
    assert not (stage.root / "missing").exists()


def test_create_intent_cannot_overwrite_existing_file_or_directory(tmp_path: Path):
    stage = _ctx(tmp_path)
    original = (stage.root / "tracker.py").read_text(encoding="utf-8")
    with pytest.raises(ToolError, match="match exactly once"):
        execute(
            "edit_file", {"path": "tracker.py", "old": "", "new": "overwrite"}, stage=stage, role="principal"
        )
    with pytest.raises(ToolError, match="not a regular file"):
        execute("edit_file", {"path": ".", "old": "", "new": "overwrite"}, stage=stage, role="principal")
    assert (stage.root / "tracker.py").read_text(encoding="utf-8") == original
    assert stage.changed_files() == []


@pytest.mark.parametrize("rel", ["alias", "alias/module.py"])
def test_create_intent_rejects_dangling_stage_symlinks(tmp_path: Path, rel: str):
    stage = _ctx(tmp_path)
    _symlink_or_skip(stage.root / "alias", "missing-target")
    with pytest.raises(ToolError, match="dangling symlink"):
        execute("edit_file", {"path": rel, "old": "", "new": "content"}, stage=stage, role="principal")
    assert not (stage.root / "missing-target").exists()


@pytest.mark.parametrize("rel", ["../escaped.py", ".git/new-file", "/outside-stage-file.py"])
def test_create_intent_obeys_path_authority(tmp_path: Path, rel: str):
    stage = _ctx(tmp_path)
    with pytest.raises(PathDenied):
        execute("edit_file", {"path": rel, "old": "", "new": "content"}, stage=stage, role="principal")
    assert stage.changed_files() == []


@pytest.mark.parametrize("role,stopped", [("reviewer", False), ("principal", True)])
def test_create_intent_obeys_role_and_stop(tmp_path: Path, role: str, stopped: bool):
    stage = _ctx(tmp_path)
    with pytest.raises((PermissionDenied, ToolError)):
        execute(
            "edit_file",
            {"path": "new/module.py", "old": "", "new": "content"},
            stage=stage,
            role=role,
            stopped=stopped,
        )
    assert not (stage.root / "new").exists()


@pytest.mark.parametrize("argument,value", [("old", None), ("old", 1), ("new", []), ("new", {})])
def test_edit_requires_string_contents_before_creating(tmp_path: Path, argument: str, value):
    stage = _ctx(tmp_path)
    arguments = {"path": "new/module.py", "old": "", "new": "content", argument: value}
    with pytest.raises(ToolError, match="old and new strings"):
        execute("edit_file", arguments, stage=stage, role="principal")
    assert not (stage.root / "new").exists()


def test_tools_fail_closed_after_shell_creates_escaping_symlink(tmp_path: Path):
    stage = _ctx(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside target\n", encoding="utf-8")
    probe = tmp_path / "symlink-probe"
    _symlink_or_skip(probe, outside)
    probe.unlink()
    command = (
        "python3 -c \"from pathlib import Path; target = Path('tracker.py'); "
        f"target.unlink(); target.symlink_to({str(outside)!r})\""
    )

    with pytest.raises(ToolError, match="symlink escapes"):
        execute("run_shell", {"command": command}, stage=stage, role="principal")
    with pytest.raises(ToolError, match="symlink escapes"):
        execute("read_file", {"path": "tracker.py"}, stage=stage, role="principal")


def test_delegate_inspects_without_user_management(tmp_path: Path):
    stage = _ctx(tmp_path)
    result = execute(
        "delegate",
        {"objective": "Find the priority classifier."},
        stage=stage,
        role="principal",
        helper=lambda objective: software_helper(objective, stage),
    )
    assert "tracker.py" in result


def test_reviewer_cannot_edit(tmp_path: Path):
    stage = _ctx(tmp_path)
    try:
        execute(
            "edit_file",
            {"path": "tracker.py", "old": "low", "new": "high"},
            stage=stage,
            role="reviewer",
        )
    except (ToolError, PermissionDenied) as exc:
        assert "reviewer" in str(exc).lower() or "permission" in str(exc).lower()
    else:
        raise AssertionError("reviewer must not edit")


def test_shell_python_resolves_to_the_repository_interpreter(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    (source / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    git_init(source)
    fake = source / ".venv" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\necho \"project-venv $*\"\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    stage = create_stage(source, tmp_path / "stages", "run-shell-venv")
    output = execute("run_shell", {"command": "python -m pytest -q"}, stage=stage, role="principal")
    assert output.splitlines()[0] == "exit 0"
    assert "project-venv -m pytest -q" in output
    assert stage.changed_files() == []


def test_shell_timeout_is_reported_not_raised_through(tmp_path: Path):
    stage = _ctx(tmp_path)
    try:
        execute("run_shell", {"command": "sleep 5"}, stage=stage, role="principal", timeout=1)
    except ToolError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("a hung command must surface as a tool error")


def test_search_skips_binary_and_oversized_files(tmp_path: Path):
    from harness.tools import MAX_SEARCH_BYTES

    stage = _ctx(tmp_path)
    (stage.root / "blob.bin").write_bytes(b"needle\0binary")
    (stage.root / "huge.txt").write_text("needle\n" + "x" * MAX_SEARCH_BYTES, encoding="utf-8")
    (stage.root / "small.txt").write_text("needle here\n", encoding="utf-8")
    found = execute("search", {"query": "needle"}, stage=stage, role="principal")
    assert "small.txt:1" in found
    assert "blob.bin" not in found
    assert "huge.txt" not in found
