from __future__ import annotations

import json
from pathlib import Path

from agent_harness.cli import main
from agent_harness.ledger import ingest_regime_replay, read_regime_entries
from agent_harness.regimes import (
    evaluate_packet_regimes,
    write_regime_replay,
)


def _packet(tmp_path: Path) -> dict:
    return {
        "schema_version": "agent-harness.run.v1",
        "run_id": "run_regime",
        "content_digest": "digest",
        "namespace_root": str(tmp_path),
        "inputs": {
            "tickers": ["AAPL", "MSFT"],
            "backtest": {"hold": 2},
        },
        "engine_runs": {
            "monte_carlo": {
                "ok": True,
                "payload": {
                    "action_plan": {
                        "cash_weight": 0.4,
                        "primary_pick": {
                            "ticker": "AAPL",
                            "weight": 0.6,
                            "expected_return": 0.18,
                        },
                    },
                    "allocations": {
                        "AAPL": {"weight": 0.6},
                    },
                },
            }
        },
    }


def _by_name(report: dict) -> dict[str, dict]:
    return {regime["name"]: regime for regime in report["regimes"]}


def test_evaluate_packet_regimes_writes_synthetic_prices_and_flags_fragility(tmp_path: Path) -> None:
    report = evaluate_packet_regimes(_packet(tmp_path), output_dir=tmp_path / "regimes")
    regimes = _by_name(report)

    assert report["schema_version"] == "agent-harness.regime-replay.v1"
    assert len(report["report_digest"]) == 64
    assert report["primary_ticker"] == "AAPL"
    assert set(regimes) == {
        "primary_trend",
        "primary_reversal",
        "shock_recovery",
        "cash_drag_rally",
    }
    assert (tmp_path / "regimes" / "run_regime" / "prices" / "primary_reversal" / "AAPL.csv").exists()

    assert regimes["primary_trend"]["fragility"]["ok"]
    assert round(regimes["primary_trend"]["returns"]["allocation"], 6) == 0.15
    assert round(regimes["primary_trend"]["returns"]["excess_vs_equal_weight"], 6) == 0.005

    reversal = regimes["primary_reversal"]
    assert not reversal["fragility"]["ok"]
    assert "portfolio lost more than reversal budget" in reversal["fragility"]["reasons"]
    assert "underperformed equal weight during primary reversal" in reversal["fragility"]["reasons"]
    assert round(reversal["primary_pick"]["realized_return"], 6) == -0.1

    shock = regimes["shock_recovery"]
    assert shock["scorecard"]["ok"]
    assert not shock["fragility"]["ok"]
    assert shock["risk"]["realized_max_drawdown"] > 0.08

    cash_drag = regimes["cash_drag_rally"]
    assert not cash_drag["fragility"]["ok"]
    assert cash_drag["fragility"]["reasons"] == [
        "failed to keep pace with broad risky-asset rally"
    ]
    assert round(cash_drag["attribution"]["cash"]["drag_vs_equal_weight"], 6) == -0.048

    summary = report["summary"]
    assert not summary["ok"]
    assert summary["regime_count"] == 4
    assert summary["scorecard_pass_count"] == 2
    assert summary["fragile_count"] == 3
    assert summary["fragile_regimes"] == [
        "primary_reversal",
        "shock_recovery",
        "cash_drag_rally",
    ]
    assert round(summary["primary_reversal_loss"], 6) == -0.1
    assert summary["worst_drawdown"] > 0.08


def test_write_regime_replay_persists_latest_artifact(tmp_path: Path) -> None:
    report = evaluate_packet_regimes(_packet(tmp_path), output_dir=tmp_path / "regimes")

    path = write_regime_replay(report, tmp_path / "regimes")
    saved = json.loads(path.read_text(encoding="utf-8"))
    latest = json.loads((tmp_path / "regimes" / "latest.json").read_text(encoding="utf-8"))

    assert path.name.startswith("run_regime_regime_replay_")
    assert saved["report_digest"] == report["report_digest"]
    assert latest == saved


def test_ingest_regime_replay_is_idempotent(tmp_path: Path) -> None:
    report = evaluate_packet_regimes(_packet(tmp_path), output_dir=tmp_path / "regimes")
    path = write_regime_replay(report, tmp_path / "regimes")

    first = ingest_regime_replay(report, regime_path=path, ledger_dir=tmp_path / "ledger")
    second = ingest_regime_replay(report, regime_path=path, ledger_dir=tmp_path / "ledger")

    assert first == second
    assert first["entry_type"] == "regime_replay"
    assert first["summary"]["fragile_count"] == 3
    assert first["regimes"][0]["name"] == "primary_trend"
    assert len(read_regime_entries(tmp_path / "ledger")) == 1
    assert (tmp_path / "ledger" / "latest_regime.json").exists()
    assert (tmp_path / "ledger" / "regimes" / f"run_regime_regime_replay_{report['report_digest'][:12]}.json").exists()


def test_regime_replay_cli_writes_artifact_and_returns_nonzero_on_fragility(
    tmp_path: Path,
    capsys,
) -> None:
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(_packet(tmp_path)), encoding="utf-8")

    exit_code = main(
        [
            "regime-replay",
            str(packet_path),
            "--output-dir",
            str(tmp_path / "regimes"),
            "--ledger-dir",
            str(tmp_path / "ledger"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Regime replay: FAIL" in captured.out
    assert "primary_reversal" in captured.out
    assert "cash_drag_rally" in captured.out
    assert "Saved regime replay:" in captured.out
    assert "Ledger regime replay:" in captured.out
    assert (tmp_path / "regimes" / "latest.json").exists()
    assert (tmp_path / "ledger" / "latest_regime.json").exists()
