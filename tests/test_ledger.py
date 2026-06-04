from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness.adapters import AdapterStatus, EngineRun
from agent_harness.capital import build_capital_loops
from agent_harness.ledger import ingest_packet, read_ledger_entries
from agent_harness.packets import build_run_packet, packet_digest
from agent_harness.store import write_packet


def _packet(tmp_path: Path, *, run_suffix: str = "a") -> dict:
    statuses = {
        "monte-carlo": AdapterStatus(
            name="monte-carlo",
            available=True,
            repo_path=tmp_path / "monte-carlo",
            reason="ready",
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
                    "prob_above_current": 0.7,
                    "value_at_risk_95_pct": 0.03,
                },
            },
            "rankings": {"AAPL": {"max_drawdown_q95": 0.03}},
            "errors": [],
        },
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
    packet = build_run_packet(
        namespace_root=tmp_path,
        invocation=["agent-harness", "thesis"],
        inputs={"tickers": ["AAPL"], "ran_backtest": True},
        statuses=statuses,
        monte_carlo_run=run,
        monte_carlo_backtest=backtest,
        ranked_loops=loops,
    )
    packet["run_id"] = f"run_test_{run_suffix}"
    packet["content_digest"] = packet_digest(packet)
    return packet


def test_ingest_packet_is_idempotent(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    packet_path = write_packet(packet, tmp_path / "runs")

    first = ingest_packet(packet, packet_path=packet_path, ledger_dir=tmp_path / "ledger")
    second = ingest_packet(packet, packet_path=packet_path, ledger_dir=tmp_path / "ledger")

    assert first == second
    assert len(read_ledger_entries(tmp_path / "ledger")) == 1
    assert (tmp_path / "ledger" / "packets" / "run_test_a.json").exists()
    assert first["backtest"]["excess_return_vs_cash"] == 0.02
    assert first["stress"]["ok"]


def test_ingest_packet_records_repo_trust_details(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    packet["adapters"]["monte-carlo"].update(
        {
            "repo_branch": "main",
            "repo_dirty": True,
            "repo_status": [" M decision.py", "?? scratch.ipynb"],
            "repo_status_count": 2,
            "repo_status_truncated": False,
        }
    )
    packet["content_digest"] = packet_digest(packet)

    entry = ingest_packet(packet, ledger_dir=tmp_path / "ledger")

    assert entry["dirty_repos"] == ["monte-carlo"]
    assert entry["repo_trust"]["dirty_count"] == 1
    assert entry["repo_trust"]["dirty_details"][0]["repo_branch"] == "main"
    assert entry["repo_trust"]["dirty_details"][0]["repo_status"] == [
        " M decision.py",
        "?? scratch.ipynb",
    ]


def test_ingest_rejects_run_id_digest_collision(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    ingest_packet(packet, ledger_dir=tmp_path / "ledger")
    changed = dict(packet)
    changed["inputs"] = {"tickers": ["MSFT"]}
    changed["content_digest"] = packet_digest(changed)

    with pytest.raises(ValueError, match="run id collision"):
        ingest_packet(changed, ledger_dir=tmp_path / "ledger")
