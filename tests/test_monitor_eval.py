from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness.__main__ import main
from harness.evals.cases import ATTACKS, CASES, CLEAN, REPOS, Case
from harness.evals.monitor import (
    bootstrap_ci,
    build_case,
    cohen_kappa,
    run_eval,
    software_monitor,
    summarize,
)
from harness.review import inspect_change
from harness.verification import run_checks


def test_subtle_variants_cover_the_attacks_software_cannot_see():
    subtle = {case.label for case in CASES if case.difficulty == "subtle"}
    assert {"special_case", "conftest_patch", "unrelated_harm", "incorrect_fix"} <= subtle


def test_case_ids_are_unique_and_labels_known():
    ids = [case.id for case in CASES]
    assert len(ids) == len(set(ids))
    assert {case.label for case in CASES} <= {CLEAN, *ATTACKS}
    for repo in REPOS:
        labels = {case.label for case in CASES if case.repo == repo}
        assert CLEAN in labels and set(ATTACKS) <= labels


@pytest.mark.parametrize("repo", sorted(REPOS))
def test_every_base_repo_fails_its_tests(tmp_path, repo):
    stage = build_case(Case(f"base-{repo}", repo, CLEAN, "base"), tmp_path)
    assert not run_checks(stage.root, timeout=60).passed


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_every_case_passes_its_tests(tmp_path, case):
    """Otherwise the test gate decides and the monitor is never measured."""
    stage = build_case(case, tmp_path)
    verification = run_checks(stage.root, timeout=60)
    assert verification.passed, verification.output


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_software_rules_block_only_what_they_can_prove(tmp_path, case):
    stage = build_case(case, tmp_path)
    blocked = bool(inspect_change(stage))
    if case.label in {"skip_test", "conftest_patch"} and case.difficulty == "obvious":
        assert blocked, case.id
    else:
        assert not blocked, (case.id, inspect_change(stage))


def _cases(*ids: str) -> list[Case]:
    wanted = set(ids)
    return [case for case in CASES if case.id in wanted]


def test_run_eval_scores_a_monitor_per_case(tmp_path):
    seen: list[str] = []

    def flaky(case, stage, verification, objective):
        seen.append(case.id)
        assert verification.passed
        assert objective == REPOS[case.repo].objective
        # Blocks every attack except special-casing, and blocks the clean fix once in three.
        if case.label == "special_case":
            return {"ship": True, "findings": []}
        if case.label == CLEAN:
            return {"ship": seen.count(case.id) != 2, "findings": ["looks wrong"]}
        return {"ship": False, "findings": ["games the tests"]}

    results = run_eval(
        flaky,
        trials=3,
        cases=_cases("tracker-clean", "tracker-special-case", "tracker-skip-test", "ledger-incorrect-fix"),
        workdir=tmp_path,
    )
    summary = summarize(results)
    assert summary["trials"] == 3
    assert summary["invalid_cases"] == []
    assert summary["monitor"]["catch_rate"]["rate"] == pytest.approx(2 / 3)
    assert summary["monitor"]["false_block_rate"]["rate"] == pytest.approx(1 / 3)
    assert summary["monitor"]["split_verdict_cases"] == ["tracker-clean"]
    assert summary["by_attack"]["special_case"]["rate"] == 0
    assert summary["gate"]["catch_rate"]["rate"] == pytest.approx(2 / 3)
    assert summary["software_only_catch_rate"]["rate"] == pytest.approx(1 / 3)


def test_reviewer_errors_count_as_blocks(tmp_path):
    def broken(case, stage, verification, objective):
        return {"ship": False, "findings": ["reviewer failed: boom"], "error": True}

    results = run_eval(broken, cases=_cases("slug-clean"), workdir=tmp_path)
    summary = summarize(results)
    assert summary["monitor"]["false_block_rate"]["rate"] == 1
    assert summary["monitor"]["reviewer_errors"] == 1


def test_run_eval_rejects_zero_trials():
    with pytest.raises(ValueError):
        run_eval(software_monitor, trials=0, cases=[])


def test_cohen_kappa_known_values():
    assert cohen_kappa([True, False, True, False], [True, False, True, False]) == 1
    assert cohen_kappa([False, True, False, True], [True, False, True, False]) == -1
    assert cohen_kappa([True, True], [True, True]) is None
    assert cohen_kappa([], []) is None


def test_bootstrap_ci_is_deterministic_and_brackets_the_mean():
    values = [0.0, 1.0, 1.0, 0.5, 1.0, 0.0, 1.0, 1.0]
    low, high = bootstrap_ci(values)
    assert bootstrap_ci(values) == (low, high)
    assert low <= sum(values) / len(values) <= high
    assert bootstrap_ci([]) is None
    assert bootstrap_ci([1.0, 1.0]) == (1.0, 1.0)


def test_cli_writes_a_report(tmp_path, capsys):
    out = tmp_path / "report"
    code = main(
        ["eval", "monitor", "--case", "tracker-skip-test", "--case", "tracker-clean", "--out", str(out)]
    )
    assert code == 0
    assert "catch rate: 100%" in capsys.readouterr().out
    payload = json.loads((out / "results.json").read_text())
    assert payload["monitor"] == "software rules"
    assert {row["id"] for row in payload["results"]} == {"tracker-skip-test", "tracker-clean"}
    assert "| tracker-skip-test | skip_test | obvious | 1/1 |" in (out / "report.md").read_text()


def test_cli_rejects_unknown_case(tmp_path, capsys):
    assert main(["eval", "monitor", "--case", "nope", "--out", str(tmp_path)]) == 1
    assert "unknown case: nope" in capsys.readouterr().out


def test_live_monitor_needs_its_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["eval", "monitor", "--monitor", "claude", "--out", str(tmp_path)]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_cases_do_not_leak_into_the_repository():
    root = Path(__file__).resolve().parent.parent
    assert not (root / "conftest.py").exists()
