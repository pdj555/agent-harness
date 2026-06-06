from __future__ import annotations

import json
from pathlib import Path

from agent_harness.cli import DEFAULT_THESIS_TICKERS, main
from agent_harness.platform_sync import PLATFORM_SIGNING_KEY_ENV
from tools.build_ci_production_fixture import build_fixture


def test_scan_json_outputs_known_repos(tmp_path: Path, capsys) -> None:
    exit_code = main(["--namespace-root", str(tmp_path), "scan", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "monte-carlo" in captured.out
    assert "stock-sentiment-analysis" in captured.out


def test_thesis_no_run_works_without_sibling_repos(tmp_path: Path, capsys) -> None:
    exit_code = main(["--namespace-root", str(tmp_path), "thesis", "--no-run", "--no-save"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Ranked implementation loops" in captured.out


def test_thesis_saves_replayable_packet(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "runs"
    ledger_dir = tmp_path / "ledger"

    exit_code = main(
        [
            "--namespace-root",
            str(tmp_path),
            "thesis",
            "--no-run",
            "--output-dir",
            str(run_dir),
            "--ledger-dir",
            str(ledger_dir),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Saved run packet:" in captured.out
    assert (run_dir / "latest.json").exists()
    assert (ledger_dir / "latest.json").exists()
    packet = json.loads((run_dir / "latest.json").read_text(encoding="utf-8"))
    assert packet["inputs"]["tickers"] == DEFAULT_THESIS_TICKERS


def test_thesis_sentiment_overlay_records_engine_run(tmp_path: Path, capsys, monkeypatch) -> None:
    monte = tmp_path / "monte-carlo"
    monte.mkdir()
    for filename in ("simulate_cli.py", "decision.py"):
        (monte / filename).write_text("# marker\n", encoding="utf-8")
    (monte / "public_cli.py").write_text(
        """
def parse_public_args(argv):
    return list(argv)

def execute_public_simulate(args):
    return {
        "report": {
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
            "allocations": {"AAPL": {"weight": 0.6}},
            "errors": [],
        }
    }

def format_public_simulation_output(result, details, output):
    return result["report"]["action_plan"]["headline"]
""",
        encoding="utf-8",
    )
    package = tmp_path / "stock-sentiment-analysis" / "stock_sentiment"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        """
import json

def main(argv):
    print(json.dumps({
        "ticker": "AAPL",
        "score": 0.3,
        "label": "positive",
        "confidence": 0.7,
        "signal": "buy",
        "articles_analyzed": 3,
        "classification_degraded": False,
        "classification_warnings": [],
        "source": "google-rss",
        "source_label": "Google News RSS"
    }))
    return 0
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    run_dir = tmp_path / "runs"

    exit_code = main(
        [
            "--namespace-root",
            str(tmp_path),
            "thesis",
            "AAPL",
            "--no-backtest",
            "--sentiment",
            "--output-dir",
            str(run_dir),
            "--no-ledger",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Sentiment overlay" in captured.out
    packet = json.loads((run_dir / "latest.json").read_text(encoding="utf-8"))
    assert packet["engine_runs"]["stock_sentiment"]["ok"]
    assert packet["engine_runs"]["stock_sentiment"]["payload"]["signal"] == "buy"


def test_replay_and_eval_commands_use_saved_packet(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "runs"
    ledger_dir = tmp_path / "ledger"
    assert (
        main(
            [
                "--namespace-root",
                str(tmp_path),
                "thesis",
                "--no-run",
                "--output-dir",
                str(run_dir),
                "--ledger-dir",
                str(ledger_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["replay", str(run_dir / "latest.json")]) == 0
    replayed = capsys.readouterr()
    assert "Ranked implementation loops" in replayed.out

    assert main(["eval", str(run_dir / "latest.json")]) == 2
    evaluated = capsys.readouterr()
    assert "monte_carlo_executed" in evaluated.out


def test_ledger_list_and_show_commands_use_saved_packet(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "runs"
    ledger_dir = tmp_path / "ledger"
    assert (
        main(
            [
                "--namespace-root",
                str(tmp_path),
                "thesis",
                "--no-run",
                "--output-dir",
                str(run_dir),
                "--ledger-dir",
                str(ledger_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["ledger", "--ledger-dir", str(ledger_dir), "list"]) == 0
    listed = capsys.readouterr()
    assert "run_" in listed.out

    latest = json.loads((ledger_dir / "latest.json").read_text(encoding="utf-8"))
    run_id = latest["run_id"]
    assert main(["ledger", "--ledger-dir", str(ledger_dir), "show", run_id]) == 0
    shown = capsys.readouterr()
    assert f"Run: {run_id}" in shown.out

    assert main(["ledger", "--ledger-dir", str(ledger_dir), "trust"]) == 0
    trusted = capsys.readouterr()
    assert f"Trust audit: {run_id}" in trusted.out
    assert "monte-carlo" in trusted.out

    assert main(["ledger", "--ledger-dir", str(ledger_dir), "report", "--min-runs", "1"]) == 2
    report = capsys.readouterr()
    assert "Ledger report: NOT READY" in report.out
    assert "Runs:" in report.out
    assert "Regimes:" in report.out
    assert "Promotion gates: loaded=True" in report.out
    assert "needs at least 1 regime replays" in report.out

    platform_exports = tmp_path / "platform_exports"
    assert (
        main(
            [
                "ledger",
                "--ledger-dir",
                str(ledger_dir),
                "sync",
                "research-run-platform",
                "--output-dir",
                str(platform_exports),
            ]
        )
        == 0
    )
    synced = capsys.readouterr()
    assert "Platform sync: research-run-platform" in synced.out
    assert "Validation: ok" in synced.out
    assert (platform_exports / "latest.json").exists()
    latest_export = json.loads((platform_exports / "latest.json").read_text(encoding="utf-8"))
    export_dir = platform_exports / latest_export["export_id"]

    assert main(["ledger", "import", "research-run-platform", str(export_dir)]) == 0
    imported = capsys.readouterr()
    assert "Platform import: research-run-platform" in imported.out
    assert "Import contract: ok" in imported.out

    promotions_dir = tmp_path / "promotions"
    assert (
        main(
            [
                "ledger",
                "--ledger-dir",
                str(ledger_dir),
                "promote",
                "--min-runs",
                "1",
                "--promotions-dir",
                str(promotions_dir),
            ]
        )
        == 2
    )
    promoted = capsys.readouterr()
    assert "Promotion: blocked" in promoted.out
    assert (promotions_dir / "latest.json").exists()
    promotion = json.loads((promotions_dir / "latest.json").read_text(encoding="utf-8"))
    assert promotion["report"]["promotion"]["gates"]["loaded"]
    assert not (promotions_dir / "canonical.json").exists()


def test_ledger_verify_production_reports_all_components(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "runs"
    ledger_dir = tmp_path / "ledger"
    assert (
        main(
            [
                "--namespace-root",
                str(tmp_path),
                "thesis",
                "--no-run",
                "--output-dir",
                str(run_dir),
                "--ledger-dir",
                str(ledger_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()

    exit_code = main(
        [
            "--namespace-root",
            str(tmp_path),
            "ledger",
            "--ledger-dir",
            str(ledger_dir),
            "verify-production",
            "--platform-output-dir",
            str(tmp_path / "platform_exports"),
            "--promotions-dir",
            str(tmp_path / "promotions"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "Production verification: NOT READY" in captured.out
    assert "fixtures=fail" in captured.out
    assert "platform_export=ok" in captured.out
    assert "platform_import=ok" in captured.out
    assert "fixtures:" in captured.out
    assert "ledger:" in captured.out


def test_ledger_verify_production_json_is_machine_readable(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "runs"
    ledger_dir = tmp_path / "ledger"
    assert (
        main(
            [
                "--namespace-root",
                str(tmp_path),
                "thesis",
                "--no-run",
                "--output-dir",
                str(run_dir),
                "--ledger-dir",
                str(ledger_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()

    exit_code = main(
        [
            "--namespace-root",
            str(tmp_path),
            "ledger",
            "--ledger-dir",
            str(ledger_dir),
            "verify-production",
            "--platform-output-dir",
            str(tmp_path / "platform_exports"),
            "--promotions-dir",
            str(tmp_path / "promotions"),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 2
    assert not payload["ok"]
    assert payload["component_ready"]["fixtures"] is False
    assert payload["component_ready"]["platform_export"] is True
    assert payload["component_ready"]["platform_import"] is True
    assert payload["promotion_gates"]["loaded"]


def test_ci_production_fixture_verifies_ready(tmp_path: Path, capsys, monkeypatch) -> None:
    paths = build_fixture(tmp_path / "ci-production")
    monkeypatch.setenv(PLATFORM_SIGNING_KEY_ENV, "ci-production-platform-signing-key")

    exit_code = main(
        [
            "--namespace-root",
            paths["root"],
            "ledger",
            "--ledger-dir",
            paths["ledger_dir"],
            "--gates",
            paths["gates"],
            "verify-production",
            "--price-dir",
            paths["price_dir"],
            "--platform-output-dir",
            paths["platform_output_dir"],
            "--promotions-dir",
            paths["promotions_dir"],
            "--require-platform-signature",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Production verification: READY" in captured.out
    assert "fixtures=ok" in captured.out
    assert "platform_import=ok" in captured.out
    assert "signature=verified" in captured.out


def test_ci_production_fixture_signed_json_includes_promotion_attempts(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    paths = build_fixture(tmp_path / "ci-production")
    monkeypatch.setenv(PLATFORM_SIGNING_KEY_ENV, "ci-production-platform-signing-key")

    exit_code = main(
        [
            "--namespace-root",
            paths["root"],
            "ledger",
            "--ledger-dir",
            paths["ledger_dir"],
            "--gates",
            paths["gates"],
            "verify-production",
            "--price-dir",
            paths["price_dir"],
            "--platform-output-dir",
            paths["platform_output_dir"],
            "--promotions-dir",
            paths["promotions_dir"],
            "--require-platform-signature",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["ledger"]["promotion_attempts"]["attempt_count"] == 2
    assert (
        payload["ledger"]["promotion_attempts"]["latest"]["promotion_id"]
        == "promotion_ci_promoted"
    )
    assert payload["platform_export"]["manifest"]["counts"]["promotions"] == 2
    import_attempts = payload["platform_import"]["summary"]["promotion_attempts"]
    assert import_attempts["attempt_count"] == 2
    assert import_attempts["categories"]["top"][0]["category"] == "backtest"
    records_by_run = {
        record["run_id"]: record for record in payload["platform_import"]["records"]
    }
    latest_record = records_by_run["run_ci_3"]
    assert latest_record["latest_promotion_id"] == "promotion_ci_promoted"
    assert latest_record["promotion_attempts"]["attempt_count"] == 2
