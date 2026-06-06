"""Ledger analytics and promotion readiness reports."""

from __future__ import annotations

import math
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


def _quantile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    bounded = min(1.0, max(0.0, quantile))
    index = bounded * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: list[float]) -> dict[str, Any]:
    stats = _summary_stats(values)
    return {
        **stats,
        "count": len(values),
        "p10": _quantile(values, 0.10),
        "p25": _quantile(values, 0.25),
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
        "p95": _quantile(values, 0.95),
    }


def _promotion_blockers(row: dict[str, Any]) -> list[str]:
    raw_blockers = row.get("blockers")
    if isinstance(raw_blockers, list):
        return [
            str(blocker).strip()
            for blocker in raw_blockers
            if blocker is not None and str(blocker).strip()
        ]
    if raw_blockers:
        blocker = str(raw_blockers).strip()
        return [blocker] if blocker else []
    return []


def _promotion_blocker_category(blocker: str) -> str:
    scoped = blocker.lower()
    if "dirty" in scoped or "repo" in scoped or "trust" in scoped:
        return "trust"
    if "regime" in scoped:
        return "regime_replay"
    if (
        "realized" in scoped
        or "outcome" in scoped
        or "forecast" in scoped
        or "sentiment" in scoped
    ):
        return "realized_outcomes"
    if "backtest" in scoped or "walk-forward" in scoped or "beat cash" in scoped:
        return "backtest"
    if "stress" in scoped:
        return "stress"
    if "monte carlo" in scoped or "simulation" in scoped:
        return "simulation"
    if "eval" in scoped:
        return "eval"
    if "fixture" in scoped or "price" in scoped:
        return "fixtures"
    if "ledger runs" in scoped or "min_runs" in scoped:
        return "sample_depth"
    return "other"


def _rank_promotion_counts(
    counts: Counter[str],
    latest: dict[str, dict[str, Any]],
) -> list[tuple[str, int]]:
    ranked = sorted(counts.items(), key=lambda item: item[0])
    ranked = sorted(
        ranked,
        key=lambda item: str(latest.get(item[0], {}).get("created_at") or ""),
        reverse=True,
    )
    return sorted(ranked, key=lambda item: item[1], reverse=True)


def build_promotion_attempt_report(
    attempts: list[dict[str, Any]],
    *,
    recent_window: int = 5,
) -> dict[str, Any]:
    """Rank promotion blockers across persisted promotion attempts."""

    ordered = sorted(
        (dict(row) for row in attempts if isinstance(row, dict)),
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("promotion_id") or ""),
        ),
    )
    attempt_count = len(ordered)
    blocked = [row for row in ordered if row.get("status") == "blocked"]
    promoted = [row for row in ordered if row.get("status") == "promoted"]
    scoped_recent_window = max(0, int(recent_window))
    recent = ordered[-scoped_recent_window:] if scoped_recent_window else []
    blocker_counts: Counter[str] = Counter()
    recent_blocker_counts: Counter[str] = Counter()
    blocker_latest: dict[str, dict[str, Any]] = {}
    category_counts: Counter[str] = Counter()
    recent_category_counts: Counter[str] = Counter()
    category_latest: dict[str, dict[str, Any]] = {}
    for row in ordered:
        row_categories = set()
        for blocker in dict.fromkeys(_promotion_blockers(row)):
            category = _promotion_blocker_category(blocker)
            blocker_counts[blocker] += 1
            blocker_latest[blocker] = {
                "promotion_id": row.get("promotion_id"),
                "run_id": row.get("run_id"),
                "created_at": row.get("created_at"),
                "status": row.get("status"),
            }
            row_categories.add(category)
            category_latest[category] = blocker_latest[blocker]
        for category in row_categories:
            category_counts[category] += 1
    for row in recent:
        recent_row_categories = set()
        for blocker in dict.fromkeys(_promotion_blockers(row)):
            recent_blocker_counts[blocker] += 1
            recent_row_categories.add(_promotion_blocker_category(blocker))
        for category in recent_row_categories:
            recent_category_counts[category] += 1
    top_blockers = [
        {
            "blocker": blocker,
            "category": _promotion_blocker_category(blocker),
            "count": count,
            "share_of_attempts": count / attempt_count if attempt_count else 0.0,
            "recent_count": recent_blocker_counts.get(blocker, 0),
            "recent_share_of_attempts": (
                recent_blocker_counts.get(blocker, 0) / len(recent) if recent else 0.0
            ),
            "latest": blocker_latest.get(blocker, {}),
        }
        for blocker, count in _rank_promotion_counts(blocker_counts, blocker_latest)
    ]
    top_categories = [
        {
            "category": category,
            "count": count,
            "share_of_attempts": count / attempt_count if attempt_count else 0.0,
            "recent_count": recent_category_counts.get(category, 0),
            "recent_share_of_attempts": (
                recent_category_counts.get(category, 0) / len(recent) if recent else 0.0
            ),
            "latest": category_latest.get(category, {}),
        }
        for category, count in _rank_promotion_counts(category_counts, category_latest)
    ]
    latest = ordered[-1] if ordered else {}
    run_ids = {str(row.get("run_id")) for row in ordered if row.get("run_id")}
    recent_promoted = [row for row in recent if row.get("status") == "promoted"]
    return {
        "attempt_count": attempt_count,
        "blocked_count": len(blocked),
        "promoted_count": len(promoted),
        "promotion_rate": len(promoted) / attempt_count if attempt_count else 0.0,
        "attempted_run_count": len(run_ids),
        "recent": {
            "window": scoped_recent_window,
            "attempt_count": len(recent),
            "blocked_count": sum(1 for row in recent if row.get("status") == "blocked"),
            "promoted_count": len(recent_promoted),
            "promotion_rate": len(recent_promoted) / len(recent) if recent else 0.0,
        },
        "latest": {
            "promotion_id": latest.get("promotion_id"),
            "run_id": latest.get("run_id"),
            "created_at": latest.get("created_at"),
            "status": latest.get("status"),
            "blockers": _promotion_blockers(latest),
        },
        "blockers": {
            "unique_count": len(blocker_counts),
            "top": top_blockers,
        },
        "categories": {
            "unique_count": len(category_counts),
            "top": top_categories,
        },
    }


