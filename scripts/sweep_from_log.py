"""Rebuild a reviewer sweep's summary table from the run's log, with no model called.

The sweep prints one line per review (``ledger-clean [2/3] ship``) and one spend line per
model and set. That is every verdict the summary is computed from, so the table can be
rebuilt from the log with the same scoring code when the run's own summary was lost:

    gh run view 42577123643 -R pdj555/agent-harness --log > run.log
    uv run python scripts/sweep_from_log.py run.log --harness f476a86 --date 2026-11-09 \\
        --bare-error-is-step-limit

The case file is checked against the run's commit when this checkout has it; the scoring
code is always this checkout's.

What the log cannot give back:

- Tokens. Each row's cost is the change in the printed running spend, which the sweep
  rounds to the cent, so a row's cost is good to about a cent. The total is exact to the
  cent. Both are recorded tokens priced at the list rates in ``reviewer_sweep.PRICES``.
- Why a reviewer failed, when the failure had no message. Harness versions before
  2026-11-12 raised the step-budget error with no message, so the log shows a bare
  ``reviewer failed:``. ``--bare-error-is-step-limit`` counts those as ``step_limit``; it
  is an inference about that harness version, so it is off unless asked for.
- Which cases never reached the reviewer. An invalid case and a case an aborted run never
  reached both print nothing; either way the case is left unscored, not counted as a miss.

A log cut short (review lines after the last spend line), a trial logged twice, or an
effort or seed passed as a shell variable is refused rather than guessed at.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

from harness.evals.monitor import CaseResult, error_cause, summarize

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reviewer_sweep", ROOT / "scripts" / "reviewer_sweep.py")
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)

# GitHub prefixes each log line with the job, the step, and a timestamp (the first line also
# carries a byte order mark); a local run has none of it.
_PREFIX = re.compile("^[^\t]*\t[^\t]*\t﻿?\\d{4}-\\d\\d-\\d\\dT[\\d:.]+Z ")
_REVIEW = re.compile(r"^(?P<case>[a-z0-9-]+) \[(?P<trial>\d+)/(?P<trials>\d+)\] (?P<verdict>.*)$")
_SPENT = re.compile(r"^(?P<model>\S+) (?P<set>\S+): spent \$(?P<spent>[\d.]+) of \$(?P<cap>[\d.]+)$")
_COMMAND = re.compile(r"reviewer_sweep\.py --run\b(?P<args>.*)$")
_BARE = "reviewer failed:"
CASES_FILE = "harness/evals/cases.py"


class LogError(ValueError):
    """The log does not hold a complete sweep, or this checkout cannot score it."""


def _strip(raw: str) -> str:
    return _PREFIX.sub("", raw).lstrip("﻿").rstrip()


def parse(text: str) -> list[dict]:
    """Every model and set in the log, in order: its review lines and its running spend."""
    blocks: list[dict] = []
    reviews: list[tuple[str, int, int, str]] = []
    for raw in text.splitlines():
        line = _strip(raw)
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
    if reviews:
        raise LogError(f"{len(reviews)} review lines after the last spend line: the log is cut short")
    return blocks


def settings(text: str) -> dict:
    """Effort and seed from the logged ``--run`` command; reviewer_sweep.py's defaults where it set none."""
    defaults = sweep.build_parser().parse_args([])
    found = {"effort": defaults.effort, "seed": defaults.seed}
    for raw in text.splitlines():
        match = _COMMAND.search(_strip(raw))
        if not match:
            continue
        for name, pattern, cast in (("effort", r"[a-z]+", str), ("seed", r"\d+", int)):
            if f"--{name}" not in match["args"]:
                continue
            value = re.search(rf"--{name}[ =]\"?({pattern})\"?(?:\s|$)", match["args"])
            if value is None:
                raise LogError(f"--{name} in the logged command is not a literal value")
            found[name] = cast(value[1])
        break
    return found


