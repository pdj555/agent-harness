from __future__ import annotations

from agent_harness.reports import build_ledger_report, build_outcome_report
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
        },
    ]

    report = build_ledger_report(entries, min_runs_for_promotion=2)

    assert report["run_count"] == 2
    assert report["primary_picks"]["most_common"] == "AAPL"
    assert report["primary_picks"]["most_common_share"] == 1.0
    assert report["backtest"]["excess_return_vs_cash"]["avg"] == 0.02
    assert report["stress"]["ok_rate"] == 1.0
    assert report["stress"]["worst_margin"]["min"] == 0.02
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
            _outcome_entry(run_id="run_2", allocation=-0.01, excess_cash=-0.01, excess_equal=-0.02, ok=False),
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
    assert not report["promotion"]["ready"]
    assert "latest realized outcome did not pass scorecard" in report["promotion"]["blockers"]


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
