from __future__ import annotations

import json
from pathlib import Path

from agent_harness.cli import main


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

    assert main(["ledger", "--ledger-dir", str(ledger_dir), "report", "--min-runs", "1"]) == 0
    report = capsys.readouterr()
    assert "Ledger report:" in report.out
    assert "Runs:" in report.out

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
    assert not (promotions_dir / "canonical.json").exists()
