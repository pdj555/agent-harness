from __future__ import annotations

from pathlib import Path

from agent_harness.adapters import AdapterStatus, EngineRun
from agent_harness.capital import CapitalLoop, build_capital_loops, rank_loops


def test_rank_loops_rewards_edge_confidence_and_penalizes_tail_risk() -> None:
    strong = CapitalLoop(
        name="strong",
        repo="repo",
        thesis="high edge",
        expected_edge=0.4,
        confidence=0.9,
        max_loss=0.05,
        implementation_effort=2.0,
        half_life_days=30.0,
        evidence=("x",),
    )
    weak = CapitalLoop(
        name="weak",
        repo="repo",
        thesis="fragile",
        expected_edge=0.4,
        confidence=0.3,
        max_loss=0.3,
        implementation_effort=2.0,
        half_life_days=30.0,
        evidence=("x",),
    )

    assert rank_loops([weak, strong])[0] is strong


def test_build_capital_loops_promotes_successful_monte_carlo_run() -> None:
    statuses = {
        "monte-carlo": AdapterStatus(
            name="monte-carlo",
            available=True,
            repo_path=Path("/tmp/monte-carlo"),
            reason="ready",
        ),
        "stock-sentiment-analysis": AdapterStatus(
            name="stock-sentiment-analysis",
            available=False,
            repo_path=Path("/tmp/stock"),
            reason="OPENAI_API_KEY not set",
        ),
    }
    run = EngineRun(
        name="monte-carlo",
        ok=True,
        summary="ok",
        payload={
            "action_plan": {
                "headline": "Concentrate in AAPL",
                "cash_weight": 0.4,
                "primary_pick": {
                    "ticker": "AAPL",
                    "expected_return": 0.18,
                    "prob_above_current": 0.75,
                    "value_at_risk_95_pct": 0.04,
                },
            },
            "rankings": {"AAPL": {"max_drawdown_q95": 0.08}},
            "errors": [],
        },
    )

    loops = build_capital_loops(statuses, monte_carlo_run=run)

    assert loops[0].name == "risk-first allocation loop"
    assert any("Concentrate in AAPL" in item for item in loops[0].evidence)
