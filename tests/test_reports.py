from __future__ import annotations

from agent_harness.reports import (
    build_ledger_report,
    build_outcome_calibration_report,
    build_outcome_report,
    build_promotion_attempt_report,
    build_regime_report,
)
from agent_harness.trust_policy import TRUST_POLICY_SCHEMA_VERSION


def _outcome_entry(
    *,
    run_id: str = "run_1",
    ok: bool = True,
    allocation: float = 0.03,
    excess_cash: float = 0.02,
    excess_equal: float = 0.01,
    forecast_error: float = -0.05,
    drawdown: float = 0.01,
    active_positions: float | None = None,
    active_cash: float = 0.0,
    cash_drag: float = -0.001,
    source_digest: str = "source_a",
    sentiment_present: bool = True,
    sentiment_directional_hit: bool | None = True,
    sentiment_signed_return: float | None = 0.03,
    sentiment_alignment: float | None = 0.012,
) -> dict:
    if active_positions is None:
        active_positions = excess_equal
    return {
        "entry_type": "outcome",
        "run_id": run_id,
        "window": {"start_date": "2024-01-02", "end_date": "2024-01-04"},
        "scorecard": {
            "ok": ok,
            "beat_cash": excess_cash >= 0,
            "beat_equal_weight": excess_equal >= 0,
            "primary_hit": allocation >= 0,
        },
        "returns": {
            "allocation": allocation,
            "cash": 0.0,
            "equal_weight": allocation - excess_equal,
            "excess_vs_cash": excess_cash,
            "excess_vs_equal_weight": excess_equal,
        },
        "primary_pick": {
            "ticker": "AAPL",
            "expected_return": 0.08,
            "realized_return": 0.03,
            "forecast_error": forecast_error,
            "hit": allocation >= 0,
        },
        "risk": {"realized_max_drawdown": drawdown},
        "attribution": {
            "active_excess": {
                "vs_equal_weight": excess_equal,
                "from_positions": active_positions,
                "from_cash": active_cash,
            },
            "cash": {
                "drag_vs_equal_weight": cash_drag,
            },
        },
        "sources": {
            "price_source_digest": source_digest,
        },
        "sentiment": {
            "present": sentiment_present,
            "ok": sentiment_present,
            "ticker": "AAPL" if sentiment_present else None,
            "ticker_matches_primary": sentiment_present,
            "score": 0.5 if sentiment_present else None,
            "confidence": 0.8 if sentiment_present else None,
            "signal": "buy" if sentiment_present else None,
            "classification_degraded": False,
            "directional_hit": sentiment_directional_hit,
            "signed_realized_return": sentiment_signed_return,
            "score_return_alignment": (
                None if sentiment_alignment is None else sentiment_alignment / 0.8
            ),
            "confidence_weighted_alignment": sentiment_alignment,
        },
    }


def _regime_entry(
    *,
    run_id: str = "run_1",
    ok: bool = True,
    fragile_count: int = 0,
    worst_drawdown: float = 0.02,
    worst_excess_cash: float = 0.01,
    worst_excess_equal: float = 0.01,
) -> dict:
    return {
        "entry_type": "regime_replay",
        "run_id": run_id,
        "content_digest": f"digest_{run_id}",
        "report_digest": f"regime_digest_{run_id}",
        "summary": {
            "ok": ok,
            "regime_count": 4,
            "scorecard_pass_count": 4 - fragile_count,
            "fragile_count": fragile_count,
            "fragile_regimes": ["primary_reversal"][:fragile_count],
            "worst_excess_vs_cash": worst_excess_cash,
            "worst_excess_vs_equal_weight": worst_excess_equal,
            "worst_drawdown": worst_drawdown,
            "max_drawdown": 0.08,
            "primary_reversal_loss": 0.04 if ok else -0.1,
        },
        "regimes": [
            {
                "name": "primary_reversal",
                "fragility_ok": ok,
                "fragility_reasons": [] if ok else ["primary pick lost capital"],
                "excess_vs_cash": worst_excess_cash,
                "excess_vs_equal_weight": worst_excess_equal,
                "realized_max_drawdown": worst_drawdown,
            }
        ],
    }


