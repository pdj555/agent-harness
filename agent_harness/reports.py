"""Ledger analytics and promotion readiness reports."""

from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Any, Iterable


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(values: Iterable[bool]) -> float:
    scoped = list(values)
    if not scoped:
        return 0.0
    return sum(1 for value in scoped if value) / len(scoped)


def _summary_stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "median": None, "min": None, "max": None}
    return {
        "avg": mean(values),
        "median": median(values),
        "min": min(values),
        "max": max(values),
    }


def _dirty_repos_for_entry(entry: dict[str, Any]) -> list[str]:
    dirty_repos = entry.get("dirty_repos", [])
    if isinstance(dirty_repos, list):
        return [str(repo) for repo in dirty_repos]
    trust = entry.get("repo_trust", {})
    if not isinstance(trust, dict):
        return []
    dirty_details = trust.get("dirty_details", [])
    if not isinstance(dirty_details, list):
        return []
    return [
        str(detail.get("name"))
        for detail in dirty_details
        if isinstance(detail, dict) and detail.get("name")
    ]


def _latest_dirty_details(entry: dict[str, Any]) -> list[dict[str, Any]]:
    trust = entry.get("repo_trust", {})
    if isinstance(trust, dict):
        dirty_details = trust.get("dirty_details", [])
        if isinstance(dirty_details, list):
            return [detail for detail in dirty_details if isinstance(detail, dict)]
    return [{"name": repo} for repo in _dirty_repos_for_entry(entry)]


def build_ledger_report(
    entries: list[dict[str, Any]],
    *,
    min_runs_for_promotion: int = 3,
) -> dict[str, Any]:
    """Build aggregate metrics and promotion readiness from ledger entries."""

    ordered = list(entries)
    latest = ordered[-1] if ordered else {}
    run_count = len(ordered)

    eval_scores = [
        value
        for value in (_number(entry.get("eval_score")) for entry in ordered)
        if value is not None
    ]
    primary_picks = [
        entry.get("primary_pick", {}).get("ticker")
        for entry in ordered
        if isinstance(entry.get("primary_pick"), dict)
        and entry.get("primary_pick", {}).get("ticker")
    ]
    pick_counts = Counter(str(pick) for pick in primary_picks)
    most_common_pick, most_common_pick_count = (
        pick_counts.most_common(1)[0] if pick_counts else (None, 0)
    )

    dirty_counter: Counter[str] = Counter()
    dirty_run_count = 0
    for entry in ordered:
        dirty_repos = _dirty_repos_for_entry(entry)
        if dirty_repos:
            dirty_run_count += 1
        dirty_counter.update(dirty_repos)

    backtest_rows = [
        entry.get("backtest", {})
        for entry in ordered
        if isinstance(entry.get("backtest"), dict)
    ]
    excess_cash = [
        value
        for value in (
            _number(row.get("excess_return_vs_cash")) for row in backtest_rows
        )
        if value is not None
    ]
    excess_equal = [
        value
        for value in (
            _number(row.get("excess_return_vs_equal_weight")) for row in backtest_rows
        )
        if value is not None
    ]
    drawdowns = [
        value
        for value in (
            _number(row.get("strategy_max_drawdown")) for row in backtest_rows
        )
        if value is not None
    ]

    latest_backtest = latest.get("backtest", {}) if isinstance(latest, dict) else {}
    latest_excess_cash = (
        _number(latest_backtest.get("excess_return_vs_cash"))
        if isinstance(latest_backtest, dict)
        else None
    )
    latest_dirty_repos = _dirty_repos_for_entry(latest) if isinstance(latest, dict) else []
    latest_dirty_details = _latest_dirty_details(latest) if isinstance(latest, dict) else []
    stress_rows = [
        entry.get("stress", {})
        for entry in ordered
        if isinstance(entry.get("stress"), dict)
    ]
    stress_margins = [
        value
        for value in (_number(row.get("worst_margin")) for row in stress_rows)
        if value is not None
    ]
    latest_stress = latest.get("stress", {}) if isinstance(latest, dict) else {}
    latest_stress_ok = bool(latest_stress.get("ok")) if isinstance(latest_stress, dict) else False

    blockers: list[str] = []
    if run_count < min_runs_for_promotion:
        blockers.append(f"needs at least {min_runs_for_promotion} ledger runs")
    if not latest.get("eval_ok"):
        blockers.append("latest run did not pass eval")
    if not latest.get("monte_carlo_ok"):
        blockers.append("latest run did not execute Monte Carlo simulation")
    if not latest.get("monte_carlo_backtest_ok"):
        blockers.append("latest run did not execute walk-forward backtest")
    if latest_excess_cash is None or latest_excess_cash < 0:
        blockers.append("latest backtest did not beat cash")
    if not latest_stress_ok:
        blockers.append("latest stress tests failed")
    if latest_dirty_repos:
        blockers.append("latest run has dirty repos")

    return {
        "run_count": run_count,
        "latest_run_id": latest.get("run_id") if isinstance(latest, dict) else None,
        "eval": {
            "ok_rate": _rate(bool(entry.get("eval_ok")) for entry in ordered),
            "score": _summary_stats(eval_scores),
        },
        "engines": {
            "monte_carlo_ok_rate": _rate(
                bool(entry.get("monte_carlo_ok")) for entry in ordered
            ),
            "backtest_ok_rate": _rate(
                bool(entry.get("monte_carlo_backtest_ok")) for entry in ordered
            ),
        },
        "primary_picks": {
            "counts": dict(sorted(pick_counts.items())),
            "most_common": most_common_pick,
            "most_common_share": (
                most_common_pick_count / len(primary_picks) if primary_picks else 0.0
            ),
        },
        "backtest": {
            "excess_return_vs_cash": _summary_stats(excess_cash),
            "excess_return_vs_equal_weight": _summary_stats(excess_equal),
            "strategy_max_drawdown": _summary_stats(drawdowns),
            "positive_excess_cash_rate": _rate(value >= 0 for value in excess_cash),
        },
        "stress": {
            "ok_rate": _rate(
                bool(row.get("ok")) for row in stress_rows
            ),
            "worst_margin": _summary_stats(stress_margins),
        },
        "trust": {
            "dirty_run_rate": dirty_run_count / run_count if run_count else 0.0,
            "dirty_repos": dict(sorted(dirty_counter.items())),
            "latest_dirty_details": latest_dirty_details,
        },
        "promotion": {
            "ready": not blockers,
            "blockers": blockers,
            "min_runs": min_runs_for_promotion,
        },
    }
