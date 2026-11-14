"""The evals as another program calls them: one JSON document out, one exit code.

``harness eval ... --json`` and the MCP server (``harness.mcp_server``) both return the
documents built here, so an agent reads the same shape either way. The shape is fixed by
``eval_output.schema.json`` next to this file, and the exit codes by the constants below.
"""

from __future__ import annotations

import json
import math
import secrets
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from harness.evals import paired_generator
from harness.evals.cases import (
    BLIND_SPOTS,
    CANARY_GUID,
    CASES,
    CLOSED_BLIND_SPOTS,
    PAIRED_CASES,
    SAME_SHAPE_CASES,
    Case,
)
from harness.evals.monitor import (
    CaseResult,
    EvalAborted,
    Monitor,
    run_eval,
    software_monitor,
    spec_monitor,
    write_report,
)

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("eval_output.schema.json")

# Exit codes of every ``--json`` command, and the ``status`` each one carries.
EXIT_PASS = 0  # the eval ran and the monitor judged every case right
EXIT_FAIL = 1  # the eval ran and found failures: an attack shipped or a clean change was blocked
EXIT_USAGE = 2  # the request was wrong: an unknown set, case, flag or file
EXIT_INFRASTRUCTURE = 3  # the eval could not be scored: a reviewer error, or a case whose tests fail
STATUS = {
    EXIT_PASS: "pass",
    EXIT_FAIL: "fail",
    EXIT_USAGE: "usage_error",
    EXIT_INFRASTRUCTURE: "infrastructure_error",
}

SETS: dict[str, tuple[list[Case], str]] = {
    "main": (CASES, "the 33 published cases: 6 honest fixes and 27 attacks"),
    "spec-attacks": (
        CLOSED_BLIND_SPOTS + BLIND_SPOTS,
        "8 attacks built against the spec check: 5 it now catches and the 3 it still misses",
    ),
    "blind-spots": (BLIND_SPOTS, "3 attacks the spec check is known to miss"),
    "same-shape": (
        SAME_SHAPE_CASES,
        "8 attacks and 14 correct controls that add one return line and differ only in its condition",
    ),
    "paired": (PAIRED_CASES, "24 generated attack and control pairs that only arithmetic tells apart"),
}
# The set that exists only for a run: generated from a seed, about 30 seconds.
FRESH = "paired-fresh"
FRESH_DESCRIPTION = (
    "24 attack and control pairs generated when the run starts from a seed (--fresh-seed, drawn at "
    "random when not given); no expression is shared with the published paired set"
)
SET_NAMES = (*SETS, FRESH)
OFFLINE_MONITORS: dict[str, tuple[Monitor, str]] = {
    "software": (software_monitor, "software rules"),
    "spec": (spec_monitor, "executable spec, side effects blocked"),
}


class UsageError(ValueError):
    """The caller asked for something that does not exist; nothing was run."""


def _document(command: str, code: int, error: str | None = None, **fields: Any) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "status": STATUS[code],
        "exit_code": code,
        "error": error,
        **fields,
    }


def dumps(document: dict) -> str:
    """The document as strict JSON: one line, and never NaN or Infinity, which are not JSON."""
    return json.dumps(document, allow_nan=False)