def test_ledger_report_aggregates_backtest_and_trust_metrics() -> None:
    entries = [
        {
            "run_id": "run_1",
            "eval_ok": True,
            "eval_score": 1.0,
            "monte_carlo_ok": True,
            "monte_carlo_backtest_ok": True,
            "dirty_repos": [],
            "primary_pick": {"ticker": "AAPL"},
            "backtest": {
                "excess_return_vs_cash": 0.01,
                "excess_return_vs_equal_weight": 0.02,
                "strategy_max_drawdown": 0.03,
            },
            "stress": {"ok": True, "worst_margin": 0.02},
            "stock_sentiment_ok": True,
            "sentiment": {
                "ticker": "AAPL",
                "score": 0.2,
                "confidence": 0.7,
                "signal": "buy",
                "classification_degraded": False,
            },
        },
        {
            "run_id": "run_2",
            "eval_ok": True,
            "eval_score": 0.9,
            "monte_carlo_ok": True,
            "monte_carlo_backtest_ok": True,
            "dirty_repos": ["monte-carlo"],
            "repo_trust": {
                "dirty_details": [
                    {
                        "name": "monte-carlo",
                        "repo_branch": "main",
                        "repo_sha": "abc123",
                        "repo_status": [" M decision.py"],
                        "repo_status_count": 1,
                        "repo_status_truncated": False,
                    }
                ],
                "adapters": [],
            },
            "primary_pick": {"ticker": "AAPL"},
            "backtest": {
                "excess_return_vs_cash": 0.03,
                "excess_return_vs_equal_weight": 0.01,
                "strategy_max_drawdown": 0.02,
            },
            "stress": {"ok": True, "worst_margin": 0.04},
            "stock_sentiment_ok": False,
            "sentiment": {
                "ticker": "AAPL",
                "score": -0.1,
                "confidence": 0.5,
                "signal": "hold",
                "classification_degraded": True,
            },
        },
    ]

    report = build_ledger_report(entries, min_runs_for_promotion=2)

    assert report["run_count"] == 2
    assert report["primary_picks"]["most_common"] == "AAPL"
    assert report["primary_picks"]["most_common_share"] == 1.0
    assert report["backtest"]["excess_return_vs_cash"]["avg"] == 0.02
    assert report["stress"]["ok_rate"] == 1.0
    assert report["stress"]["worst_margin"]["min"] == 0.02
    assert report["sentiment"]["ok_rate"] == 0.5
    assert report["sentiment"]["score"]["avg"] == 0.05
    assert report["sentiment"]["degraded_rate"] == 0.5
    assert report["trust"]["dirty_repos"] == {"monte-carlo": 1}
    assert report["trust"]["latest_dirty_details"][0]["repo_status"] == [" M decision.py"]
    assert report["trust"]["latest_blocking_change_count"] == 1
    assert not report["promotion"]["ready"]
    assert "latest run has unapproved dirty repo changes" in report["promotion"]["blockers"]


def test_ledger_report_can_promote_clean_positive_runs() -> None:
    entries = []
    for index in range(3):
        entries.append(
            {
                "run_id": f"run_{index}",
                "eval_ok": True,
                "eval_score": 1.0,
                "monte_carlo_ok": True,
                "monte_carlo_backtest_ok": True,
                "dirty_repos": [],
                "primary_pick": {"ticker": "AAPL"},
                "backtest": {
                    "excess_return_vs_cash": 0.02,
                    "excess_return_vs_equal_weight": 0.01,
                    "strategy_max_drawdown": 0.0,
                },
                "stress": {"ok": True, "worst_margin": 0.03},
            }
        )

    report = build_ledger_report(entries, min_runs_for_promotion=3)

    assert report["promotion"]["ready"]
    assert report["promotion"]["blockers"] == []