def _threshold(value: float | None, *, floor: float | None = None, cap: float | None = None) -> float | None:
    if value is None:
        return None
    scoped = value
    if floor is not None:
        scoped = max(floor, scoped)
    if cap is not None:
        scoped = min(cap, scoped)
    return round(scoped, 6)


def _upper_threshold(value: float | None, *, floor: float | None = None, cap: float | None = None) -> float | None:
    if value is None:
        return None
    scoped = value
    if floor is not None:
        scoped = max(floor, scoped)
    if cap is not None:
        scoped = min(cap, scoped)
    return math.ceil(scoped * 1_000_000.0) / 1_000_000.0


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
    min_sentiment_directional_count: int = 0,
    min_sentiment_hit_rate: float | None = None,
    min_avg_sentiment_alignment: float | None = None,
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
    sentiment_rows = [
        entry.get("sentiment", {})
        for entry in ordered
        if isinstance(entry.get("sentiment"), dict)
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
    sentiment_present_rows = [row for row in sentiment_rows if row.get("present")]
    sentiment_directional_rows = [
        row for row in sentiment_present_rows if row.get("directional_hit") is not None
    ]
    sentiment_signed_returns = [
        value
        for value in (
            _number(row.get("signed_realized_return")) for row in sentiment_directional_rows
        )
        if value is not None
    ]
    sentiment_score_alignment = [
        value
        for value in (
            _number(row.get("score_return_alignment")) for row in sentiment_present_rows
        )
        if value is not None
    ]
    sentiment_weighted_alignment = [
        value
        for value in (
            _number(row.get("confidence_weighted_alignment"))
            for row in sentiment_present_rows
        )
        if value is not None
    ]
    sentiment_directional_hit_rate = _rate(
        bool(row.get("directional_hit")) for row in sentiment_directional_rows
    )
    sentiment_weighted_alignment_stats = _summary_stats(sentiment_weighted_alignment)
    avg_sentiment_alignment = sentiment_weighted_alignment_stats["avg"]

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
    if len(sentiment_directional_rows) < min_sentiment_directional_count:
        blockers.append(
            f"needs at least {min_sentiment_directional_count} realized sentiment directional outcomes"
        )
    if min_sentiment_hit_rate is not None and (
        not sentiment_directional_rows
        or sentiment_directional_hit_rate < min_sentiment_hit_rate
    ):
        blockers.append(
            f"realized sentiment directional hit rate below {min_sentiment_hit_rate}"
        )
    if min_avg_sentiment_alignment is not None and (
        avg_sentiment_alignment is None
        or avg_sentiment_alignment < min_avg_sentiment_alignment
    ):
        blockers.append(
            "realized sentiment average confidence-weighted alignment "
            f"below {min_avg_sentiment_alignment}"
        )

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
        "sentiment": {
            "present_count": len(sentiment_present_rows),
            "directional_count": len(sentiment_directional_rows),
            "directional_hit_rate": sentiment_directional_hit_rate,
            "degraded_rate": _rate(
                bool(row.get("classification_degraded")) for row in sentiment_present_rows
            ),
            "signed_realized_return": _summary_stats(sentiment_signed_returns),
            "score_return_alignment": _summary_stats(sentiment_score_alignment),
            "confidence_weighted_alignment": sentiment_weighted_alignment_stats,
            "latest": (
                latest.get("sentiment", {})
                if isinstance(latest, dict) and isinstance(latest.get("sentiment"), dict)
                else {}
            ),
        },
        "primary_picks": {
            "counts": dict(sorted(pick_counts.items())),
        },
        "promotion": {
            "ready": not blockers,
            "blockers": blockers,
            "min_outcomes": min_outcomes_for_promotion,
            "latest_scorecard_ok": (
                bool(latest_scorecard.get("ok"))
                if latest_scorecard.get("ok") is not None
                else None
            ),
            "thresholds": {
                "min_ok_rate": min_ok_rate,
                "min_avg_excess_cash": required_excess_cash,
                "min_avg_excess_equal_weight": required_excess_equal,
                "max_avg_abs_forecast_error": max_avg_abs_forecast_error,
                "max_realized_drawdown": max_realized_drawdown,
                "min_sentiment_directional_count": min_sentiment_directional_count,
                "min_sentiment_hit_rate": min_sentiment_hit_rate,
                "min_avg_sentiment_alignment": min_avg_sentiment_alignment,
            },
        },
    }


