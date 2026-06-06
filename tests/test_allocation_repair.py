from __future__ import annotations

from pathlib import Path

from agent_harness.adapters import EngineRun
from agent_harness.allocation_repair import repair_monte_carlo_allocation
from agent_harness.evals import evaluate_packet
from agent_harness.packets import packet_digest
from agent_harness.regimes import evaluate_packet_regimes


def _run() -> EngineRun:
    return EngineRun(
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
                    "prob_above_current": 0.7,
                    "value_at_risk_95_pct": 0.03,
                },
            },
            "allocations": {"AAPL": {"weight": 0.6}},
            "rankings": {
                "AAPL": {"max_drawdown_q95": 0.03},
                "MSFT": {"max_drawdown_q95": 0.02},
            },
            "errors": [],
        },
    )


def _packet(payload: dict, tmp_path: Path, *, tickers: list[str] | None = None) -> dict:
    scoped_tickers = tickers or ["AAPL", "MSFT"]
    packet = {
        "schema_version": "agent-harness.run.v1",
        "run_id": "run_repair",
        "created_at": "2026-06-05T00:00:00+00:00",
        "namespace_root": str(tmp_path),
        "inputs": {"tickers": scoped_tickers, "ran_backtest": False},
        "risk_controls": {
            "max_position_weight": 0.60,
            "min_cash_buffer_when_concentrated": 0.20,
            "concentration_weight": 0.50,
            "execution_authority": "research_only",
            "requires_human_approval_for_orders": True,
        },
        "adapters": {
            "monte-carlo": {
                "name": "monte-carlo",
                "repo_sha": "abc",
                "repo_branch": "main",
                "repo_dirty": False,
                "repo_status": [],
                "repo_status_count": 0,
                "repo_status_truncated": False,
            }
        },
        "engine_runs": {"monte_carlo": {"ok": True, "payload": payload}},
        "ranked_loops": [{"repo": "monte-carlo", "name": "risk-first allocation loop"}],
        "stress_tests": {"ok": True, "worst_margin": 0.1},
    }
    packet["content_digest"] = packet_digest(packet)
    return packet


def test_repair_monte_carlo_allocation_passes_deterministic_regimes(tmp_path: Path) -> None:
    raw = _run()
    raw_report = evaluate_packet_regimes(
        _packet(raw.payload, tmp_path),
        output_dir=tmp_path / "raw-regimes",
    )

    repaired = repair_monte_carlo_allocation(raw, tickers=["AAPL", "MSFT"])
    assert repaired is not None
    payload = repaired.payload
    action_plan = payload["action_plan"]
    allocation_repair = payload["allocation_repair"]
    repaired_report = evaluate_packet_regimes(
        _packet(payload, tmp_path),
        output_dir=tmp_path / "repaired-regimes",
    )

    assert not raw_report["summary"]["ok"]
    assert raw_report["summary"]["fragile_count"] == 3
    assert allocation_repair["applied"]
    assert allocation_repair["max_position_policy"]["enforced"] is False
    assert allocation_repair["max_position_policy"]["reason"] == "insufficient_non_primary_alternatives"
    assert allocation_repair["max_position_policy"]["violations"] == {"MSFT": 0.85}
    assert allocation_repair["original"]["summary"]["fragile_count"] == 3
    assert allocation_repair["selected"]["summary"]["fragile_count"] == 0
    assert action_plan["cash_weight"] == 0.0
    assert action_plan["primary_pick"]["weight"] == 0.15
    assert action_plan["headline"] == "Regime-repaired allocation: AAPL 15.0%, MSFT 85.0%; cash 0.0%"
    assert action_plan["raw_headline"] == "Lean in"
    assert payload["allocations"]["AAPL"]["weight"] == 0.15
    assert payload["allocations"]["MSFT"]["weight"] == 0.85
    assert repaired_report["summary"]["ok"]
    assert repaired_report["summary"]["fragile_regimes"] == []
    assert "Regime repair: fragile=3->0" in repaired.summary
    assert "max_position_policy=deferred" in repaired.summary
    eval_result = evaluate_packet(_packet(payload, tmp_path))
    assert not eval_result["ok"]
    assert any(
        check["name"] == "allocation_rows_cap_respected" and not check["passed"]
        for check in eval_result["checks"]
    )


