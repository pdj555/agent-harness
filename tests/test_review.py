from __future__ import annotations

from pathlib import Path

import pytest
from harness.isolation import create_stage
from harness.review import run_review
from harness.verification import Verification, run_checks
from tests.helpers import copy_sample, git_init


def test_review_rejects_green_checks_when_nothing_changed(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "review-empty")
    green = Verification(
        passed=True,
        command="python -m pytest -q",
        exit_code=0,
        output="4 passed",
    )
    review = run_review(stage, green, None)
    assert green.passed is True
    assert review["role"] == "reviewer"
    assert review["passed"] is False
    assert review["passed"] is not green.passed
    assert review["findings"]


def test_review_rejects_when_only_tests_change(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "review-tests")
    (stage.root / "test_tracker.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    checks = run_checks(stage.root)
    assert checks.passed is True
    review = run_review(stage, checks, None)
    assert review["passed"] is False
    assert any("test" in item.lower() for item in review["findings"])


def test_review_accepts_implementation_change_when_tests_remain(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "review-ok")
    path = stage.root / "tracker.py"
    text = path.read_text(encoding="utf-8")
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
    checks = run_checks(stage.root)
    review = run_review(stage, checks, None)
    assert checks.passed is True
    assert review["passed"] is True
    assert review["role"] == "reviewer"
    assert "tracker.py" in review["files_reviewed"]


def test_review_recognises_non_python_test_layouts(tmp_path: Path):
    from harness.review import _test_files, inspect_change

    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src" / "app.js").write_text("module.exports = 1;\n", encoding="utf-8")
    (source / "src" / "app.test.js").write_text("test('x', () => {});\n", encoding="utf-8")
    (source / "node_modules" / "dep").mkdir(parents=True)
    (source / "node_modules" / "dep" / "index.test.js").write_text("ignored\n", encoding="utf-8")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "review-js")
    found = {str(path.relative_to(stage.root)) for path in _test_files(stage.root)}
    assert found == {"src/app.test.js"}
    (stage.root / "src" / "app.js").write_text("module.exports = 2;\n", encoding="utf-8")
    assert inspect_change(stage) == []
    (stage.root / "src" / "app.test.js").unlink()
    assert any("no test files" in finding for finding in inspect_change(stage))


def _fixed_stage(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "review-model")
    path = stage.root / "tracker.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('        return "low"\n', '        return "HIGH"\n')
    text = text.replace('    return "high"\n', '    return "low"\n')
    path.write_text(text.replace('"HIGH"', '"high"'), encoding="utf-8")
    return stage


def test_model_reviewer_can_block_a_change_software_accepts(tmp_path: Path):
    stage = _fixed_stage(tmp_path)
    checks = run_checks(stage.root)
    seen = []

    def reviewer(diff: str) -> dict:
        seen.append(diff)
        return {"ship": False, "findings": ["weakens the priority contract"]}

    review = run_review(stage, checks, reviewer)
    assert seen and "tracker.py" in seen[0]
    assert review["passed"] is False
    assert review["findings"] == ["weakens the priority contract"]
    assert review["reviewer"] == "software + model"


def test_model_reviewer_approval_is_recorded(tmp_path: Path):
    stage = _fixed_stage(tmp_path)
    review = run_review(stage, run_checks(stage.root), lambda _diff: {"ship": True, "findings": []})
    assert review["passed"] is True
    assert "Independent model approved" in review["summary"]


@pytest.mark.parametrize(
    "verdict",
    [
        None,
        {"ship": "yes", "findings": []},
        {"ship": 1, "findings": []},
        {"ship": True},
        {"ship": True, "findings": "unsafe"},
        {"ship": True, "findings": [1]},
        {"ship": True, "findings": [" "]},
        {"ship": True, "findings": [], "error": True},
    ],
)
def test_malformed_model_approval_blocks(tmp_path: Path, verdict):
    stage = _fixed_stage(tmp_path)
    review = run_review(stage, run_checks(stage.root), lambda _diff: verdict)
    assert review["passed"] is False
    assert review["findings"] == ["reviewer returned no readable verdict"]


def test_model_findings_block_even_when_reviewer_says_ship(tmp_path: Path):
    stage = _fixed_stage(tmp_path)
    review = run_review(
        stage,
        run_checks(stage.root),
        lambda _diff: {"ship": True, "findings": ["leaks credentials"]},
    )
    assert review["passed"] is False
    assert review["findings"] == ["leaks credentials"]


def test_reviewer_that_crashes_blocks_instead_of_approving(tmp_path: Path):
    stage = _fixed_stage(tmp_path)

    def broken(_diff: str) -> dict:
        raise RuntimeError("rate limited")

    review = run_review(stage, run_checks(stage.root), broken)
    assert review["passed"] is False
    assert "rate limited" in review["findings"][0]


def test_model_reviewer_is_not_asked_when_software_already_blocks(tmp_path: Path):
    source = copy_sample(tmp_path / "source")
    git_init(source)
    stage = create_stage(source, tmp_path / "stages", "review-skip")
    calls = []
    review = run_review(stage, run_checks(stage.root), lambda diff: calls.append(diff) or {"ship": True})
    assert review["passed"] is False
    assert calls == []


def test_reviewer_sees_new_files_and_is_told_when_the_diff_is_cut(tmp_path: Path):
    from harness.review import MAX_REVIEW_DIFF

    stage = _fixed_stage(tmp_path)
    (stage.root / "new_module.py").write_text("def added():\n    return 42\n", encoding="utf-8")
    seen = []
    approve = lambda diff: seen.append(diff) or {"ship": True, "findings": []}  # noqa: E731
    run_review(stage, run_checks(stage.root), approve)
    assert "new file new_module.py" in seen[0] and "return 42" in seen[0]
    assert "tracker.py" in seen[0]

    (stage.root / "huge.txt").write_text("x" * (MAX_REVIEW_DIFF * 2), encoding="utf-8")
    seen.clear()
    run_review(stage, run_checks(stage.root), approve)
    assert "more characters" in seen[0] or "DIFF TRUNCATED" in seen[0]


def test_stop_during_model_review_is_not_turned_into_a_block(tmp_path: Path):
    import pytest
    from harness.agents import RunStopped

    stage = _fixed_stage(tmp_path)

    def stopped(_diff: str) -> dict:
        raise RunStopped()

    with pytest.raises(RunStopped):
        run_review(stage, run_checks(stage.root), stopped)
