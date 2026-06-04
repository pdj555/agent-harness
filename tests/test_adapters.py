from __future__ import annotations

from pathlib import Path

from agent_harness.adapters import MonteCarloAdapter


def test_monte_carlo_status_reports_missing_repo(tmp_path: Path) -> None:
    status = MonteCarloAdapter(tmp_path / "missing").status()

    assert not status.available
    assert status.reason == "repository not found"


def test_monte_carlo_adapter_executes_public_cli_contract(tmp_path: Path) -> None:
    repo = tmp_path / "monte-carlo"
    repo.mkdir()
    for filename in ("simulate_cli.py", "decision.py"):
        (repo / filename).write_text("# marker\n", encoding="utf-8")
    (repo / "public_cli.py").write_text(
        """
def parse_public_args(argv):
    return list(argv)

def execute_public_simulate(args):
    return {
        "report": {
            "action_plan": {"headline": "Lean in", "primary_pick": {"ticker": "AAPL"}},
            "rankings": {"AAPL": {"score": 1.0}},
            "allocations": {"AAPL": {"weight": 0.6}},
            "errors": [],
        }
    }

def format_public_simulation_output(result, details, output):
    return result["report"]["action_plan"]["headline"]
""",
        encoding="utf-8",
    )

    run = MonteCarloAdapter(repo).run_offline_simulation(("AAPL",), days=1, scenarios=1)

    assert run.ok
    assert run.summary == "Lean in"
    assert run.payload["action_plan"]["primary_pick"]["ticker"] == "AAPL"


def test_monte_carlo_adapter_executes_backtest_contract(tmp_path: Path) -> None:
    repo = tmp_path / "monte-carlo"
    repo.mkdir()
    for filename in ("simulate_cli.py", "decision.py"):
        (repo / filename).write_text("# marker\n", encoding="utf-8")
    (repo / "public_cli.py").write_text(
        """
class Summary:
    def to_dict(self):
        return {
            "strategy_total_return": 0.1,
            "excess_return_vs_cash": 0.05,
        }

def parse_public_args(argv):
    return list(argv)

def execute_public_backtest(args):
    return {"summary": Summary(), "price_sources": {"AAPL": {"kind": "fixture"}}}

def format_public_backtest_output(result, details, output):
    return "Strategy return: 10.0%"
""",
        encoding="utf-8",
    )

    run = MonteCarloAdapter(repo).run_offline_backtest(("AAPL",), lookback=3, hold=2)

    assert run.ok
    assert run.summary == "Strategy return: 10.0%"
    assert run.payload["summary"]["excess_return_vs_cash"] == 0.05
