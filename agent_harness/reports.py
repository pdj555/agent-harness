"""Ledger analytics and promotion readiness reports."""

from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Any, Iterable

from agent_harness.trust_policy import empty_trust_policy, evaluate_repo_trust


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


def build_outcome_report(
    entries: list[dict[str, Any]],
    *,
    min_outcomes_for_promotion: int = 0,
    min_ok_rate: float | None = None,
    min_avg_excess_cash: float | None = None,
    min_avg_excess_equal_weight: float | None = None,
    max_avg_abs_forecast_error: float | None = None,
    max_realized_drawdown: float | None = None,
) -> dict[str, Any]:
    """Build aggregate realized-outcome metrics and readiness blockers."""

    ordered = list(entries)
    latest = ordered[-1] if ordered else {}
    outcome_count = len(ordered)
    scorecards = [
        entry.get("scorecard", {})
        for entry in ordered
        if isinstance(entry.get("scorecard"), dict)
    ]
    return_rows = [
        entry.get("returns", {})
        for entry in ordered
        if isinstance(entry.get("returns"), dict)
    ]
    primary_rows = [
        entry.get("primary_pick", {})
        for entry in ordered
        if isinstance(entry.get("primary_pick"), dict)
    ]
    risk_rows = [
        entry.get("risk", {})
        for entry in ordered
        if isinstance(entry.get("risk"), dict)
    ]
    attribution_rows = [
        entry.get("attribution", {})
        for entry in ordered
        if isinstance(entry.get("attribution"), dict)
    ]
    source_rows = [
        entry.get("sources", {})
        for entry in ordered
        if isinstance(entry.get("sources"), dict)
    ]

    primary_picks = [
        str(row.get("ticker"))
        for row in primary_rows
        if row.get("ticker")
    ]
    pick_counts = Counter(primary_picks)
    forecast_errors = [
        value
        for value in (_number(row.get("forecast_error")) for row in primary_rows)
        if value is not None
    ]
    abs_forecast_errors = [abs(value) for value in forecast_errors]
    allocation_returns = [
        value
        for value in (_number(row.get("allocation")) for row in return_rows)
        if value is not None
    ]
    excess_cash = [
        value
        for value in (_number(row.get("excess_vs_cash")) for row in return_rows)
        if value is not None
    ]
    excess_equal = [
        value
        for value in (_number(row.get("excess_vs_equal_weight")) for row in return_rows)
        if value is not None
    ]
    drawdowns = [
        value
        for value in (_number(row.get("realized_max_drawdown")) for row in risk_rows)
        if value is not None
    ]
    active_excess_rows = [
        row.get("active_excess", {})
        for row in attribution_rows
        if isinstance(row.get("active_excess"), dict)
    ]
    cash_attribution_rows = [
        row.get("cash", {})
        for row in attribution_rows
        if isinstance(row.get("cash"), dict)
    ]
    active_from_positions = [
        value
        for value in (_number(row.get("from_positions")) for row in active_excess_rows)
        if value is not None
    ]
    active_from_cash = [
        value
        for value in (_number(row.get("from_cash")) for row in active_excess_rows)
        if value is not None
    ]
    cash_drag = [
        value
        for value in (
            _number(row.get("drag_vs_equal_weight")) for row in cash_attribution_rows
        )
        if value is not None
    ]
    price_source_digests = Counter(
        str(row.get("price_source_digest"))
        for row in source_rows
        if row.get("price_source_digest")
    )

    latest_scorecard = (
        latest.get("scorecard", {})
        if isinstance(latest, dict) and isinstance(latest.get("scorecard"), dict)
        else {}
    )
    scorecard_ok_rate = _rate(bool(row.get("ok")) for row in scorecards)
    excess_cash_stats = _summary_stats(excess_cash)
    excess_equal_stats = _summary_stats(excess_equal)
    abs_forecast_error_stats = _summary_stats(abs_forecast_errors)
    drawdown_stats = _summary_stats(drawdowns)
    avg_excess_cash = excess_cash_stats["avg"]
    avg_excess_equal = excess_equal_stats["avg"]
    avg_abs_forecast_error = abs_forecast_error_stats["avg"]
    worst_drawdown = drawdown_stats["max"]

    blockers: list[str] = []
    if outcome_count < min_outcomes_for_promotion:
        blockers.append(f"needs at least {min_outcomes_for_promotion} realized outcomes")
    if min_outcomes_for_promotion and outcome_count and not latest_scorecard.get("ok"):
        blockers.append("latest realized outcome did not pass scorecard")
    required_excess_cash = (
        min_avg_excess_cash
        if min_avg_excess_cash is not None
        else 0.0
        if min_outcomes_for_promotion
        else None
    )
    required_excess_equal = (
        min_avg_excess_equal_weight
        if min_avg_excess_equal_weight is not None
        else 0.0
        if min_outcomes_for_promotion
        else None
    )
    if outcome_count and min_ok_rate is not None and scorecard_ok_rate < min_ok_rate:
        blockers.append(f"realized outcome ok rate below {min_ok_rate}")
    if outcome_count and required_excess_cash is not None and (
        avg_excess_cash is None or avg_excess_cash < required_excess_cash
    ):
        blockers.append(f"realized outcomes average excess vs cash below {required_excess_cash}")
    if outcome_count and required_excess_equal is not None and (
        avg_excess_equal is None or avg_excess_equal < required_excess_equal
    ):
        blockers.append(
            f"realized outcomes average excess vs equal weight below {required_excess_equal}"
        )
    if outcome_count and max_avg_abs_forecast_error is not None and (
        avg_abs_forecast_error is None
        or avg_abs_forecast_error > max_avg_abs_forecast_error
    ):
        blockers.append(
            f"realized outcomes average absolute forecast error above {max_avg_abs_forecast_error}"
        )
    if outcome_count and max_realized_drawdown is not None and (
        worst_drawdown is None or worst_drawdown > max_realized_drawdown
    ):
        blockers.append(f"realized max drawdown above {max_realized_drawdown}")

    return {
        "outcome_count": outcome_count,
        "latest_run_id": latest.get("run_id") if isinstance(latest, dict) else None,
        "latest_window": latest.get("window") if isinstance(latest, dict) else None,
        "scorecard": {
            "ok_rate": scorecard_ok_rate,
            "beat_cash_rate": _rate(bool(row.get("beat_cash")) for row in scorecards),
            "beat_equal_weight_rate": _rate(
                bool(row.get("beat_equal_weight")) for row in scorecards
            ),
            "primary_hit_rate": _rate(bool(row.get("primary_hit")) for row in scorecards),
        },
        "returns": {
            "allocation": _summary_stats(allocation_returns),
            "excess_vs_cash": excess_cash_stats,
            "excess_vs_equal_weight": excess_equal_stats,
        },
        "calibration": {
            "forecast_error": _summary_stats(forecast_errors),
            "absolute_forecast_error": abs_forecast_error_stats,
        },
        "risk": {
            "realized_max_drawdown": drawdown_stats,
        },
        "attribution": {
            "active_excess": {
                "from_positions": _summary_stats(active_from_positions),
                "from_cash": _summary_stats(active_from_cash),
            },
            "cash": {
                "drag_vs_equal_weight": _summary_stats(cash_drag),
            },
        },
        "sources": {
            "price_source_digests": dict(sorted(price_source_digests.items())),
            "unique_price_source_digest_count": len(price_source_digests),
        },
        "primary_picks": {
            "counts": dict(sorted(pick_counts.items())),
        },
        "promotion": {
            "ready": not blockers,
            "blockers": blockers,
            "min_outcomes": min_outcomes_for_promotion,
            "thresholds": {
                "min_ok_rate": min_ok_rate,
                "min_avg_excess_cash": required_excess_cash,
                "min_avg_excess_equal_weight": required_excess_equal,
                "max_avg_abs_forecast_error": max_avg_abs_forecast_error,
                "max_realized_drawdown": max_realized_drawdown,
            },
        },
    }


