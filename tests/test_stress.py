from __future__ import annotations

from agent_harness.stress import stress_packet


def test_stress_packet_passes_robust_positive_signal() -> None:
    packet = {
        "engine_runs": {
            "monte_carlo": {
                "payload": {
                    "action_plan": {
                        "cash_weight": 0.4,
                        "primary_pick": {
                            "ticker": "AAPL",
                            "weight": 0.6,
                            "expected_return": 0.18,
                            "value_at_risk_95_pct": 0.02,
                        },
                    },
                    "rankings": {"AAPL": {"max_drawdown_q95": 0.03}},
                }
            },
            "monte_carlo_backtest": {
                "payload": {"summary": {"excess_return_vs_cash": 0.03}}
            },
        }
    }

    result = stress_packet(packet)

    assert result["ok"]
    assert result["worst_margin"] > 0
    assert {scenario["name"] for scenario in result["scenarios"]} == {
        "edge_decay",
        "drawdown_spike",
        "liquidity_shock",
    }


def test_stress_packet_fails_missing_primary_pick() -> None:
    result = stress_packet({"engine_runs": {}})

    assert not result["ok"]
    assert result["reason"] == "missing primary pick"
