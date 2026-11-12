"""Rebuild a reviewer sweep's summary table from the run's log, with no model called.

The sweep prints one line per review (``ledger-clean [2/3] ship``) and one spend line per
model and set. That is every verdict the summary is computed from, so the table can be
rebuilt from the log with the same scoring code when the run's own summary was lost:

    gh run view 42577123643 -R pdj555/agent-harness --log > run.log
    uv run python scripts/sweep_from_log.py run.log --harness f476a86 --date 2026-11-09

What the log cannot give back is tokens. Each row's cost is the change in the printed
running spend, which the sweep rounds to the cent, so a row's cost is good to about a cent.
The total is exact to the cent.

One reading is inferred, not printed. Harness versions before this script raised the
step-budget error with no message, so the log shows a bare ``reviewer failed:``. No other
reviewer failure in those versions has an empty message (provider errors carry the API's
reason, unreadable answers say so), so a bare one is counted as ``step_limit``.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

from harness.evals.monitor import CaseResult, error_cause, summarize

_spec = importlib.util.spec_from_file_location(
    "reviewer_sweep", Path(__file__).resolve().parent / "reviewer_sweep.py"
)
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)

# GitHub prefixes each log line with the job, the step, and a timestamp; a local run has none.
_PREFIX = re.compile(r"^[^\t]*\t[^\t]*\t﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ")
_REVIEW = re.compile(r"^(?P<case>[a-z0-9-]+) \[(?P<trial>\d+)/(?P<trials>\d+)\] (?P<verdict>.*)$")
_SPENT = re.compile(r"^(?P<model>\S+) (?P<set>\S+): spent \$(?P<spent>[\d.]+) of \$(?P<cap>[\d.]+)$")
_LEGACY_STEP_LIMIT = "reviewer failed:"


class LogError(ValueError):
    """The log does not hold a complete sweep."""


def parse(text: str) -> list[dict]:
    """Every model and set in the log, in order: its review lines and its running spend."""
    blocks: list[dict] = []
    reviews: list[tuple[str, int, int, str]] = []
    for raw in text.splitlines():
        line = _PREFIX.sub("", raw).rstrip()
        if match := _SPENT.match(line):
            blocks.append({
                "model": match["model"],
                "set": match["set"],
                "spent": float(match["spent"]),
                "cap": float(match["cap"]),
                "reviews": reviews,
            })
            reviews = []
        elif match := _REVIEW.match(line):
            reviews.append((match["case"], int(match["trial"]), int(match["trials"]), match["verdict"]))
    if not blocks:
        raise LogError("no sweep spend lines in the log")
    return blocks


def results_for(block: dict) -> tuple[list[CaseResult], int]:
    """One CaseResult per case in the set, holding the verdicts the log printed for it."""
    cases = sweep.SETS.get(block["set"])
    if cases is None:
        raise LogError(f"unknown set {block['set']}")
    by_id = {case.id: case for case in cases}
    results = {
        case.id: CaseResult(
            id=case.id,
            repo=case.repo,
            label=case.label,
            note=case.note,
            difficulty=case.difficulty,
            checks_passed=True,  # a case whose tests fail is never sent to the reviewer
            software_blocked=False,  # not printed; the sweep table does not use it
        )
        for case in cases
    }
    trials = {total for _, _, total, _ in block["reviews"]}
    if len(trials) != 1:
        raise LogError(f"{block['model']} {block['set']}: mixed trial counts {sorted(trials)}")
    for case_id, _trial, _total, verdict in block["reviews"]:
        if case_id not in by_id:
            raise LogError(f"{block['model']} {block['set']}: {case_id} is not in the set")
        result = results[case_id]
        if verdict in ("ship", "block"):
            result.verdicts.append(verdict == "block")
            result.findings.append([])
        elif verdict.startswith("error: "):
            finding = verdict.removeprefix("error: ")
            cause = "step_limit" if finding.strip() == _LEGACY_STEP_LIMIT else error_cause([finding])
            result.findings.append([finding])
            result.errors += 1
            result.error_causes[cause] = result.error_causes.get(cause, 0) + 1
        else:
            raise LogError(f"{case_id}: unreadable verdict {verdict!r}")
    return list(results.values()), trials.pop()


def rebuild(text: str, *, harness: str, date: str, effort: str = "medium", seed: int = 0) -> dict:
    """The record ``reviewer_sweep.py --run`` would have written, from its log."""
    rows, previous, trials, cap = [], 0.0, None, None
    for block in parse(text):
        results, block_trials = results_for(block)
        incomplete = [r.id for r in results if r.trials != block_trials]
        summary = summarize(results)
        usd = block["spent"] - previous
        previous, trials, cap = block["spent"], block_trials, block["cap"]
        rows.append(sweep.sweep_row(block["model"], block["set"], summary, usd, stopped=bool(incomplete)))
    return {
        "date": date,
        "harness_commit": harness,
        "models": list(dict.fromkeys(row["model"] for row in rows)),
        "sets": list(dict.fromkeys(row["set"] for row in rows)),
        "trials": trials,
        "effort": effort,
        "seed": seed,
        "prices_per_million": sweep.PRICES,
        "max_usd": cap,
        "spent_usd": previous,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("log", type=Path, help="the run's log, from `gh run view <id> --log`")
    parser.add_argument("--harness", required=True, help="the commit the run checked out")
    parser.add_argument("--date", required=True, help="the run's date, for the table's title")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        record = rebuild(
            args.log.read_text(encoding="utf-8"),
            harness=args.harness,
            date=args.date,
            effort=args.effort,
            seed=args.seed,
        )
    except LogError as exc:
        print(f"sweep_from_log: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(sweep.render_sweep(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