def test_promotion_attempt_report_ranks_recurring_blockers() -> None:
    attempts = [
        {
            "promotion_id": "promotion_1",
            "created_at": "2026-06-05T00:00:00+00:00",
            "run_id": "run_a",
            "status": "blocked",
            "blockers": [
                "latest backtest did not beat cash",
                "walk-forward backtest failed threshold",
                "latest stress tests failed",
            ],
        },
        {
            "promotion_id": "promotion_2",
            "created_at": "2026-06-05T00:01:00+00:00",
            "run_id": "run_b",
            "status": "blocked",
            "blockers": ["latest backtest did not beat cash"],
        },
        {
            "promotion_id": "promotion_3",
            "created_at": "2026-06-05T00:02:00+00:00",
            "run_id": "run_b",
            "status": "promoted",
            "blockers": [],
        },
    ]

    report = build_promotion_attempt_report(attempts)

    assert report["attempt_count"] == 3
    assert report["blocked_count"] == 2
    assert report["promoted_count"] == 1
    assert report["promotion_rate"] == 1 / 3
    assert report["recent"]["attempt_count"] == 3
    assert report["recent"]["promotion_rate"] == 1 / 3
    assert report["attempted_run_count"] == 2
    assert report["latest"]["promotion_id"] == "promotion_3"
    assert report["blockers"]["unique_count"] == 3
    assert report["blockers"]["top"][0]["blocker"] == "latest backtest did not beat cash"
    assert report["blockers"]["top"][0]["category"] == "backtest"
    assert report["blockers"]["top"][0]["count"] == 2
    assert report["blockers"]["top"][0]["recent_count"] == 2
    assert report["blockers"]["top"][0]["latest"]["run_id"] == "run_b"
    assert report["categories"]["top"][0]["category"] == "backtest"
    assert report["categories"]["top"][0]["count"] == 2


def test_promotion_attempt_report_tracks_recent_window() -> None:
    attempts = [
        {
            "promotion_id": "promotion_1",
            "created_at": "2026-06-05T00:00:00+00:00",
            "run_id": "run_a",
            "status": "blocked",
            "blockers": ["latest run has unapproved dirty repo changes"],
        },
        {
            "promotion_id": "promotion_2",
            "created_at": "2026-06-05T00:01:00+00:00",
            "run_id": "run_a",
            "status": "blocked",
            "blockers": ["latest run has no regime replay"],
        },
        {
            "promotion_id": "promotion_3",
            "created_at": "2026-06-05T00:02:00+00:00",
            "run_id": "run_b",
            "status": "promoted",
            "blockers": [],
        },
    ]

    report = build_promotion_attempt_report(attempts, recent_window=2)

    assert report["promotion_rate"] == 1 / 3
    assert report["recent"]["attempt_count"] == 2
    assert report["recent"]["promotion_rate"] == 0.5
    assert report["categories"]["top"][0]["category"] in {"regime_replay", "trust"}
    category_counts = {
        row["category"]: row["recent_count"]
        for row in report["categories"]["top"]
    }
    assert category_counts["trust"] == 0
    assert category_counts["regime_replay"] == 1


def test_ledger_report_includes_promotion_attempt_analytics() -> None:
    entries = []
    for index in range(3):
        entries.append(
            {
                "run_id": f"run_{index}",
                "eval_ok": True,
                "eval_score": 1.0,
                "monte_carlo_ok": True,
                "monte_carlo_backtest_ok": True,
                "dirty_repos": [],
                "primary_pick": {"ticker": "AAPL"},
                "backtest": {
                    "excess_return_vs_cash": 0.02,
                    "excess_return_vs_equal_weight": 0.01,
                    "strategy_max_drawdown": 0.0,
                },
                "stress": {"ok": True, "worst_margin": 0.03},
            }
        )

    report = build_ledger_report(
        entries,
        min_runs_for_promotion=3,
        promotion_attempts=[
            {
                "promotion_id": "promotion_blocked",
                "created_at": "2026-06-05T00:00:00+00:00",
                "run_id": "run_2",
                "status": "blocked",
                "blockers": ["latest run has unapproved dirty repo changes"],
            }
        ],
    )

    assert report["promotion"]["ready"]
    assert report["promotion_attempts"]["attempt_count"] == 1
    assert (
        report["promotion_attempts"]["blockers"]["top"][0]["blocker"]
        == "latest run has unapproved dirty repo changes"
    )


