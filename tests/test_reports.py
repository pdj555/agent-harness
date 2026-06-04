from __future__ import annotations

from agent_harness.reports import build_ledger_report


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
    assert not report["promotion"]["ready"]
    assert "latest run has dirty repos" in report["promotion"]["blockers"]


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
