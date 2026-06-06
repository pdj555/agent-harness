from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from agent_harness.adapters import AdapterStatus, EngineRun
from agent_harness.capital import build_capital_loops
from agent_harness.cli import main
from agent_harness.ledger import ingest_outcome, ingest_packet
from agent_harness.outcomes import backfill_ledger_outcomes, evaluate_outcome, write_outcome
from agent_harness.packets import build_run_packet, packet_digest


def _write_prices(price_dir: Path) -> None:
    price_dir.mkdir(parents=True, exist_ok=True)
    (price_dir / "AAPL.csv").write_text(
        "Date,Close\n2024-01-02,100\n2024-01-03,110\n2024-01-04,121\n",
        encoding="utf-8",
    )
    (price_dir / "MSFT.csv").write_text(
        "Date,Close\n2024-01-02,100\n2024-01-03,90\n2024-01-04,81\n",
        encoding="utf-8",
    )


def _packet(tmp_path: Path) -> dict:
    return {
        "schema_version": "agent-harness.run.v1",
        "run_id": "run_outcome",
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
                            "expected_return": 0.25,
                        },
                    },
                    "allocations": {
                        "AAPL": {"weight": 0.6},
                    },
                },
            }
        },
    }


def _packet_with_sentiment(tmp_path: Path) -> dict:
    packet = _packet(tmp_path)
    packet["engine_runs"]["stock_sentiment"] = {
        "name": "stock-sentiment-analysis",
        "ok": True,
        "summary": "AAPL sentiment score=0.5",
        "payload": {
            "ticker": "AAPL",
            "score": 0.5,
            "label": "positive",
            "confidence": 0.8,
            "signal": "buy",
            "articles_analyzed": 6,
            "source": "google-rss",
            "source_label": "Google News RSS",
            "classification_degraded": False,
            "classification_warnings": [],
        },
        "diagnostics": [],
        "command": ["stock-sentiment", "analyze", "AAPL"],
        "duration_ms": 10,
        "repo_sha": "sentiment-sha",
        "repo_dirty": False,
    }
    return packet


def _ledger_packet(tmp_path: Path, *, run_id: str = "run_backfill") -> dict:
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
            repo_path=tmp_path / "stock-sentiment-analysis",
            reason="not keyed",
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
            "allocations": {"AAPL": {"weight": 0.6}},
            "rankings": {"AAPL": {"max_drawdown_q95": 0.03}},
            "errors": [],
        },
        repo_sha="abc",
        repo_dirty=False,
    )
    loops = build_capital_loops(statuses, monte_carlo_run=run)
    packet = build_run_packet(
        namespace_root=tmp_path,
        invocation=["agent-harness", "thesis", "AAPL"],
        inputs={"tickers": ["AAPL", "MSFT"], "backtest": {"hold": 1}},
        statuses=statuses,
        monte_carlo_run=run,
        ranked_loops=loops,
    )
    packet["run_id"] = run_id
    packet["content_digest"] = packet_digest(packet)
    return packet


def test_evaluate_outcome_scores_allocation_against_benchmarks(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)

    outcome = evaluate_outcome(_packet(tmp_path), price_dir=price_dir)

    assert outcome["schema_version"] == "agent-harness.outcome.v1"
    assert len(outcome["outcome_digest"]) == 64
    assert outcome["window"]["start_date"] == "2024-01-02"
    assert outcome["window"]["end_date"] == "2024-01-04"
    assert round(outcome["returns"]["by_ticker"]["AAPL"], 6) == 0.21
    assert round(outcome["returns"]["allocation"], 6) == 0.126
    assert round(outcome["returns"]["equal_weight"], 6) == 0.01
    assert outcome["returns"]["excess_vs_cash"] == outcome["returns"]["allocation"]
    assert outcome["scorecard"]["ok"]
    assert outcome["risk"]["realized_max_drawdown"] == 0.0
    assert (
        outcome["sources"]["prices"]["AAPL"]["sha256"]
        == hashlib.sha256((price_dir / "AAPL.csv").read_bytes()).hexdigest()
    )
    assert len(outcome["sources"]["price_source_digest"]) == 64
    attribution = outcome["attribution"]
    positions = {row["ticker"]: row for row in attribution["positions"]}
    assert round(positions["AAPL"]["allocation_contribution"], 6) == 0.126
    assert round(positions["MSFT"]["active_contribution"], 6) == 0.095
    assert round(attribution["cash"]["drag_vs_equal_weight"], 6) == -0.004
    assert round(
        attribution["active_excess"]["from_positions"]
        + attribution["active_excess"]["from_cash"],
        6,
    ) == round(outcome["returns"]["excess_vs_equal_weight"], 6)
    assert attribution["drivers"]["top_active_contributor"]["ticker"] == "MSFT"
    assert attribution["drivers"]["largest_active_drag"] is None
    assert attribution["drivers"]["weakest_active_contributor"]["ticker"] == "AAPL"