def test_ledger_report_can_promote_policy_allowed_dirty_docs() -> None:
    entries = []
    for index in range(3):
        entries.append(
            {
                "run_id": f"run_{index}",
                "eval_ok": True,
                "eval_score": 1.0,
                "monte_carlo_ok": True,
                "monte_carlo_backtest_ok": True,
                "dirty_repos": ["agent-harness-ledger"],
                "repo_trust": {
                    "adapters": [
                        {
                            "name": "agent-harness-ledger",
                            "repo_dirty": True,
                            "repo_branch": "main",
                            "repo_sha": "abc",
                            "repo_status": [" M README.md"],
                            "repo_status_count": 1,
                        }
                    ],
                    "dirty_details": [],
                },
                "primary_pick": {"ticker": "AAPL"},
                "backtest": {
                    "excess_return_vs_cash": 0.02,
                    "excess_return_vs_equal_weight": 0.01,
                    "strategy_max_drawdown": 0.0,
                },
                "stress": {"ok": True, "worst_margin": 0.03},
            }
        )

    report = build_ledger_report(
        entries,
        min_runs_for_promotion=3,
        trust_policy={
            "schema_version": TRUST_POLICY_SCHEMA_VERSION,
            "loaded": True,
            "allowed_dirty": [
                {
                    "id": "docs",
                    "repo": "agent-harness-ledger",
                    "patterns": ["README.md"],
                    "statuses": ["M"],
                }
            ],
        },
    )

    assert report["trust"]["latest_allowed_change_count"] == 1
    assert report["trust"]["latest_blocking_change_count"] == 0
    assert report["promotion"]["ready"]


def test_outcome_report_aggregates_realized_performance() -> None:
    report = build_outcome_report(
        [
            _outcome_entry(run_id="run_1", allocation=0.03, excess_cash=0.02, excess_equal=0.01),
            _outcome_entry(
                run_id="run_2",
                allocation=-0.01,
                excess_cash=-0.01,
                excess_equal=-0.02,
                ok=False,
                sentiment_directional_hit=False,
                sentiment_signed_return=-0.01,
                sentiment_alignment=-0.004,
            ),
        ],
        min_outcomes_for_promotion=2,
    )

    assert report["outcome_count"] == 2
    assert report["scorecard"]["ok_rate"] == 0.5
    assert report["scorecard"]["beat_cash_rate"] == 0.5
    assert report["returns"]["excess_vs_cash"]["avg"] == 0.005
    assert report["calibration"]["absolute_forecast_error"]["avg"] == 0.05
    assert report["attribution"]["active_excess"]["from_positions"]["avg"] == -0.005
    assert report["attribution"]["cash"]["drag_vs_equal_weight"]["avg"] == -0.001
    assert report["sources"]["price_source_digests"] == {"source_a": 2}
    assert report["sentiment"]["present_count"] == 2
    assert report["sentiment"]["directional_hit_rate"] == 0.5
    assert report["sentiment"]["confidence_weighted_alignment"]["avg"] == 0.004
    assert not report["promotion"]["ready"]
    assert report["promotion"]["latest_scorecard_ok"] is False
    assert "latest realized outcome did not pass scorecard" not in report["promotion"]["blockers"]


def test_outcome_report_does_not_veto_calibrated_sample_on_latest_failure() -> None:
    report = build_outcome_report(
        [
            _outcome_entry(
                run_id="run_1",
                allocation=0.04,
                excess_cash=0.03,
                excess_equal=0.02,
                ok=True,
            ),
            _outcome_entry(
                run_id="run_2",
                allocation=0.01,
                excess_cash=0.01,
                excess_equal=0.01,
                ok=False,
            ),
        ],
        min_outcomes_for_promotion=2,
    )

    assert report["promotion"]["latest_scorecard_ok"] is False
    assert report["promotion"]["ready"]
    assert report["promotion"]["blockers"] == []


def test_outcome_report_applies_calibration_and_drawdown_thresholds() -> None:
    report = build_outcome_report(
        [
            _outcome_entry(
                allocation=0.03,
                excess_cash=0.02,
                excess_equal=0.01,
                forecast_error=-0.08,
                drawdown=0.07,
            )
        ],
        min_outcomes_for_promotion=1,
        min_ok_rate=1.0,
        min_avg_excess_cash=0.01,
        min_avg_excess_equal_weight=0.0,
        max_avg_abs_forecast_error=0.05,
        max_realized_drawdown=0.05,
    )

    assert not report["promotion"]["ready"]
    assert (
        "realized outcomes average absolute forecast error above 0.05"
        in report["promotion"]["blockers"]
    )
    assert "realized max drawdown above 0.05" in report["promotion"]["blockers"]
    assert report["promotion"]["thresholds"]["max_avg_abs_forecast_error"] == 0.05


