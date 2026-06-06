"""Build a deterministic production-verifier smoke fixture for CI.

The fixture is synthetic. It proves the verifier, gates, ledger, and platform
export/import paths execute together from a clean checkout; it is not market
evidence for a live promotion.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_harness.promotion_gates import (
    DEFAULT_REGIME_PROMOTION_GATES,
    PROMOTION_GATES_SCHEMA_VERSION,
)


DATES = [
    "2024-01-02",
    "2024-01-03",
    "2024-01-04",
    "2024-01-05",
    "2024-01-08",
    "2024-01-09",
    "2024-01-10",
    "2024-01-11",
    "2024-01-12",
    "2024-01-15",
]

PRICES = {
    "AAPL": [100.0, 101.0, 102.0, 101.5, 103.0, 104.0, 104.5, 105.0, 106.0, 107.0],
    "MSFT": [200.0, 199.0, 201.0, 202.0, 203.0, 202.0, 204.0, 205.0, 206.0, 207.0],
    "GOOGL": [150.0, 151.0, 150.0, 152.0, 153.0, 154.0, 153.0, 155.0, 156.0, 158.0],
    "JPM": [120.0, 119.5, 120.5, 121.0, 121.5, 122.5, 123.0, 122.8, 123.5, 124.0],
    "XOM": [80.0, 81.0, 80.5, 82.0, 81.8, 83.0, 84.0, 83.5, 84.5, 85.0],
}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _clean_repo_trust() -> dict[str, Any]:
    adapters = [
        {
            "name": "agent-harness-ledger",
            "repo_branch": "ci",
            "repo_dirty": False,
            "repo_path": None,
            "repo_sha": "ci-agent-harness",
            "repo_status": [],
            "repo_status_count": 0,
            "repo_status_truncated": False,
        },
        {
            "name": "monte-carlo",
            "repo_branch": "ci",
            "repo_dirty": False,
            "repo_path": None,
            "repo_sha": "ci-monte-carlo",
            "repo_status": [],
            "repo_status_count": 0,
            "repo_status_truncated": False,
        },
        {
            "name": "stock-sentiment-analysis",
            "repo_branch": "ci",
            "repo_dirty": False,
            "repo_path": None,
            "repo_sha": "ci-stock-sentiment",
            "repo_status": [],
            "repo_status_count": 0,
            "repo_status_truncated": False,
        },
    ]
    return {
        "adapter_count": len(adapters),
        "dirty_count": 0,
        "dirty_details": [],
        "adapters": adapters,
    }


def _run_entry(root: Path, run_id: str, index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    content_digest = f"ci_content_{index}"
    packet = {
        "schema_version": "agent-harness.run.v1",
        "run_id": run_id,
        "created_at": f"2026-06-05T00:0{index}:00+00:00",
        "content_digest": content_digest,
        "inputs": {"tickers": list(PRICES)},
        "ranked_loops": [{"repo": "monte-carlo", "name": "ci-smoke", "score": 1.0}],
    }
    packet_path = root / "ledger" / "packets" / f"{run_id}.json"
    _write_json(packet_path, packet)
    primary = ["AAPL", "MSFT", "GOOGL"][index - 1]
    entry = {
        "ledger_schema_version": "agent-harness.ledger.v1",
        "run_id": run_id,
        "created_at": packet["created_at"],
        "content_digest": content_digest,
        "packet_schema_version": packet["schema_version"],
        "packet_copy_path": str(packet_path.resolve()),
        "namespace_root": str(root.resolve()),
        "monte_carlo_ok": True,
        "monte_carlo_backtest_ok": True,
        "stock_sentiment_ok": False,
        "eval_ok": True,
        "eval_score": 1.0,
        "dirty_repos": [],
        "repo_trust": _clean_repo_trust(),
        "top_loop": {"name": "ci-smoke", "repo": "monte-carlo", "score": 1.0},
        "primary_pick": {
            "ticker": primary,
            "weight": 0.25,
            "expected_return": 0.03,
            "prob_above_current": 0.7,
            "value_at_risk_95_pct": 0.02,
        },
        "backtest": {
            "strategy_total_return": 0.03 + index / 1000.0,
            "strategy_max_drawdown": 0.005,
            "strategy_win_rate": 1.0,
            "excess_return_vs_equal_weight": 0.01,
            "excess_return_vs_cash": 0.02,
            "periods": 4,
        },
        "stress": {"ok": True, "worst_margin": 0.02, "scenario_count": 3},
        "sentiment": {},
    }
    return entry, packet


def _outcome(root: Path, run: dict[str, Any], index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    digest = f"ci_outcome_{index}"
    outcome = {
        "schema_version": "agent-harness.outcome.v1",
        "run_id": run["run_id"],
        "content_digest": run["content_digest"],
        "outcome_digest": digest,
        "window": {
            "start_date": "2024-01-02",
            "end_date": "2024-01-05",
            "horizon_rows": 3,
        },
        "scorecard": {
            "ok": True,
            "beat_cash": True,
            "beat_equal_weight": True,
            "primary_hit": True,
        },
        "returns": {
            "allocation": 0.012 + index / 1000.0,
            "equal_weight": 0.008,
            "cash": 0.0,
            "excess_vs_cash": 0.012 + index / 1000.0,
            "excess_vs_equal_weight": 0.004 + index / 1000.0,
        },
        "primary_pick": {
            "ticker": run["primary_pick"]["ticker"],
            "expected_return": 0.03,
            "realized_return": 0.025,
            "forecast_error": -0.005,
            "hit": True,
        },
        "risk": {"realized_max_drawdown": 0.004},
        "attribution": {
            "active_excess": {
                "vs_equal_weight": 0.004 + index / 1000.0,
                "from_positions": 0.004 + index / 1000.0,
                "from_cash": 0.0,
            },
            "cash": {"drag_vs_equal_weight": 0.0},
            "drivers": {
                "top_active_contributor": run["primary_pick"]["ticker"],
                "weakest_active_contributor": None,
                "largest_active_drag": None,
            },
        },
        "sentiment": {"present": False},
        "sources": {"price_source_digest": "ci_prices"},
    }
    path = root / "ledger" / "outcomes" / f"{digest}.json"
    _write_json(path, outcome)
    entry = dict(outcome)
    entry.update(
        {
            "ledger_schema_version": "agent-harness.ledger.v1",
            "entry_type": "outcome",
            "outcome_copy_path": str(path.resolve()),
        }
    )
    return entry, outcome


def _regime(root: Path, run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    digest = "ci_regime_latest"
    report = {
        "schema_version": "agent-harness.regime-replay.v1",
        "run_id": run["run_id"],
        "content_digest": run["content_digest"],
        "report_digest": digest,
        "primary_ticker": run["primary_pick"]["ticker"],
        "parameters": {
            "rows": 5,
            "start_date": "2024-01-02",
            "cash_return": 0.0,
            "max_drawdown": 0.08,
            "regime_names": ["primary_trend", "primary_reversal"],
        },
        "summary": {
            "ok": True,
            "regime_count": 2,
            "scorecard_pass_count": 2,
            "fragile_count": 0,
            "fragile_regimes": [],
            "worst_excess_vs_cash": 0.012,
            "worst_excess_vs_equal_weight": 0.004,
            "worst_drawdown": 0.03,
            "max_drawdown": 0.08,
            "primary_reversal_loss": -0.04,
        },
        "regimes": [
            {
                "name": "primary_trend",
                "fragility_ok": True,
                "fragility_reasons": [],
                "excess_vs_cash": 0.02,
                "excess_vs_equal_weight": 0.01,
                "realized_max_drawdown": 0.01,
            },
            {
                "name": "primary_reversal",
                "fragility_ok": True,
                "fragility_reasons": [],
                "excess_vs_cash": 0.012,
                "excess_vs_equal_weight": 0.004,
                "realized_max_drawdown": 0.03,
            },
        ],
    }
    path = root / "ledger" / "regimes" / f"{digest}.json"
    _write_json(path, report)
    entry = {
        "ledger_schema_version": "agent-harness.ledger.v1",
        "entry_type": "regime_replay",
        "run_id": run["run_id"],
        "content_digest": run["content_digest"],
        "report_digest": digest,
        "regime_copy_path": str(path.resolve()),
        "primary_ticker": report["primary_ticker"],
        "parameters": report["parameters"],
        "summary": report["summary"],
        "regimes": report["regimes"],
    }
    return entry, report


def _promotion_attempts(root: Path, run: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = [
        {
            "schema_version": "agent-harness.promotion.v1",
            "promotion_id": "promotion_ci_blocked",
            "created_at": "2026-06-05T00:04:00+00:00",
            "run_id": run["run_id"],
            "content_digest": run["content_digest"],
            "status": "blocked",
            "blockers": [
                "latest backtest did not beat cash",
                "walk-forward backtest failed threshold",
            ],
        },
        {
            "schema_version": "agent-harness.promotion.v1",
            "promotion_id": "promotion_ci_promoted",
            "created_at": "2026-06-05T00:05:00+00:00",
            "run_id": run["run_id"],
            "content_digest": run["content_digest"],
            "status": "promoted",
            "blockers": [],
        },
    ]
    attempts_dir = root / "promotions" / "attempts"
    for attempt in attempts:
        _write_json(attempts_dir / f"{attempt['promotion_id']}.json", attempt)
    _write_json(root / "promotions" / "latest.json", attempts[-1])
    _write_json(
        root / "promotions" / "canonical.json",
        {
            "schema_version": "agent-harness.canonical-promotion.v1",
            "promotion_id": attempts[-1]["promotion_id"],
            "run_id": run["run_id"],
            "content_digest": run["content_digest"],
            "source": "synthetic_ci_fixture",
        },
    )
    return attempts


def build_fixture(root: Path) -> dict[str, str]:
    root = root.expanduser().resolve()
    for ticker, prices in PRICES.items():
        path = root / "prices" / f"{ticker}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = ["Date,Close", *(f"{date},{price:g}" for date, price in zip(DATES, prices))]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    runs = [_run_entry(root, f"run_ci_{index}", index)[0] for index in range(1, 4)]
    outcomes = [_outcome(root, run, index)[0] for index, run in enumerate(runs[1:], start=1)]
    regime_entry, _ = _regime(root, runs[-1])
    regimes = [regime_entry]
    _promotion_attempts(root, runs[-1])

    ledger = root / "ledger"
    _write_jsonl(ledger / "runs.jsonl", runs)
    _write_jsonl(ledger / "outcomes.jsonl", outcomes)
    _write_jsonl(ledger / "regimes.jsonl", regimes)
    _write_json(ledger / "latest.json", runs[-1])
    _write_json(ledger / "latest_outcome.json", outcomes[-1])
    _write_json(ledger / "latest_regime.json", regimes[-1])

    gates = {
        "schema_version": PROMOTION_GATES_SCHEMA_VERSION,
        "min_runs": 3,
        "outcomes": {
            "min_outcomes": 2,
            "min_ok_rate": 1.0,
            "min_avg_excess_cash": 0.0,
            "min_avg_excess_equal_weight": 0.0,
            "max_avg_abs_forecast_error": 0.01,
            "max_realized_drawdown": 0.01,
            "min_sentiment_directional_count": 0,
        },
        "regimes": dict(DEFAULT_REGIME_PROMOTION_GATES),
        "source": {
            "method": "tools/build_ci_production_fixture.py",
            "synthetic_ci_smoke": True,
        },
    }
    _write_json(root / "gates.json", gates)
    paths = {
        "root": str(root),
        "ledger_dir": str(ledger),
        "price_dir": str(root / "prices"),
        "gates": str(root / "gates.json"),
        "platform_output_dir": str(root / "platform_exports"),
        "promotions_dir": str(root / "promotions"),
    }
    _write_json(root / "paths.json", paths)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("AGENT_HARNESS_CI_FIXTURE", ".ci-production")),
        help="Directory to write the synthetic verifier fixture.",
    )
    args = parser.parse_args(argv)
    print(json.dumps(build_fixture(args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
