"""Rebuilding a sweep's summary from its log: the same scoring, no model called."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "sweep_from_log", Path(__file__).resolve().parent.parent / "scripts" / "sweep_from_log.py"
)
from_log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(from_log)

SPEC_ATTACKS = [case.id for case in from_log.sweep.SETS["spec-attacks"]]


def _github(line: str) -> str:
    """A line as `gh run view --log` prints it."""
    return f"sweep\tUNKNOWN STEP\t2026-11-09T07:12:00.0000000Z {line}"


def _log(verdicts: dict[str, list[str]], spend: list[tuple[str, float]], *, github: bool = True) -> str:
    lines = ["noise before the run", "{", '  "total_usd": 0.1', "}"]
    for (model, spent), (_, by_case) in zip(spend, verdicts.items(), strict=True):
        for case, answers in by_case.items():
            for trial, answer in enumerate(answers, start=1):
                lines.append(f"{case} [{trial}/{len(answers)}] {answer}")
        lines.append(f"{model} spec-attacks: spent ${spent:.2f} of $40.00")
    lines.append("report: /tmp/sweep/sweep.md")
    return "\n".join(_github(line) if github else line for line in lines) + "\n"


def _all(answer: str, trials: int = 2) -> dict[str, list[str]]:
    return {case: [answer] * trials for case in SPEC_ATTACKS}


@pytest.mark.parametrize("github", [True, False])
def test_rebuild_scores_every_verdict_and_reads_spend_as_differences(github):
    second = _all("block")
    second[SPEC_ATTACKS[0]] = ["error: reviewer failed: ", "block"]  # old harness: step limit, no message
    refusal = "error: reviewer failed: Anthropic response did not complete: refusal"
    second[SPEC_ATTACKS[1]] = [refusal, "block"]
    text = _log(
        {"a": _all("block"), "b": second},
        [("claude-haiku-5-5", 0.04), ("claude-opus-5-5", 0.30)],
        github=github,
    )
    record = from_log.rebuild(text, harness="abc1234", date="2026-11-09")
    haiku, opus = record["rows"]
    assert record["trials"] == 2 and record["max_usd"] == 40.0 and record["spent_usd"] == 0.30
    assert haiku["judged_trials"] == 16 and haiku["reviewer_errors"] == 0
    assert haiku["catch_rate"]["rate"] == 1
    assert opus["judged_trials"] == 14 and opus["reviewer_errors"] == 2
    assert opus["reviewer_errors_by_cause"] == {"refusal": 1, "step_limit": 1}
    assert haiku["usd"] == pytest.approx(0.04) and opus["usd"] == pytest.approx(0.26)
    assert not haiku["stopped_early"] and not opus["stopped_early"]
    table = from_log.sweep.render_sweep(record)
    assert "Spent $0.30 of a $40.00 cap." in table
    row = (
        "| claude-opus-5-5 | spec-attacks | 100% (63% to 100%) | n/a | 14 "
        "| 2 (1 refusal, 1 step limit) | $0.26 |"
    )
    assert row in table


def test_a_miss_is_scored_as_a_miss():
    verdicts = _all("block")
    verdicts[SPEC_ATTACKS[-1]] = ["ship", "ship"]
    record = from_log.rebuild(_log({"a": verdicts}, [("claude-haiku-5-5", 0.01)]), harness="x", date="d")
    assert record["rows"][0]["catch_rate"]["rate"] == pytest.approx(7 / 8)


def test_a_case_missing_reviews_marks_the_row_stopped():
    verdicts = _all("block")
    verdicts[SPEC_ATTACKS[2]] = ["block", "block"]
    del verdicts[SPEC_ATTACKS[3]]
    record = from_log.rebuild(_log({"a": verdicts}, [("claude-haiku-5-5", 0.01)]), harness="x", date="d")
    assert record["rows"][0]["stopped_early"]


def test_a_case_outside_the_set_is_refused():
    verdicts = _all("block")
    verdicts["tracker-clean"] = ["ship", "ship"]
    with pytest.raises(from_log.LogError, match="tracker-clean is not in the set"):
        from_log.rebuild(_log({"a": verdicts}, [("claude-haiku-5-5", 0.01)]), harness="x", date="d")


def test_a_log_with_no_sweep_is_refused(tmp_path, capsys):
    log = tmp_path / "run.log"
    log.write_text(_github("nothing ran") + "\n", encoding="utf-8")
    assert from_log.main([str(log), "--harness", "x", "--date", "d"]) == 1
    assert "no sweep spend lines" in capsys.readouterr().err


def test_cli_prints_the_table(tmp_path, capsys):
    log = tmp_path / "run.log"
    log.write_text(_log({"a": _all("block", 3)}, [("claude-haiku-5-5", 0.01)]), encoding="utf-8")
    assert from_log.main([str(log), "--harness", "abc1234", "--date", "2026-11-09"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Reviewer sweep, 2026-11-09\n")
    assert "Harness abc1234, 3 trials per case, effort medium, seed 0." in out