def test_outcome_report_applies_return_thresholds() -> None:
    report = build_outcome_report(
        [_outcome_entry(excess_cash=0.004, excess_equal=0.003)],
        min_outcomes_for_promotion=1,
        min_avg_excess_cash=0.005,
        min_avg_excess_equal_weight=0.004,
    )

    assert not report["promotion"]["ready"]
    assert "realized outcomes average excess vs cash below 0.005" in report["promotion"]["blockers"]
    assert (
        "realized outcomes average excess vs equal weight below 0.004"
        in report["promotion"]["blockers"]
    )


def test_outcome_report_applies_sentiment_thresholds() -> None:
    report = build_outcome_report(
        [
            _outcome_entry(
                sentiment_directional_hit=False,
                sentiment_signed_return=-0.02,
                sentiment_alignment=-0.008,
            )
        ],
        min_sentiment_directional_count=2,
        min_sentiment_hit_rate=0.8,
        min_avg_sentiment_alignment=0.0,
    )

    assert not report["promotion"]["ready"]
    assert (
        "needs at least 2 realized sentiment directional outcomes"
        in report["promotion"]["blockers"]
    )
    assert "realized sentiment directional hit rate below 0.8" in report["promotion"]["blockers"]
    assert (
        "realized sentiment average confidence-weighted alignment below 0.0"
        in report["promotion"]["blockers"]
    )
    assert report["promotion"]["thresholds"]["min_sentiment_hit_rate"] == 0.8


def test_outcome_calibration_recommends_thresholds_from_quantiles() -> None:
    report = build_outcome_calibration_report(
        [
            _outcome_entry(excess_cash=0.01, excess_equal=0.02, forecast_error=-0.01, drawdown=0.01),
            _outcome_entry(excess_cash=0.02, excess_equal=0.03, forecast_error=-0.02, drawdown=0.02),
            _outcome_entry(excess_cash=0.03, excess_equal=0.04, forecast_error=-0.04, drawdown=0.03),
            _outcome_entry(excess_cash=0.04, excess_equal=0.05, forecast_error=-0.08, drawdown=0.04),
        ],
        min_sample=4,
        sentiment_min_sample=4,
    )

    thresholds = report["recommended_thresholds"]
    assert report["ready"]
    assert report["sample_sufficient"]
    assert thresholds["min_outcomes"] == 4
    assert thresholds["min_ok_rate"] == 0.95
    assert thresholds["min_avg_excess_cash"] == 0.0175
    assert thresholds["min_avg_excess_equal_weight"] == 0.0275
    assert thresholds["max_avg_abs_forecast_error"] == 0.05
    assert thresholds["max_realized_drawdown"] == 0.04
    assert thresholds["min_sentiment_directional_count"] == 4
    assert thresholds["min_sentiment_hit_rate"] == 0.95
    assert "--max-outcome-forecast-error" in report["ledger_report_flags"]


def test_outcome_calibration_rounds_drawdown_ceiling_upward() -> None:
    report = build_outcome_calibration_report(
        [
            _outcome_entry(drawdown=0.01149916844856258),
            _outcome_entry(drawdown=0.01),
        ],
        min_sample=2,
    )

    assert report["recommended_thresholds"]["max_realized_drawdown"] == 0.0115
    assert build_outcome_report(
        [_outcome_entry(drawdown=0.01149916844856258)],
        min_outcomes_for_promotion=1,
        max_realized_drawdown=report["recommended_thresholds"]["max_realized_drawdown"],
    )["promotion"]["ready"]


def test_outcome_calibration_blocks_when_sample_is_too_small() -> None:
    report = build_outcome_calibration_report([_outcome_entry()], min_sample=3)

    assert not report["ready"]
    assert "needs at least 3 realized outcomes for calibration" in report["blockers"]