def test_repair_monte_carlo_allocation_can_be_left_unapplied(tmp_path: Path) -> None:
    run = _run()
    run.payload["action_plan"]["cash_weight"] = 0.0
    run.payload["action_plan"]["primary_pick"]["weight"] = 0.15
    run.payload["allocations"] = {
        "AAPL": {"weight": 0.15},
        "MSFT": {"weight": 0.85},
    }

    repaired = repair_monte_carlo_allocation(run, tickers=["AAPL", "MSFT"])

    assert repaired is not None
    assert repaired.payload["allocation_repair"]["applied"] is False
    assert repaired.payload["allocation_repair"]["reason"] == (
        "no candidate improved deterministic regime replay score"
    )
    assert repaired.payload["allocation_repair"]["max_position_policy"]["enforced"] is False


def test_repair_monte_carlo_allocation_enforces_position_cap_when_universe_is_wide(
    tmp_path: Path,
) -> None:
    raw = _run()
    repaired = repair_monte_carlo_allocation(raw, tickers=["AAPL", "MSFT", "GOOGL"])
    assert repaired is not None
    payload = repaired.payload
    allocation_repair = payload["allocation_repair"]
    repaired_report = evaluate_packet_regimes(
        _packet(payload, tmp_path, tickers=["AAPL", "MSFT", "GOOGL"]),
        output_dir=tmp_path / "wide-regimes",
    )

    assert allocation_repair["applied"]
    assert allocation_repair["max_position_policy"]["enforced"] is True
    assert allocation_repair["max_position_policy"]["ok"]
    assert allocation_repair["max_position_policy"]["violations"] == {}
    assert payload["allocations"]["AAPL"]["weight"] == 0.15
    assert payload["allocations"]["MSFT"]["weight"] == 0.425
    assert payload["allocations"]["GOOGL"]["weight"] == 0.425
    assert repaired_report["summary"]["ok"]
    eval_result = evaluate_packet(_packet(payload, tmp_path, tickers=["AAPL", "MSFT", "GOOGL"]))
    assert eval_result["ok"]


def test_repair_monte_carlo_allocation_prefers_stress_safe_candidate() -> None:
    raw = EngineRun(
        name="monte-carlo",
        ok=True,
        summary="Lean in",
        payload={
            "action_plan": {
                "headline": "Lean in",
                "cash_weight": 0.4,
                "primary_pick": {
                    "ticker": "JPM",
                    "weight": 0.6,
                    "expected_return": 0.14437711052724667,
                    "prob_above_current": 0.97,
                    "value_at_risk_95_pct": 0.0,
                },
            },
            "allocations": {"JPM": {"weight": 0.6}},
            "rankings": {
                "AAPL": {"max_drawdown_q95": 0.010960724646825565},
                "GOOGL": {"max_drawdown_q95": 0.021126054635007392},
                "JPM": {"max_drawdown_q95": 0.06071083255023414},
                "MSFT": {"max_drawdown_q95": 0.20557606886081228},
                "XOM": {"max_drawdown_q95": 0.13270866637856252},
            },
            "errors": [],
        },
    )
    backtest = EngineRun(
        name="monte-carlo-backtest",
        ok=True,
        summary="Strategy return",
        payload={"summary": {"excess_return_vs_cash": 0.004273881392899526}},
    )

    repaired = repair_monte_carlo_allocation(
        raw,
        tickers=["AAPL", "MSFT", "GOOGL", "JPM", "XOM"],
        backtest_run=backtest,
    )

    assert repaired is not None
    payload = repaired.payload
    assert payload["allocation_repair"]["applied"]
    assert payload["allocation_repair"]["selected"]["stress"]["ok"]
    assert payload["allocation_repair"]["selected"]["stress"]["worst_margin"] > 0
    assert payload["action_plan"]["primary_pick"]["weight"] == 0.05
    assert payload["allocations"]["JPM"]["weight"] == 0.05
    assert "stress_ok=True" in repaired.summary