def results_for(block: dict, *, bare_is_step_limit: bool = False) -> tuple[list[CaseResult], int]:
    """One CaseResult per case in the set, holding the verdicts the log printed for it."""
    cases = sweep.SETS.get(block["set"])
    if cases is None:
        raise LogError(f"unknown set {block['set']}")
    reviewed = {case_id for case_id, *_ in block["reviews"]}
    results = {
        case.id: CaseResult(
            id=case.id,
            repo=case.repo,
            label=case.label,
            note=case.note,
            difficulty=case.difficulty,
            # A case that printed no review is left unscored: invalid, or never reached.
            checks_passed=case.id in reviewed,
            software_blocked=False,  # not printed; the sweep table does not use it
        )
        for case in cases
    }
    trials = {total for _, _, total, _ in block["reviews"]}
    if len(trials) != 1:
        raise LogError(f"{block['model']} {block['set']}: trial counts {sorted(trials)}")
    seen: set[tuple[str, int]] = set()
    for case_id, trial, total, verdict in block["reviews"]:
        if case_id not in results:
            raise LogError(f"{block['model']} {block['set']}: {case_id} is not in the set")
        if (case_id, trial) in seen or not 1 <= trial <= total:
            where = f"{block['model']} {block['set']}: {case_id} trial {trial}"
            raise LogError(f"{where} is logged twice or out of range")
        seen.add((case_id, trial))
        result = results[case_id]
        if verdict in ("ship", "block"):
            result.verdicts.append(verdict == "block")
            result.findings.append([])
        elif verdict.startswith("error: "):
            finding = verdict.removeprefix("error: ")
            bare = finding.strip() == _BARE
            cause = "step_limit" if bare and bare_is_step_limit else error_cause([finding])
            result.findings.append([finding])
            result.errors += 1
            result.error_causes[cause] = result.error_causes.get(cause, 0) + 1
        else:
            raise LogError(f"{case_id}: unreadable verdict {verdict!r}")
    return list(results.values()), trials.pop()


def rebuild(text: str, *, harness: str, date: str, bare_is_step_limit: bool = False) -> dict:
    """The record ``reviewer_sweep.py --run`` would have written, from its log."""
    rows, previous, trials, cap = [], 0.0, None, None
    unreviewed: dict[str, list[str]] = {}
    for block in parse(text):
        results, block_trials = results_for(block, bare_is_step_limit=bare_is_step_limit)
        partial = [r.id for r in results if 0 < r.trials < block_trials]
        missing = [r.id for r in results if r.trials == 0]
        if missing:
            unreviewed[f"{block['model']} {block['set']}"] = missing
        summary = summarize(results)
        usd = block["spent"] - previous
        previous, trials, cap = block["spent"], block_trials, block["cap"]
        rows.append(sweep.sweep_row(block["model"], block["set"], summary, usd, stopped=bool(partial)))
    return {
        "date": date,
        "harness_commit": harness,
        "models": list(dict.fromkeys(row["model"] for row in rows)),
        "sets": list(dict.fromkeys(row["set"] for row in rows)),
        "trials": trials,
        **settings(text),
        "prices_per_million": sweep.PRICES,
        "max_usd": cap,
        "spent_usd": previous,
        "unreviewed_cases": unreviewed,
        "rows": rows,
    }


def cases_at(commit: str) -> str | None:
    """The case file at ``commit``, or None when this checkout does not have that commit."""
    try:
        return subprocess.run(
            ["git", "show", f"{commit}:{CASES_FILE}"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("log", type=Path, help="the run's log, from `gh run view <id> --log`")
    parser.add_argument("--harness", required=True, help="the commit the run checked out")
    parser.add_argument("--date", required=True, help="the run's date, for the table's title")
    parser.add_argument(
        "--bare-error-is-step-limit",
        action="store_true",
        help="count a message-less reviewer error as a step-limit error (harness before 2026-11-12)",
    )
    args = parser.parse_args(argv)
    then = cases_at(args.harness)
    if then is None:
        note = f"commit {args.harness} not found; scoring with this checkout's cases"
        print(f"sweep_from_log: {note}", file=sys.stderr)
    elif then != (ROOT / CASES_FILE).read_text(encoding="utf-8"):
        note = f"{CASES_FILE} changed since {args.harness}; check that commit out to score its log"
        print(f"sweep_from_log: {note}", file=sys.stderr)
        return 1
    try:
        record = rebuild(
            args.log.read_text(encoding="utf-8"),
            harness=args.harness,
            date=args.date,
            bare_is_step_limit=args.bare_error_is_step_limit,
        )
    except LogError as exc:
        print(f"sweep_from_log: {exc}", file=sys.stderr)
        return 1
    for where, cases in record["unreviewed_cases"].items():
        print(f"sweep_from_log: {where}: no reviews logged for {', '.join(cases)}", file=sys.stderr)
    sys.stdout.write(sweep.render_sweep(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