def build_regime_report(
    entries: list[dict[str, Any]],
    *,
    latest_run_id: str | None = None,
    min_regime_replays_for_promotion: int = 0,
    require_latest_run: bool = False,
    require_ok: bool = False,
    max_fragile_count: int | None = None,
    max_worst_drawdown: float | None = None,
    min_worst_excess_cash: float | None = None,
    min_worst_excess_equal_weight: float | None = None,
) -> dict[str, Any]:
    """Build aggregate deterministic-regime replay metrics and blockers."""

    ordered = list(entries)
    replay_count = len(ordered)
    matching_latest = [
        entry for entry in ordered if latest_run_id and entry.get("run_id") == latest_run_id
    ]
    latest = matching_latest[-1] if matching_latest else ordered[-1] if ordered else {}
    latest_summary = (
        latest.get("summary", {})
        if isinstance(latest, dict) and isinstance(latest.get("summary"), dict)
        else {}
    )
    summary_rows = [
        entry.get("summary", {})
        for entry in ordered
        if isinstance(entry.get("summary"), dict)
    ]
    regime_rows = [
        row
        for entry in ordered
        for row in (
            entry.get("regimes", [])
            if isinstance(entry.get("regimes"), list)
            else []
        )
        if isinstance(row, dict)
    ]
    failed_regime_counts = Counter(
        str(row.get("name"))
        for row in regime_rows
        if row.get("name") and row.get("fragility_ok") is False
    )
    fragile_counts = [
        value
        for value in (_number(row.get("fragile_count")) for row in summary_rows)
        if value is not None
    ]
    worst_drawdowns = [
        value
        for value in (_number(row.get("worst_drawdown")) for row in summary_rows)
        if value is not None
    ]
    worst_excess_cash = [
        value
        for value in (_number(row.get("worst_excess_vs_cash")) for row in summary_rows)
        if value is not None
    ]
    worst_excess_equal = [
        value
        for value in (
            _number(row.get("worst_excess_vs_equal_weight")) for row in summary_rows
        )
        if value is not None
    ]
    latest_fragile_count = _number(latest_summary.get("fragile_count"))
    latest_worst_drawdown = _number(latest_summary.get("worst_drawdown"))
    latest_worst_excess_cash = _number(latest_summary.get("worst_excess_vs_cash"))
    latest_worst_excess_equal = _number(latest_summary.get("worst_excess_vs_equal_weight"))
    latest_ok = bool(latest_summary.get("ok")) if latest_summary else False
    latest_matches_run = bool(latest_run_id and matching_latest)

    blockers: list[str] = []
    if replay_count < min_regime_replays_for_promotion:
        blockers.append(f"needs at least {min_regime_replays_for_promotion} regime replays")
    if require_latest_run and latest_run_id and not latest_matches_run:
        blockers.append("latest run has no regime replay")
    if require_ok and (not latest_summary or not latest_ok):
        blockers.append("latest regime replay is fragile")
    if max_fragile_count is not None and (
        latest_fragile_count is None or latest_fragile_count > max_fragile_count
    ):
        blockers.append(f"latest regime replay fragile count above {max_fragile_count}")
    if max_worst_drawdown is not None and (
        latest_worst_drawdown is None or latest_worst_drawdown > max_worst_drawdown
    ):
        blockers.append(f"latest regime replay worst drawdown above {max_worst_drawdown}")
    if min_worst_excess_cash is not None and (
        latest_worst_excess_cash is None
        or latest_worst_excess_cash < min_worst_excess_cash
    ):
        blockers.append(
            f"latest regime replay worst excess vs cash below {min_worst_excess_cash}"
        )
    if min_worst_excess_equal_weight is not None and (
        latest_worst_excess_equal is None
        or latest_worst_excess_equal < min_worst_excess_equal_weight
    ):
        blockers.append(
            "latest regime replay worst excess vs equal weight "
            f"below {min_worst_excess_equal_weight}"
        )

    return {
        "replay_count": replay_count,
        "latest_run_id": latest.get("run_id") if isinstance(latest, dict) else None,
        "latest_matches_run": latest_matches_run,
        "latest_report_digest": latest.get("report_digest") if isinstance(latest, dict) else None,
        "latest_summary": latest_summary,
        "scorecard": {
            "ok_rate": _rate(bool(row.get("ok")) for row in summary_rows),
        },
        "fragility": {
            "fragile_count": _summary_stats(fragile_counts),
            "failed_regime_counts": dict(sorted(failed_regime_counts.items())),
        },
        "returns": {
            "worst_excess_vs_cash": _summary_stats(worst_excess_cash),
            "worst_excess_vs_equal_weight": _summary_stats(worst_excess_equal),
        },
        "risk": {
            "worst_drawdown": _summary_stats(worst_drawdowns),
        },
        "promotion": {
            "ready": not blockers,
            "blockers": blockers,
            "thresholds": {
                "min_regime_replays": min_regime_replays_for_promotion,
                "require_latest_run": require_latest_run,
                "require_ok": require_ok,
                "max_fragile_count": max_fragile_count,
                "max_worst_drawdown": max_worst_drawdown,
                "min_worst_excess_cash": min_worst_excess_cash,
                "min_worst_excess_equal_weight": min_worst_excess_equal_weight,
            },
        },
    }