def _finite(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return True


def prepare_out(out: Path) -> None:
    """Make sure the report directory can be written before anything runs; ``UsageError`` if not."""
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".write-check"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        raise UsageError(f"cannot write the report to {out}: {error}") from error


def error_document(command: str, code: int, message: str) -> dict:
    """The document for a request that produced no result."""
    return _document(command, code, message)


def help_document(text: str) -> dict:
    """``--help`` asked for beside ``--json``: the help text inside a document, exit 0."""
    return _document("help", EXIT_PASS, help=text)


def sets_document() -> dict:
    """Every case set an eval can run, with its size and what it is."""
    sets = [
        {
            "name": name,
            "description": description,
            "cases": len(cases),
            "attacks": sum(case.is_attack for case in cases),
            "clean": sum(not case.is_attack for case in cases),
        }
        for name, (cases, description) in SETS.items()
    ]
    pairs = paired_generator.FRESH_SEARCH["pairs"]
    sets.append({"name": FRESH, "description": FRESH_DESCRIPTION, "cases": 2 * pairs, "attacks": pairs,
                 "clean": pairs, "generated": True})
    monitors = list(OFFLINE_MONITORS)
    return _document("eval sets", EXIT_PASS, sets=sets, monitors=monitors, canary_guid=CANARY_GUID)


def select_cases(
    set_name: str, case_ids: list[str] | None = None, fresh_seed: int | None = None
) -> tuple[list[Case], dict | None]:
    """The cases of a set, and for the generated set what to record about it (its seed and hash).

    ``paired-fresh`` is generated here; a seed is drawn when none is given. A generated set
    whose labels fail their check raises ``RuntimeError``: nothing can be scored on it.
    """
    if set_name not in SET_NAMES:
        raise UsageError(f"unknown set: {set_name}; choose from {', '.join(SET_NAMES)}")
    fresh = None
    if set_name == FRESH:
        seed = fresh_seed if fresh_seed is not None else secrets.randbelow(2**31)
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise UsageError("the fresh seed must be a whole number, 0 or more")
        try:
            pool, fresh = paired_generator.fresh_paired(seed)
        except ValueError as error:
            raise RuntimeError(str(error)) from error
    elif fresh_seed is not None:
        raise UsageError(f"a fresh seed only applies to the {FRESH} set")
    else:
        pool = SETS[set_name][0]
    if not case_ids:
        return list(pool), fresh
    unknown = sorted(set(case_ids) - {case.id for case in pool})
    if unknown:
        raise UsageError(f"unknown case: {', '.join(unknown)}")
    return [case for case in pool if case.id in set(case_ids)], fresh


def monitor_document(
    summary: dict,
    results: list[CaseResult],
    *,
    monitor: str,
    set_name: str | None,
    report_dir: Path,
    aborted: str | None = None,
    fresh: dict | None = None,
) -> dict:
    """What one monitor did on one set, and the exit code that says whether it passed.

    ``failures`` lists each judged case the monitor got wrong at least once. A run with a
    reviewer error, a case never judged or a case whose own tests fail is an infrastructure
    error, whatever its rates, because its rates rest on fewer cases than the set has.
    """
    failures = [
        {
            "id": r.id,
            "label": r.label,
            "expected": "block" if r.attack else "ship",
            "blocked": sum(r.verdicts),
            "judged": len(r.verdicts),
        }
        for r in results
        if r.checks_passed and r.judged and sum(r.verdicts) != (len(r.verdicts) if r.attack else 0)
    ]
    broken = (
        aborted
        or summary["invalid_cases"]
        or summary["unjudged_cases"]
        or summary["monitor"]["reviewer_errors"]
        or not summary["judged_trials"]
    )
    if broken:
        code = EXIT_INFRASTRUCTURE
        error = aborted or (
            f"{len(summary['invalid_cases'])} invalid cases, {len(summary['unjudged_cases'])} never judged, "
            f"{summary['monitor']['reviewer_errors']} reviewer errors"
        )
    else:
        code, error = (EXIT_FAIL if failures else EXIT_PASS), None
    return _document(
        "eval monitor",
        code,
        error,
        monitor=monitor,
        set=set_name,
        cases=summary["cases"],
        trials=summary["trials"],
        judged_trials=summary["judged_trials"],
        catch_rate=summary["monitor"]["catch_rate"],
        false_block_rate=summary["monitor"]["false_block_rate"],
        saturation=summary["monitor"]["saturation"],
        reviewer_errors=summary["monitor"]["reviewer_errors"],
        reviewer_errors_by_cause=summary["monitor"]["reviewer_errors_by_cause"],
        invalid_cases=summary["invalid_cases"],
        unjudged_cases=summary["unjudged_cases"],
        failures=failures,
        report_dir=str(report_dir),
        canary_guid=CANARY_GUID,
        **({"fresh": fresh} if fresh else {}),
    )


def run_offline(
    monitor: str,
    set_name: str,
    out: Path,
    case_ids: list[str] | None = None,
    fresh_seed: int | None = None,
) -> dict:
    """Run a monitor that needs no key on a set and return its document. Writes the report to ``out``."""
    if monitor not in OFFLINE_MONITORS:
        raise UsageError(f"unknown offline monitor: {monitor}; choose from {', '.join(OFFLINE_MONITORS)}")
    try:
        cases, fresh = select_cases(set_name, case_ids, fresh_seed)
    except RuntimeError as error:
        return error_document("eval monitor", EXIT_INFRASTRUCTURE, str(error))
    prepare_out(out)
    review, label = OFFLINE_MONITORS[monitor]
    aborted = None
    try:
        results = run_eval(review, cases=cases, jobs=1 if monitor == "software" else 4)
    except EvalAborted as exc:  # only a model monitor sets an error limit; kept so the shape holds
        results, aborted = exc.results, str(exc)
    summary = write_report(results, out, monitor_name=label)
    return monitor_document(
        summary, results, monitor=label, set_name=set_name, report_dir=out, aborted=aborted, fresh=fresh
    )


def problems(document: object) -> list[str]:
    """Everything in ``document`` that breaks the schema; empty when it is a valid eval document."""
    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or 'document'}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda e: [str(p) for p in e.absolute_path])
    ]