def build_ledger_report(
    entries: list[dict[str, Any]],
    *,
    min_runs_for_promotion: int = 3,
    trust_policy: dict[str, Any] | None = None,
    outcome_entries: list[dict[str, Any]] | None = None,
    min_outcomes_for_promotion: int = 0,
    outcome_thresholds: dict[str, Any] | None = None,
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
    latest_dirty_details = _latest_dirty_details(latest) if isinstance(latest, dict) else []
    latest_repo_trust_raw = (
        latest.get("repo_trust", {})
        if isinstance(latest, dict) and isinstance(latest.get("repo_trust"), dict)
        else {}
    )
    latest_repo_trust = (
        latest_repo_trust_raw
        if isinstance(latest_repo_trust_raw.get("adapters"), list)
        and latest_repo_trust_raw.get("adapters")
        else {"adapters": latest_dirty_details}
    )
    trust_evaluation = evaluate_repo_trust(
        latest_repo_trust,
        trust_policy=trust_policy or empty_trust_policy(),
    )
    outcome_report = build_outcome_report(
        outcome_entries or [],
        min_outcomes_for_promotion=min_outcomes_for_promotion,
        **(outcome_thresholds or {}),
    )
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
    if trust_evaluation["blocking_change_count"]:
        blockers.append("latest run has unapproved dirty repo changes")
    blockers.extend(outcome_report["promotion"]["blockers"])

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
            "latest_policy_evaluation": trust_evaluation,
            "latest_allowed_change_count": trust_evaluation["allowed_change_count"],
            "latest_blocking_change_count": trust_evaluation["blocking_change_count"],
        },
        "outcomes": outcome_report,
        "promotion": {
            "ready": not blockers,
            "blockers": blockers,
            "min_runs": min_runs_for_promotion,
            "min_outcomes": min_outcomes_for_promotion,
        },
    }