def test_evaluate_outcome_waives_repaired_de_minimis_primary_miss(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    price_dir.mkdir()
    (price_dir / "AAPL.csv").write_text(
        "Date,Close\n2024-01-02,100\n2024-01-03,90\n2024-01-04,80\n",
        encoding="utf-8",
    )
    (price_dir / "MSFT.csv").write_text(
        "Date,Close\n2024-01-02,100\n2024-01-03,120\n2024-01-04,140\n",
        encoding="utf-8",
    )
    packet = _packet(tmp_path)
    packet["engine_runs"]["monte_carlo"]["payload"]["action_plan"]["cash_weight"] = 0.0
    packet["engine_runs"]["monte_carlo"]["payload"]["action_plan"]["primary_pick"]["weight"] = 0.05
    packet["engine_runs"]["monte_carlo"]["payload"]["allocations"] = {
        "AAPL": {"weight": 0.05},
        "MSFT": {"weight": 0.95},
    }
    packet["engine_runs"]["monte_carlo"]["payload"]["allocation_repair"] = {
        "applied": True,
    }

    outcome = evaluate_outcome(packet, price_dir=price_dir)

    assert not outcome["scorecard"]["primary_hit"]
    assert not outcome["scorecard"]["primary_hit_required"]
    assert outcome["scorecard"]["primary_hit_waived_reason"] == (
        "allocation_repair_de_minimis_primary_weight"
    )
    assert outcome["scorecard"]["ok"]
    assert outcome["returns"]["allocation"] > outcome["returns"]["equal_weight"]


def test_evaluate_outcome_scores_sentiment_alignment(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)

    outcome = evaluate_outcome(_packet_with_sentiment(tmp_path), price_dir=price_dir)

    sentiment = outcome["sentiment"]
    assert sentiment["present"]
    assert sentiment["ok"]
    assert sentiment["ticker_matches_primary"]
    assert sentiment["directional_hit"]
    assert round(sentiment["signed_realized_return"], 6) == 0.21
    assert round(sentiment["score_return_alignment"], 6) == 0.105
    assert round(sentiment["confidence_weighted_alignment"], 6) == 0.084


def test_evaluate_outcome_reports_negative_active_drag(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    packet = _packet(tmp_path)
    payload = packet["engine_runs"]["monte_carlo"]["payload"]
    payload["action_plan"]["primary_pick"] = {
        "ticker": "MSFT",
        "weight": 0.6,
        "expected_return": 0.05,
    }
    payload["allocations"] = {"MSFT": {"weight": 0.6}}

    outcome = evaluate_outcome(packet, price_dir=price_dir)

    drivers = outcome["attribution"]["drivers"]
    assert drivers["top_active_contributor"] is None
    assert drivers["largest_active_drag"]["ticker"] == "AAPL"
    assert round(drivers["largest_active_drag"]["contribution"], 6) == -0.105


def test_evaluate_outcome_rejects_unbalanced_portfolio(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    packet = _packet(tmp_path)
    packet["engine_runs"]["monte_carlo"]["payload"]["action_plan"]["cash_weight"] = 0.5

    with pytest.raises(ValueError, match="must equal 1.0"):
        evaluate_outcome(packet, price_dir=price_dir)


def test_evaluate_outcome_rejects_duplicate_price_dates(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    (price_dir / "AAPL.csv").write_text(
        "Date,Close\n2024-01-02,100\n2024-01-02,101\n2024-01-04,121\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate price date for AAPL"):
        evaluate_outcome(_packet(tmp_path), price_dir=price_dir)


def test_write_and_ingest_outcome(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    outcome = evaluate_outcome(_packet(tmp_path), price_dir=price_dir)

    path = write_outcome(outcome, tmp_path / "outcomes")
    entry = ingest_outcome(outcome, outcome_path=path, ledger_dir=tmp_path / "ledger")

    assert path.exists()
    assert path.name.endswith(f"{outcome['outcome_digest'][:12]}.json")
    assert (tmp_path / "outcomes" / "latest.json").exists()
    assert entry["entry_type"] == "outcome"
    assert entry["scorecard"]["ok"]
    assert entry["sources"]["price_source_digest"] == outcome["sources"]["price_source_digest"]
    assert not entry["sentiment"]["present"]
    assert entry["attribution"]["drivers"]["top_active_contributor"]["ticker"] == "MSFT"
    assert entry["attribution"]["drivers"]["weakest_active_contributor"]["ticker"] == "AAPL"
    assert entry["attribution"]["drivers"]["largest_active_drag"] is None
    assert (tmp_path / "ledger" / "latest_outcome.json").exists()


def test_ingest_outcome_records_sentiment_alignment(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    outcome = evaluate_outcome(_packet_with_sentiment(tmp_path), price_dir=price_dir)

    entry = ingest_outcome(outcome, ledger_dir=tmp_path / "ledger")

    assert entry["sentiment"]["present"]
    assert entry["sentiment"]["directional_hit"]
    assert round(entry["sentiment"]["confidence_weighted_alignment"], 6) == 0.084


def test_ingest_outcome_is_idempotent_for_same_window(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    outcome = evaluate_outcome(_packet(tmp_path), price_dir=price_dir)
    same_outcome = dict(outcome)
    same_outcome["evaluated_at"] = "2099-01-01T00:00:00+00:00"

    first = ingest_outcome(outcome, ledger_dir=tmp_path / "ledger")
    second = ingest_outcome(same_outcome, ledger_dir=tmp_path / "ledger")

    assert first == second
    assert len((tmp_path / "ledger" / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_ingest_outcome_allows_distinct_measurements_for_same_window(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    first_outcome = evaluate_outcome(_packet(tmp_path), price_dir=price_dir)
    second_outcome = evaluate_outcome(_packet(tmp_path), price_dir=price_dir, cash_return=0.01)

    first = ingest_outcome(first_outcome, ledger_dir=tmp_path / "ledger")
    second = ingest_outcome(second_outcome, ledger_dir=tmp_path / "ledger")

    assert first["outcome_digest"] != second["outcome_digest"]
    assert len((tmp_path / "ledger" / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_ingest_outcome_accepts_existing_timestamp_scoped_digest(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    outcome = evaluate_outcome(_packet(tmp_path), price_dir=price_dir)
    old_scoped = dict(outcome)
    old_scoped.pop("outcome_digest", None)
    old_digest = hashlib.sha256(
        json.dumps(old_scoped, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    old_outcome = dict(outcome)
    old_outcome["outcome_digest"] = old_digest

    first = ingest_outcome(old_outcome, ledger_dir=tmp_path / "ledger")
    rerun = dict(outcome)
    rerun["evaluated_at"] = "2099-01-01T00:00:00+00:00"
    second = ingest_outcome(rerun, ledger_dir=tmp_path / "ledger")

    assert first == second


def test_backfill_ledger_outcomes_creates_and_skips_rolling_windows(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    ledger_dir = tmp_path / "ledger"
    packet = _ledger_packet(tmp_path)
    ingest_packet(packet, ledger_dir=ledger_dir)

    first = backfill_ledger_outcomes(
        ledger_dir=ledger_dir,
        price_dir=price_dir,
        output_dir=tmp_path / "outcomes",
        horizon_rows=1,
        rolling=True,
    )
    second = backfill_ledger_outcomes(
        ledger_dir=ledger_dir,
        price_dir=price_dir,
        output_dir=tmp_path / "outcomes",
        horizon_rows=1,
        rolling=True,
    )

    assert first["run_count"] == 1
    assert first["evaluated"] == 2
    assert first["created"] == 2
    assert first["failed"] == 0
    assert {row["status"] for row in first["rows"]} == {"created"}
    assert second["created"] == 0
    assert second["skipped_existing"] == 2
    assert {row["status"] for row in second["rows"]} == {"skipped_existing"}


def test_backfill_outcomes_cli_reports_created_rows(tmp_path: Path, capsys) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    ledger_dir = tmp_path / "ledger"
    ingest_packet(_ledger_packet(tmp_path), ledger_dir=ledger_dir)

    exit_code = main(
        [
            "ledger",
            "--ledger-dir",
            str(ledger_dir),
            "backfill-outcomes",
            "--price-dir",
            str(price_dir),
            "--output-dir",
            str(tmp_path / "outcomes"),
            "--horizon-rows",
            "1",
            "--rolling",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Outcome backfill: write rolling" in captured.out
    assert "created=2" in captured.out
    assert "failed=0" in captured.out
    assert main(["ledger", "--ledger-dir", str(ledger_dir), "outcomes", "--min-outcomes", "2"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "ledger",
                "--ledger-dir",
                str(ledger_dir),
                "calibrate-outcomes",
                "--min-sample",
                "2",
            ]
        )
        == 0
    )
    calibrated = capsys.readouterr()
    assert "Outcome calibration: READY" in calibrated.out
    assert "Ledger report flags:" in calibrated.out


def test_calibrate_outcomes_cli_writes_ready_gates(tmp_path: Path, capsys) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    ledger_dir = tmp_path / "ledger"
    gates_path = tmp_path / "generated-gates.json"
    ingest_packet(_ledger_packet(tmp_path), ledger_dir=ledger_dir)
    assert (
        main(
            [
                "ledger",
                "--ledger-dir",
                str(ledger_dir),
                "backfill-outcomes",
                "--price-dir",
                str(price_dir),
                "--output-dir",
                str(tmp_path / "outcomes"),
                "--horizon-rows",
                "1",
                "--rolling",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "ledger",
                "--ledger-dir",
                str(ledger_dir),
                "calibrate-outcomes",
                "--min-sample",
                "2",
                "--write-gates",
                "--gates-output",
                str(gates_path),
            ]
        )
        == 0
    )
    calibrated = capsys.readouterr()
    assert "Wrote gates:" in calibrated.out
    assert gates_path.exists()

    assert (
        main(
            [
                "ledger",
                "--gates",
                str(gates_path),
                "--ledger-dir",
                str(ledger_dir),
                "outcomes",
            ]
        )
        == 0
    )


def test_outcome_cli_writes_artifact_and_ledger_entry(tmp_path: Path, capsys) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(_packet(tmp_path)), encoding="utf-8")

    exit_code = main(
        [
            "outcome",
            str(packet_path),
            "--price-dir",
            str(price_dir),
            "--output-dir",
            str(tmp_path / "outcomes"),
            "--ledger-dir",
            str(tmp_path / "ledger"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Outcome: PASS" in captured.out
    assert "Attribution:" in captured.out
    assert "cash_drag=" in captured.out
    assert "largest_active_drag=None" in captured.out
    assert (tmp_path / "outcomes" / "latest.json").exists()
    assert (tmp_path / "ledger" / "latest_outcome.json").exists()

    assert main(["ledger", "--ledger-dir", str(tmp_path / "ledger"), "outcomes"]) == 2
    gated = capsys.readouterr()
    assert "Promotion gates: loaded=True" in gated.out
    assert "needs at least 20 realized outcomes" in gated.out

    assert main(["ledger", "--no-gates", "--ledger-dir", str(tmp_path / "ledger"), "outcomes"]) == 0
    ungated = capsys.readouterr()
    assert "Promotion gates: loaded=False" in ungated.out

    assert main(["ledger", "--ledger-dir", str(tmp_path / "ledger"), "outcomes", "--min-outcomes", "1"]) == 0
    reported = capsys.readouterr()
    assert "Outcome report: READY" in reported.out
    assert "ok_rate=1.00" in reported.out

    assert (
        main(
            [
                "ledger",
                "--no-gates",
                "--ledger-dir",
                str(tmp_path / "ledger"),
                "outcomes",
                "--min-outcomes",
                "1",
                "--max-forecast-error",
                "0.01",
            ]
        )
        == 2
    )
    blocked = capsys.readouterr()
    assert "Outcome report: NOT READY" in blocked.out
    assert "average absolute forecast error above 0.01" in blocked.out


def test_outcome_cli_reports_sentiment_alignment(tmp_path: Path, capsys) -> None:
    price_dir = tmp_path / "prices"
    _write_prices(price_dir)
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(_packet_with_sentiment(tmp_path)), encoding="utf-8")

    exit_code = main(
        [
            "outcome",
            str(packet_path),
            "--price-dir",
            str(price_dir),
            "--output-dir",
            str(tmp_path / "outcomes"),
            "--ledger-dir",
            str(tmp_path / "ledger"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Sentiment outcome:" in captured.out
    assert "directional_hit=True" in captured.out
    assert "weighted_alignment=0.084" in captured.out
    assert (
        main(
            [
                "ledger",
                "--no-gates",
                "--ledger-dir",
                str(tmp_path / "ledger"),
                "outcomes",
                "--min-sentiment-outcomes",
                "1",
                "--min-sentiment-hit-rate",
                "0.8",
                "--min-sentiment-alignment",
                "0.01",
            ]
        )
        == 0
    )
    reported = capsys.readouterr()
    assert "Sentiment outcomes: present=1 directional=1 hit_rate=1.00" in reported.out