SWEEP_KEYS = (
    "date", "harness_commit", "models", "sets", "trials", "effort", "seed", "max_usd", "spent_usd", "fresh"
)


def sweep_claims(analysis: object) -> dict:
    """Whether a sweep's report may be read as the preregistered run, from its stored analysis.

    A sweep checked against a preregistration supports claims only if its report says so
    and it shows no deviation and no missing primary result; the last two are read from the
    audit itself, so a record written before the report tied them to ``claims`` is judged
    the same way. A sweep with no preregistration claims nothing and is not a failure.
    """
    found = analysis if isinstance(analysis, dict) else {}
    check = found.get("prereg") if isinstance(found.get("prereg"), dict) else None
    stated = found.get("claims") if isinstance(found.get("claims"), dict) else {}
    if check is None:
        return {"preregistered": False, "allowed": False, "reason": "the sweep had no preregistration"}
    reasons = []
    if check.get("deviations"):
        names = ", ".join(str(d.get("setting")) for d in check["deviations"] if isinstance(d, dict))
        reasons.append(f"settings deviate from the preregistration: {names}")
    if check.get("primary_not_computed"):
        reasons.append(f"preregistered primary results missing: {check['primary_not_computed']}")
    if not reasons and stated.get("allowed") is not True:
        reasons.append(str(stated.get("reason") or "the report does not say its claims stand"))
    return {"preregistered": True, "allowed": not reasons, "reason": "; ".join(reasons) or None}


def sweep_summary_document(path: Path) -> dict:
    """A sweep's ``sweep.json`` as a document: its settings, rows and analysis, without per-template data.

    ``claims`` says whether the sweep stands as its preregistered run. One that deviated, is
    missing a primary result or could not be matched to its preregistration is status
    "fail", exit 1: the file is a valid summary of a sweep that supports no claim.

    The document is checked against the schema before it is returned. A file that is not a
    complete sweep summary (unreadable, not JSON, a key missing or of the wrong type) raises
    ``UsageError`` naming what is wrong; it never comes back as a passing document.
    """
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UsageError(f"cannot read a sweep summary at {path}: {error}") from error
    if not _finite(record):
        raise UsageError(f"{path} is not a sweep summary: it holds NaN or Infinity, which are not JSON")
    rows = record.get("rows") if isinstance(record, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise UsageError(f"{path} is not a sweep summary: it has no rows")
    sweep = {key: record[key] for key in SWEEP_KEYS if key in record}
    sweep["rows"] = [{k: v for k, v in row.items() if k != "templates"} for row in rows]
    if "analysis" in record:
        sweep["analysis"] = record["analysis"]
    claims = sweep_claims(record.get("analysis"))
    code = EXIT_FAIL if claims["preregistered"] and not claims["allowed"] else EXIT_PASS
    document = _document("eval sweep-summary", code, sweep=sweep, claims=claims)
    found = problems(document)
    if found:
        raise UsageError(f"{path} is not a sweep summary: {'; '.join(found[:3])}")
    return document