def build_outcome_calibration_report(
    entries: list[dict[str, Any]],
    *,
    min_sample: int = 20,
    sentiment_min_sample: int = 10,
) -> dict[str, Any]:
    """Recommend production outcome gates from realized ledger evidence."""

    ordered = list(entries)
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
    sentiment_rows = [
        entry.get("sentiment", {})
        for entry in ordered
        if isinstance(entry.get("sentiment"), dict)
    ]

    abs_forecast_errors = [
        abs(value)
        for value in (_number(row.get("forecast_error")) for row in primary_rows)
        if value is not None
    ]
    drawdowns = [
        value
        for value in (_number(row.get("realized_max_drawdown")) for row in risk_rows)
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
    sentiment_present_rows = [row for row in sentiment_rows if row.get("present")]
    sentiment_directional_rows = [
        row for row in sentiment_present_rows if row.get("directional_hit") is not None
    ]
    sentiment_alignment = [
        value
        for value in (
            _number(row.get("confidence_weighted_alignment"))
            for row in sentiment_present_rows
        )
        if value is not None
    ]
    outcome_count = len(ordered)
    ok_rate = _rate(bool(row.get("ok")) for row in scorecards)
    sentiment_hit_rate = _rate(
        bool(row.get("directional_hit")) for row in sentiment_directional_rows
    )
    excess_cash_distribution = _distribution(excess_cash)
    excess_equal_distribution = _distribution(excess_equal)
    abs_forecast_distribution = _distribution(abs_forecast_errors)
    drawdown_distribution = _distribution(drawdowns)
    sentiment_alignment_distribution = _distribution(sentiment_alignment)

    recommended: dict[str, Any] = {
        "min_outcomes": min_sample,
        "min_ok_rate": _threshold(ok_rate - 0.05, floor=0.0, cap=1.0)
        if outcome_count
        else None,
        "min_avg_excess_cash": _threshold(
            excess_cash_distribution["p25"],
            floor=0.0,
        ),
        "min_avg_excess_equal_weight": _threshold(
            excess_equal_distribution["p25"],
            floor=0.0,
        ),
        "max_avg_abs_forecast_error": _threshold(abs_forecast_distribution["p75"]),
        "max_realized_drawdown": _upper_threshold(drawdown_distribution["max"]),
        "min_sentiment_directional_count": (
            sentiment_min_sample
            if len(sentiment_directional_rows) >= sentiment_min_sample
            else 0
        ),
        "min_sentiment_hit_rate": (
            _threshold(sentiment_hit_rate - 0.05, floor=0.5, cap=1.0)
            if len(sentiment_directional_rows) >= sentiment_min_sample
            else None
        ),
        "min_avg_sentiment_alignment": (
            _threshold(sentiment_alignment_distribution["p25"], floor=0.0)
            if len(sentiment_directional_rows) >= sentiment_min_sample
            else None
        ),
    }
    flags = [
        "--min-outcomes",
        str(recommended["min_outcomes"]),
    ]
    if recommended["min_ok_rate"] is not None:
        flags.extend(["--min-outcome-ok-rate", str(recommended["min_ok_rate"])])
    if recommended["min_avg_excess_cash"] is not None:
        flags.extend(["--min-outcome-excess-cash", str(recommended["min_avg_excess_cash"])])
    if recommended["min_avg_excess_equal_weight"] is not None:
        flags.extend(
            [
                "--min-outcome-excess-equal",
                str(recommended["min_avg_excess_equal_weight"]),
            ]
        )
    if recommended["max_avg_abs_forecast_error"] is not None:
        flags.extend(
            [
                "--max-outcome-forecast-error",
                str(recommended["max_avg_abs_forecast_error"]),
            ]
        )
    if recommended["max_realized_drawdown"] is not None:
        flags.extend(["--max-outcome-drawdown", str(recommended["max_realized_drawdown"])])
    if recommended["min_sentiment_directional_count"]:
        flags.extend(
            [
                "--min-outcome-sentiment-outcomes",
                str(recommended["min_sentiment_directional_count"]),
            ]
        )
    if recommended["min_sentiment_hit_rate"] is not None:
        flags.extend(
            [
                "--min-outcome-sentiment-hit-rate",
                str(recommended["min_sentiment_hit_rate"]),
            ]
        )
    if recommended["min_avg_sentiment_alignment"] is not None:
        flags.extend(
            [
                "--min-outcome-sentiment-alignment",
                str(recommended["min_avg_sentiment_alignment"]),
            ]
        )

    blockers: list[str] = []
    if outcome_count < min_sample:
        blockers.append(f"needs at least {min_sample} realized outcomes for calibration")
    if len(abs_forecast_errors) < outcome_count:
        blockers.append("some outcomes lack forecast-error measurements")
    if len(drawdowns) < outcome_count:
        blockers.append("some outcomes lack drawdown measurements")
    if sentiment_present_rows and len(sentiment_directional_rows) < sentiment_min_sample:
        blockers.append(
            f"needs at least {sentiment_min_sample} sentiment directional outcomes for sentiment gates"
        )

    return {
        "outcome_count": outcome_count,
        "min_sample": min_sample,
        "sample_sufficient": outcome_count >= min_sample,
        "sentiment_min_sample": sentiment_min_sample,
        "distributions": {
            "excess_vs_cash": excess_cash_distribution,
            "excess_vs_equal_weight": excess_equal_distribution,
            "absolute_forecast_error": abs_forecast_distribution,
            "realized_max_drawdown": drawdown_distribution,
            "sentiment_confidence_weighted_alignment": sentiment_alignment_distribution,
        },
        "rates": {
            "ok_rate": ok_rate,
            "beat_cash_rate": _rate(bool(row.get("beat_cash")) for row in scorecards),
            "beat_equal_weight_rate": _rate(
                bool(row.get("beat_equal_weight")) for row in scorecards
            ),
            "primary_hit_rate": _rate(bool(row.get("primary_hit")) for row in scorecards),
            "sentiment_directional_count": len(sentiment_directional_rows),
            "sentiment_directional_hit_rate": sentiment_hit_rate,
        },
        "recommended_thresholds": recommended,
        "ledger_report_flags": flags,
        "ready": not blockers,
        "blockers": blockers,
        "rationale": {
            "min_avg_excess_cash": "p25 realized excess vs cash, floored at 0",
            "min_avg_excess_equal_weight": "p25 realized excess vs equal weight, floored at 0",
            "max_avg_abs_forecast_error": "p75 absolute forecast error",
            "max_realized_drawdown": "max observed realized drawdown",
            "min_ok_rate": "observed ok rate minus 5 percentage points",
        },
    }


def build_ledger_report(
    entries: list[dict[str, Any]],
    *,
    min_runs_for_promotion: int = 3,
    trust_policy: dict[str, Any] | None = None,
    promotion_attempts: list[dict[str, Any]] | None = None,
    outcome_entries: list[dict[str, Any]] | None = None,
    min_outcomes_for_promotion: int = 0,
    outcome_thresholds: dict[str, Any] | None = None,
    regime_entries: list[dict[str, Any]] | None = None,
    min_regime_replays_for_promotion: int = 0,
    regime_thresholds: dict[str, Any] | None = None,
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
    promotion_attempt_report = build_promotion_attempt_report(promotion_attempts or [])
    regime_report = build_regime_report(
        regime_entries or [],
        latest_run_id=latest.get("run_id") if isinstance(latest, dict) else None,
        min_regime_replays_for_promotion=min_regime_replays_for_promotion,
        **(regime_thresholds or {}),
    )
    stress_rows = [
        entry.get("stress", {})
        for entry in ordered
        if isinstance(entry.get("stress"), dict)
    ]
    sentiment_rows = [
        entry.get("sentiment", {})
        for entry in ordered
        if isinstance(entry.get("sentiment"), dict)
    ]
    stress_margins = [
        value
        for value in (_number(row.get("worst_margin")) for row in stress_rows)
        if value is not None
    ]
    latest_stress = latest.get("stress", {}) if isinstance(latest, dict) else {}
    latest_stress_ok = bool(latest_stress.get("ok")) if isinstance(latest_stress, dict) else False
    sentiment_scores = [
        value
        for value in (_number(row.get("score")) for row in sentiment_rows)
        if value is not None
    ]
    sentiment_confidences = [
        value
        for value in (_number(row.get("confidence")) for row in sentiment_rows)
        if value is not None
    ]
    latest_sentiment = latest.get("sentiment", {}) if isinstance(latest, dict) else {}

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
    blockers.extend(regime_report["promotion"]["blockers"])

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
        "sentiment": {
            "ok_rate": _rate(bool(entry.get("stock_sentiment_ok")) for entry in ordered),
            "score": _summary_stats(sentiment_scores),
            "confidence": _summary_stats(sentiment_confidences),
            "degraded_rate": _rate(
                bool(row.get("classification_degraded")) for row in sentiment_rows
            ),
            "latest": latest_sentiment if isinstance(latest_sentiment, dict) else {},
        },
        "trust": {
            "dirty_run_rate": dirty_run_count / run_count if run_count else 0.0,
            "dirty_repos": dict(sorted(dirty_counter.items())),
            "latest_dirty_details": latest_dirty_details,
            "latest_policy_evaluation": trust_evaluation,
            "latest_allowed_change_count": trust_evaluation["allowed_change_count"],
            "latest_blocking_change_count": trust_evaluation["blocking_change_count"],
        },
        "promotion_attempts": promotion_attempt_report,
        "outcomes": outcome_report,
        "regimes": regime_report,
        "promotion": {
            "ready": not blockers,
            "blockers": blockers,
            "min_runs": min_runs_for_promotion,
            "min_outcomes": min_outcomes_for_promotion,
            "min_regime_replays": min_regime_replays_for_promotion,
        },
    }
