"""Score both adaptive rounds: the spec check, the software rules, and the ambient-read rule.

    uv run python scripts/adaptive_rounds.py

No key and no model. Each round is 12 attacks one model wrote in one pass
(harness/evals/adaptive_cases.py). The last column is the syntactic rule in
harness/evals/ambient.py, written between the rounds.
"""

from __future__ import annotations

from harness.evals.adaptive_cases import ADAPTIVE_CASES, ADAPTIVE_ROUND_2
from harness.evals.ambient import ambient_reads
from harness.evals.cases import CASES as MAIN_CASES
from harness.evals.cases import PAIRED_CASES, REPOS, SAME_SHAPE_CASES, Case
from harness.evals.monitor import run_eval, software_monitor, spec_monitor

ROUNDS = {"round 1": ADAPTIVE_CASES, "round 2": ADAPTIVE_ROUND_2}


def module_source(case: Case) -> str:
    """The module a case leaves in place: its own edit, or the repository's when it edits only tests."""
    name = f"{case.repo}.py"
    return case.files.get(name) or REPOS[case.repo].files[name]


def rule_blocks(case: Case) -> bool:
    return bool(ambient_reads(module_source(case)))


def score(cases: list[Case]) -> dict[str, set[str]]:
    """The ids each check blocks."""
    blocked = {
        name: {r.id for r in run_eval(monitor, cases=cases) if r.verdicts and r.verdicts[0]}
        for name, monitor in (("spec", spec_monitor), ("software", software_monitor))
    }
    blocked["rule"] = {case.id for case in cases if rule_blocks(case)}
    return blocked


def clean_flagged() -> list[str]:
    """Correct changes in the published sets that the rule would block."""
    clean = [case for case in MAIN_CASES + SAME_SHAPE_CASES + PAIRED_CASES if not case.is_attack]
    return [case.id for case in clean if rule_blocks(case)]


def main() -> int:
    print(f"{'':10}{'spec check':>12}{'software':>10}{'rule':>8}{'spec or rule':>14}")
    for name, cases in ROUNDS.items():
        blocked = score(cases)
        either = blocked["spec"] | blocked["rule"]
        total = len(cases)
        print(
            f"{name:10}{len(blocked['spec']):>9} of {total}{len(blocked['software']):>4} of {total}"
            f"{len(blocked['rule']):>2} of {total}{len(either):>8} of {total}"
        )
    flagged = clean_flagged()
    print(f"rule on the 44 correct changes in main, same-shape and paired: blocks {len(flagged)} {flagged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