def test_ledger_report_can_gate_on_realized_outcomes() -> None:
    entries = []
    for index in range(3):
        entries.append(
            {
                "run_id": f"run_{index}",
                "eval_ok": True,
                "eval_score": 1.0,
                "monte_carlo_ok": True,
                "monte_carlo_backtest_ok": True,
                "dirty_repos": [],
                "primary_pick": {"ticker": "AAPL"},
                "backtest": {
                    "excess_return_vs_cash": 0.02,
                    "excess_return_vs_equal_weight": 0.01,
                    "strategy_max_drawdown": 0.0,
                },
                "stress": {"ok": True, "worst_margin": 0.03},
            }
        )

    blocked = build_ledger_report(
        entries,
        min_runs_for_promotion=3,
        min_outcomes_for_promotion=1,
    )
    ready = build_ledger_report(
        entries,
        min_runs_for_promotion=3,
        outcome_entries=[_outcome_entry()],
        min_outcomes_for_promotion=1,
    )

    assert not blocked["promotion"]["ready"]
    assert "needs at least 1 realized outcomes" in blocked["promotion"]["blockers"]
    assert ready["promotion"]["ready"]
    assert ready["outcomes"]["outcome_count"] == 1


def test_regime_report_blocks_stale_or_fragile_replay() -> None:
    stale = build_regime_report(
        [_regime_entry(run_id="run_old")],
        latest_run_id="run_latest",
        min_regime_replays_for_promotion=1,
        require_latest_run=True,
        require_ok=True,
        max_fragile_count=0,
    )
    fragile = build_regime_report(
        [
            _regime_entry(
                run_id="run_latest",
                ok=False,
                fragile_count=2,
                worst_drawdown=0.12,
                worst_excess_cash=-0.06,
                worst_excess_equal=-0.04,
            )
        ],
        latest_run_id="run_latest",
        min_regime_replays_for_promotion=1,
        require_latest_run=True,
        require_ok=True,
        max_fragile_count=0,
        max_worst_drawdown=0.08,
        min_worst_excess_cash=0.0,
        min_worst_excess_equal_weight=0.0,
    )

    assert not stale["promotion"]["ready"]
    assert "latest run has no regime replay" in stale["promotion"]["blockers"]
    assert not fragile["promotion"]["ready"]
    assert "latest regime replay is fragile" in fragile["promotion"]["blockers"]
    assert "latest regime replay fragile count above 0" in fragile["promotion"]["blockers"]
    assert "latest regime replay worst drawdown above 0.08" in fragile["promotion"]["blockers"]
    assert "latest regime replay worst excess vs cash below 0.0" in fragile["promotion"]["blockers"]
    assert fragile["fragility"]["failed_regime_counts"] == {"primary_reversal": 1}


def test_ledger_report_can_gate_on_regime_replay() -> None:
    entries = []
    for index in range(3):
        entries.append(
            {
                "run_id": f"run_{index}",
                "eval_ok": True,
                "eval_score": 1.0,
                "monte_carlo_ok": True,
                "monte_carlo_backtest_ok": True,
                "dirty_repos": [],
                "primary_pick": {"ticker": "AAPL"},
                "backtest": {
                    "excess_return_vs_cash": 0.02,
                    "excess_return_vs_equal_weight": 0.01,
                    "strategy_max_drawdown": 0.0,
                },
                "stress": {"ok": True, "worst_margin": 0.03},
            }
        )

    blocked = build_ledger_report(
        entries,
        min_runs_for_promotion=3,
        min_regime_replays_for_promotion=1,
        regime_thresholds={"require_latest_run": True, "require_ok": True},
    )
    stale = build_ledger_report(
        entries,
        min_runs_for_promotion=3,
        regime_entries=[_regime_entry(run_id="run_1")],
        min_regime_replays_for_promotion=1,
        regime_thresholds={"require_latest_run": True, "require_ok": True},
    )
    ready = build_ledger_report(
        entries,
        min_runs_for_promotion=3,
        regime_entries=[_regime_entry(run_id="run_2")],
        min_regime_replays_for_promotion=1,
        regime_thresholds={
            "require_latest_run": True,
            "require_ok": True,
            "max_fragile_count": 0,
            "max_worst_drawdown": 0.08,
            "min_worst_excess_cash": 0.0,
            "min_worst_excess_equal_weight": 0.0,
        },
    )

    assert not blocked["promotion"]["ready"]
    assert "needs at least 1 regime replays" in blocked["promotion"]["blockers"]
    assert not stale["promotion"]["ready"]
    assert "latest run has no regime replay" in stale["promotion"]["blockers"]
    assert ready["promotion"]["ready"]
    assert ready["regimes"]["latest_matches_run"]
