from __future__ import annotations

from pathlib import Path

from agent_harness.adapters import AdapterStatus, EngineRun
from agent_harness.capital import build_capital_loops
from agent_harness.evals import evaluate_packet
from agent_harness.packets import build_run_packet, packet_digest, validate_run_packet


def _packet(tmp_path: Path) -> dict:
    statuses = {
        "monte-carlo": AdapterStatus(
            name="monte-carlo",
            available=True,
            repo_path=tmp_path / "monte-carlo",
            reason="ready",
            command=("monte-carlo", "simulate"),
            capabilities=("simulation", "allocation"),
            repo_sha="abc",
            repo_dirty=False,
        ),
        "stock-sentiment-analysis": AdapterStatus(
            name="stock-sentiment-analysis",
            available=False,
            repo_path=tmp_path / "stock",
            reason="OPENAI_API_KEY not set",
            repo_sha="def",
            repo_dirty=False,
        ),
    }
    run = EngineRun(
        name="monte-carlo",
        ok=True,
        summary="Lean in",
        payload={
            "action_plan": {
                "headline": "Lean in",
                "cash_weight": 0.4,
                "primary_pick": {
                    "ticker": "AAPL",
                    "weight": 0.6,
                    "expected_return": 0.18,
                    "value_at_risk_95_pct": 0.02,
                },
            },
            "rankings": {"AAPL": {"max_drawdown_q95": 0.03}},
        },
        command=("monte-carlo", "simulate", "AAPL"),
        repo_sha="abc",
        repo_dirty=False,
    )
    backtest = EngineRun(
        name="monte-carlo-backtest",
        ok=True,
        summary="Strategy return: 3.0%",
        payload={
            "summary": {
                "strategy_total_return": 0.03,
                "strategy_max_drawdown": 0.0,
                "strategy_win_rate": 1.0,
                "excess_return_vs_cash": 0.02,
            }
        },
        repo_sha="abc",
        repo_dirty=False,
    )
    loops = build_capital_loops(
        statuses,
        monte_carlo_run=run,
        monte_carlo_backtest=backtest,
    )
    return build_run_packet(
        namespace_root=tmp_path,
        invocation=["agent-harness", "thesis"],
        inputs={"tickers": ["AAPL"], "ran_backtest": True},
        statuses=statuses,
        monte_carlo_run=run,
        monte_carlo_backtest=backtest,
        ranked_loops=loops,
    )


def test_packet_digest_validates(tmp_path: Path) -> None:
    packet = _packet(tmp_path)

    assert packet["content_digest"] == packet_digest(packet)
    assert validate_run_packet(packet) == []
    assert packet["stress_tests"]["ok"]
    monte_status = packet["adapters"]["monte-carlo"]
    assert "repo_branch" in monte_status
    assert monte_status["repo_status"] == []
    assert monte_status["repo_status_count"] == 0
    assert monte_status["repo_status_truncated"] is False


def test_evaluate_packet_requires_risk_gate_first(tmp_path: Path) -> None:
    packet = _packet(tmp_path)

    result = evaluate_packet(packet)

    assert result["ok"]
    assert result["passed"] == result["total"]
