"""Deterministic stress tests for capital decision packets."""

from __future__ import annotations

from typing import Any


STRESS_SCENARIOS = (
    {
        "name": "edge_decay",
        "expected_return_multiplier": 0.50,
        "var_multiplier": 1.00,
        "drawdown_multiplier": 1.00,
        "backtest_excess_multiplier": 0.50,
        "shock_loss": 0.00,
    },
    {
        "name": "drawdown_spike",
        "expected_return_multiplier": 0.75,
        "var_multiplier": 1.50,
        "drawdown_multiplier": 2.00,
        "backtest_excess_multiplier": 0.75,
        "shock_loss": 0.00,
    },
    {
        "name": "liquidity_shock",
        "expected_return_multiplier": 0.65,
        "var_multiplier": 1.25,
        "drawdown_multiplier": 1.50,
        "backtest_excess_multiplier": 0.50,
        "shock_loss": 0.03,
    },
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _action_plan(packet: dict[str, Any]) -> dict[str, Any]:
    monte_run = packet.get("engine_runs", {}).get("monte_carlo")
    if not isinstance(monte_run, dict):
        return {}
    payload = monte_run.get("payload", {})
    if not isinstance(payload, dict):
        return {}
    action_plan = payload.get("action_plan", {})
    return action_plan if isinstance(action_plan, dict) else {}


def _rankings(packet: dict[str, Any]) -> dict[str, Any]:
    monte_run = packet.get("engine_runs", {}).get("monte_carlo")
    if not isinstance(monte_run, dict):
        return {}
    payload = monte_run.get("payload", {})
    if not isinstance(payload, dict):
        return {}
    rankings = payload.get("rankings", {})
    return rankings if isinstance(rankings, dict) else {}


def _backtest_summary(packet: dict[str, Any]) -> dict[str, Any]:
    backtest_run = packet.get("engine_runs", {}).get("monte_carlo_backtest")
    if not isinstance(backtest_run, dict):
        return {}
    payload = backtest_run.get("payload", {})
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary", {})
    return summary if isinstance(summary, dict) else {}


def stress_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic stress-test results for the primary pick."""

    action_plan = _action_plan(packet)
    primary = action_plan.get("primary_pick", {})
    if not isinstance(primary, dict) or not primary:
        return {
            "ok": False,
            "worst_margin": None,
            "scenarios": [],
            "reason": "missing primary pick",
        }

    ticker = str(primary.get("ticker", ""))
    weight = _number(primary.get("weight"))
    cash_weight = _number(action_plan.get("cash_weight"))
    expected_return = _number(primary.get("expected_return"))
    var95 = _number(primary.get("value_at_risk_95_pct"))

    rankings = _rankings(packet)
    ticker_row = rankings.get(ticker, {}) if isinstance(rankings, dict) else {}
    drawdown = _number(
        ticker_row.get("max_drawdown_q95") if isinstance(ticker_row, dict) else None
    )

    backtest = _backtest_summary(packet)
    backtest_excess_cash = _number(backtest.get("excess_return_vs_cash"))

    scenarios: list[dict[str, Any]] = []
    for scenario in STRESS_SCENARIOS:
        stressed_expected = expected_return * float(scenario["expected_return_multiplier"])
        stressed_var = var95 * float(scenario["var_multiplier"])
        stressed_drawdown = drawdown * float(scenario["drawdown_multiplier"])
        stressed_backtest = backtest_excess_cash * float(scenario["backtest_excess_multiplier"])
        shock_loss = float(scenario["shock_loss"])

        margin = (
            stressed_expected * weight
            + stressed_backtest
            - stressed_var * weight
            - stressed_drawdown * weight
            - shock_loss * weight
            + max(cash_weight, 0.0) * 0.005
        )
        scenarios.append(
            {
                "name": scenario["name"],
                "stressed_expected_return": stressed_expected,
                "stressed_var_95_pct": stressed_var,
                "stressed_drawdown_q95": stressed_drawdown,
                "stressed_backtest_excess_cash": stressed_backtest,
                "shock_loss": shock_loss,
                "portfolio_margin": margin,
                "passed": margin >= 0.0,
            }
        )

    worst_margin = min(_number(item.get("portfolio_margin")) for item in scenarios)
    return {
        "ok": all(bool(item["passed"]) for item in scenarios),
        "worst_margin": worst_margin,
        "scenarios": scenarios,
    }

