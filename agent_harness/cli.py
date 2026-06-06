"""Command line interface for the agent harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agent_harness import __version__
from agent_harness.allocation_repair import repair_monte_carlo_allocation
from agent_harness.adapters import (
    LocalLedgerAdapter,
    MonteCarloAdapter,
    ResearchRunPlatformAdapter,
    StockSentimentAdapter,
)
from agent_harness.capital import build_capital_loops
from agent_harness.evals import evaluate_packet
from agent_harness.fixtures import (
    audit_fixture_universe,
    default_fixture_report_dir,
    default_price_fixture_dir,
    refresh_fixture_universe,
    write_fixture_refresh_report,
    write_fixture_universe_report,
)
from agent_harness.ledger import (
    build_repo_trust,
    default_ledger_dir,
    get_ledger_entry,
    ingest_packet,
    ingest_regime_replay,
    ingest_outcome,
    load_ledger_packet,
    read_ledger_entries,
    read_regime_entries,
    read_outcome_entries,
)
from agent_harness.outcomes import (
    backfill_ledger_outcomes,
    default_outcome_dir,
    evaluate_outcome,
    write_outcome,
)
from agent_harness.packets import build_run_packet, status_to_payload, validate_run_packet
from agent_harness.platform_sync import (
    default_platform_export_dir,
    load_platform_export_bundle,
    validate_platform_export,
    write_platform_export,
)
from agent_harness.promotion_gates import (
    DEFAULT_PROMOTION_GATES_FILE,
    build_gates_from_calibration,
    load_promotion_gates,
    promotion_gates_summary,
    write_promotion_gates,
)
from agent_harness.promotions import (
    default_promotions_dir,
    promote_latest,
    read_promotion_attempts,
)
from agent_harness.regimes import (
    DEFAULT_MAX_REGIME_DRAWDOWN,
    DEFAULT_REGIME_START_DATE,
    default_regime_dir,
    evaluate_packet_regimes,
    write_regime_replay,
)
from agent_harness.registry import (
    default_namespace_root,
    discover_repositories,
    known_repo_specs,
)
from agent_harness.reports import (
    build_ledger_report,
    build_outcome_calibration_report,
    build_outcome_report,
    build_regime_report,
)
from agent_harness.store import default_run_dir, load_packet, write_packet
from agent_harness.trust_policy import evaluate_repo_trust, load_trust_policy


DEFAULT_THESIS_TICKERS = ["AAPL", "MSFT", "GOOGL", "JPM", "XOM"]


def _status_payload(status: Any) -> dict[str, Any]:
    return status_to_payload(status)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-harness",
        description="Orchestrate sibling decision engines into a capital research loop.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--namespace-root",
        type=Path,
        default=default_namespace_root(),
        help="Directory containing sibling repos. Defaults to AGENT_HARNESS_NAMESPACE_ROOT or this repo's parent.",
    )

    subparsers = parser.add_subparsers(dest="command")
    scan = subparsers.add_parser("scan", help="Show discovered sibling repositories and adapter readiness.")
    scan.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    thesis = subparsers.add_parser("thesis", help="Run the capital thesis and rank implementation loops.")
    thesis.add_argument(
        "tickers",
        nargs="*",
        default=DEFAULT_THESIS_TICKERS,
        help="Tickers for the offline Monte Carlo smoke. Defaults to AAPL MSFT GOOGL JPM XOM.",
    )
    thesis.add_argument("--days", type=int, default=30, help="Trading days for the smoke simulation.")
    thesis.add_argument("--scenarios", type=int, default=100, help="Monte Carlo paths for the smoke simulation.")
    thesis.add_argument("--seed", type=int, default=42, help="Deterministic seed for the smoke simulation.")
    thesis.add_argument("--no-run", action="store_true", help="Rank loops without executing sibling engines.")
    thesis.add_argument(
        "--backtest",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run a walk-forward validation smoke after simulation. Enabled by default.",
    )
    thesis.add_argument("--backtest-lookback", type=int, default=3, help="Lookback days for offline backtest smoke.")
    thesis.add_argument("--backtest-hold", type=int, default=2, help="Holding days for offline backtest smoke.")
    thesis.add_argument("--backtest-rebalance", type=int, default=2, help="Rebalance interval for offline backtest smoke.")
    thesis.add_argument("--backtest-scenarios", type=int, default=20, help="Monte Carlo paths per backtest rebalance.")
    thesis.add_argument(
        "--sentiment",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run stock-sentiment-analysis for the Monte Carlo primary pick. Disabled by default.",
    )
    thesis.add_argument("--sentiment-days", type=int, default=3, help="News lookback days for sentiment overlay.")
    thesis.add_argument("--sentiment-max-articles", type=int, default=10, help="Maximum articles for sentiment overlay.")
    thesis.add_argument(
        "--sentiment-source",
        choices=["auto", "newsapi", "google-rss"],
        default="auto",
        help="News source for sentiment overlay.",
    )
    thesis.add_argument(
        "--sentiment-half-life-hours",
        type=float,
        default=24.0,
        help="Sentiment recency half-life in hours.",
    )
    thesis.add_argument(
        "--sentiment-include-reasons",
        action="store_true",
        help="Include per-article sentiment reasons in JSON packets.",
    )
    thesis.add_argument(
        "--allocation-repair",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply deterministic regime-replay allocation repair to Monte Carlo output. Enabled by default.",
    )
    thesis.add_argument(
        "--save",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Persist a replayable run packet. Enabled by default.",
    )
    thesis.add_argument(
        "--output-dir",
        type=Path,
        default=default_run_dir(),
        help="Directory for saved run packets.",
    )
    thesis.add_argument(
        "--ledger-dir",
        type=Path,
        default=default_ledger_dir(),
        help="Directory for the append-only provenance ledger.",
    )
    thesis.add_argument(
        "--ledger",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ingest saved packets into the provenance ledger. Enabled by default.",
    )
    thesis.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    replay = subparsers.add_parser("replay", help="Replay a saved run packet without executing engines.")
    replay.add_argument("packet", type=Path, help="Path to a saved run-packet JSON file.")
    replay.add_argument("--json", action="store_true", help="Emit the packet JSON.")

    eval_parser = subparsers.add_parser("eval", help="Evaluate a saved run packet for production readiness.")
    eval_parser.add_argument("packet", type=Path, help="Path to a saved run-packet JSON file.")
    eval_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    outcome = subparsers.add_parser("outcome", help="Evaluate realized returns for a saved run packet.")
    outcome.add_argument("packet", type=Path, help="Path to a saved run-packet JSON file.")
    outcome.add_argument(
        "--price-dir",
        type=Path,
        help="Directory containing Date,Close CSVs. Defaults to <namespace_root>/monte-carlo/sample_data.",
    )
    outcome.add_argument("--start-date", help="Start date present in every ticker CSV.")
    outcome.add_argument("--end-date", help="End date present in every ticker CSV.")
    outcome.add_argument("--horizon-rows", type=int, help="Rows after start date to evaluate when end date is omitted.")
    outcome.add_argument("--cash-return", type=float, default=0.0, help="Cash return over the evaluation window.")
    outcome.add_argument(
        "--save",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Persist a realized-outcome artifact. Enabled by default.",
    )
    outcome.add_argument(
        "--output-dir",
        type=Path,
        default=default_outcome_dir(),
        help="Directory for saved outcome artifacts.",
    )
    outcome.add_argument(
        "--ledger-dir",
        type=Path,
        default=default_ledger_dir(),
        help="Directory for the append-only provenance ledger.",
    )
    outcome.add_argument(
        "--ledger",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ingest saved outcomes into the provenance ledger. Enabled by default.",
    )
    outcome.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    regimes = subparsers.add_parser(
        "regime-replay",
        help="Replay a saved run packet across deterministic synthetic market regimes.",
    )
    regimes.add_argument("packet", type=Path, help="Path to a saved run-packet JSON file.")
    regimes.add_argument(
        "--output-dir",
        type=Path,
        default=default_regime_dir(),
        help="Directory for generated regime prices and replay reports.",
    )
    regimes.add_argument(
        "--rows",
        type=int,
        default=5,
        help="Rows to generate per synthetic price CSV.",
    )
    regimes.add_argument(
        "--start-date",
        default=DEFAULT_REGIME_START_DATE,
        help="Start date for generated synthetic prices.",
    )
    regimes.add_argument("--cash-return", type=float, default=0.0, help="Cash return over the replay window.")
    regimes.add_argument(
        "--max-drawdown",
        type=float,
        default=DEFAULT_MAX_REGIME_DRAWDOWN,
        help="Maximum acceptable realized drawdown in any replay regime.",
    )
    regimes.add_argument(
        "--ledger-dir",
        type=Path,
        default=default_ledger_dir(),
        help="Directory for the append-only provenance ledger.",
    )
    regimes.add_argument(
        "--ledger",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ingest saved regime replays into the provenance ledger. Enabled by default.",
    )
    regimes.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    fixtures = subparsers.add_parser("fixtures", help="Audit offline price fixtures.")
    fixtures_sub = fixtures.add_subparsers(dest="fixtures_command", required=True)
    fixtures_audit = fixtures_sub.add_parser(
        "audit",
        help="Measure fixture breadth, sector tags, hashes, and return correlations.",
    )
    fixtures_audit.add_argument("tickers", nargs="*", help="Tickers to audit. Defaults to every CSV in the fixture directory.")
    fixtures_audit.add_argument(
        "--price-dir",
        type=Path,
        help="Directory containing Date,Close CSVs. Defaults to <namespace_root>/monte-carlo/sample_data.",
    )
    fixtures_audit.add_argument("--sector-map", type=Path, help="Optional ticker-to-sector JSON map.")
    fixtures_audit.add_argument("--min-tickers", type=int, default=5, help="Minimum valid fixture tickers.")
    fixtures_audit.add_argument("--min-rows", type=int, default=10, help="Minimum rows required for every valid ticker.")
    fixtures_audit.add_argument("--min-common-dates", type=int, default=5, help="Minimum aligned dates across the universe.")
    fixtures_audit.add_argument("--min-sectors", type=int, default=3, help="Minimum known sectors required.")
    fixtures_audit.add_argument(
        "--max-pairwise-correlation",
        type=float,
        default=0.98,
        help="Maximum allowed absolute pairwise return correlation.",
    )
    fixtures_audit.add_argument(
        "--save",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Persist the fixture-universe audit report. Enabled by default.",
    )
    fixtures_audit.add_argument(
        "--output-dir",
        type=Path,
        default=default_fixture_report_dir(),
        help="Directory for fixture audit artifacts.",
    )
    fixtures_audit.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    fixtures_refresh = fixtures_sub.add_parser(
        "refresh",
        help="Refresh local price fixtures from an external historical source.",
    )
    fixtures_refresh.add_argument("tickers", nargs="*", help="Tickers to refresh. Defaults to the thesis universe.")
    fixtures_refresh.add_argument(
        "--source",
        choices=["stooq", "csv-dir"],
        default="stooq",
        help="Historical data source.",
    )
    fixtures_refresh.add_argument(
        "--source-dir",
        type=Path,
        help="Directory of vendor/source CSVs when --source csv-dir is used.",
    )
    fixtures_refresh.add_argument(
        "--price-dir",
        type=Path,
        help="Directory to write Date,Close CSVs. Defaults to <namespace_root>/monte-carlo/sample_data.",
    )
    fixtures_refresh.add_argument("--start-date", help="Optional start date, YYYY-MM-DD.")
    fixtures_refresh.add_argument("--end-date", help="Optional end date, YYYY-MM-DD.")
    fixtures_refresh.add_argument("--timeout", type=float, default=15.0, help="Per-ticker download timeout in seconds.")
    fixtures_refresh.add_argument(
        "--verify-tls",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify HTTPS certificates when downloading fixtures. Enabled by default.",
    )
    fixtures_refresh.add_argument("--sector-map", type=Path, help="Optional ticker-to-sector JSON map for the follow-up audit.")
    fixtures_refresh.add_argument("--min-tickers", type=int, default=5, help="Minimum valid fixture tickers for the follow-up audit.")
    fixtures_refresh.add_argument("--min-rows", type=int, default=10, help="Minimum rows required for every valid ticker.")
    fixtures_refresh.add_argument("--min-common-dates", type=int, default=5, help="Minimum aligned dates across the universe.")
    fixtures_refresh.add_argument("--min-sectors", type=int, default=3, help="Minimum known sectors required.")
    fixtures_refresh.add_argument(
        "--max-pairwise-correlation",
        type=float,
        default=0.98,
        help="Maximum allowed absolute pairwise return correlation.",
    )
    fixtures_refresh.add_argument(
        "--audit",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run fixture audit after refresh. Enabled by default.",
    )
    fixtures_refresh.add_argument(
        "--save",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Persist the refresh report. Enabled by default.",
    )
    fixtures_refresh.add_argument(
        "--output-dir",
        type=Path,
        default=default_fixture_report_dir(),
        help="Directory for fixture refresh artifacts.",
    )
    fixtures_refresh.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    ledger = subparsers.add_parser("ledger", help="Inspect or update the provenance ledger.")
    ledger.add_argument(
        "--ledger-dir",
        type=Path,
        default=default_ledger_dir(),
        help="Directory for the append-only provenance ledger.",
    )
    ledger.add_argument(
        "--gates",
        type=Path,
        help="Path to promotion-gates JSON. Defaults to agent-harness.gates.json when present.",
    )
    ledger.add_argument(
        "--no-gates",
        action="store_true",
        help="Ignore configured promotion-gates defaults for this ledger command.",
    )
    ledger_sub = ledger.add_subparsers(dest="ledger_command", required=True)
    ledger_ingest = ledger_sub.add_parser("ingest", help="Ingest a saved packet into the ledger.")
    ledger_ingest.add_argument("packet", type=Path, help="Path to a saved run-packet JSON file.")
    ledger_ingest.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_list = ledger_sub.add_parser("list", help="List recent ledger entries.")
    ledger_list.add_argument("--limit", type=int, default=10, help="Maximum entries to print.")
    ledger_list.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_show = ledger_sub.add_parser("show", help="Show one ledger entry or packet.")
    ledger_show.add_argument("run_id", help="Run id to show.")
    ledger_show.add_argument("--packet", action="store_true", help="Show the stored packet copy.")
    ledger_show.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_report = ledger_sub.add_parser("report", help="Summarize ledger performance and promotion readiness.")
    ledger_report.add_argument("--min-runs", type=int, default=3, help="Runs required before promotion readiness.")
    ledger_report.add_argument("--min-outcomes", type=int, default=0, help="Realized outcomes required before promotion readiness.")
    ledger_report.add_argument("--min-outcome-ok-rate", type=float, help="Minimum realized outcome scorecard ok rate.")
    ledger_report.add_argument("--min-outcome-excess-cash", type=float, help="Minimum average realized excess return versus cash.")
    ledger_report.add_argument("--min-outcome-excess-equal", type=float, help="Minimum average realized excess return versus equal weight.")
    ledger_report.add_argument("--max-outcome-forecast-error", type=float, help="Maximum average absolute forecast error.")
    ledger_report.add_argument("--max-outcome-drawdown", type=float, help="Maximum realized drawdown across outcomes.")
    ledger_report.add_argument("--min-outcome-sentiment-outcomes", type=int, default=0, help="Minimum realized sentiment directional outcomes.")
    ledger_report.add_argument("--min-outcome-sentiment-hit-rate", type=float, help="Minimum realized sentiment directional hit rate.")
    ledger_report.add_argument("--min-outcome-sentiment-alignment", type=float, help="Minimum average confidence-weighted sentiment/return alignment.")
    ledger_report.add_argument("--min-regime-replays", type=int, default=0, help="Deterministic regime replays required before promotion readiness.")
    ledger_report.add_argument("--require-latest-regime-replay", action=argparse.BooleanOptionalAction, default=False, help="Require a regime replay for the latest ledger run.")
    ledger_report.add_argument("--require-regime-ok", action=argparse.BooleanOptionalAction, default=False, help="Require the latest regime replay to have no fragile regimes.")
    ledger_report.add_argument("--max-regime-fragile-count", type=int, help="Maximum fragile regimes allowed in the latest replay.")
    ledger_report.add_argument("--max-regime-drawdown", type=float, help="Maximum worst drawdown allowed in the latest replay.")
    ledger_report.add_argument("--min-regime-excess-cash", type=float, help="Minimum worst excess return versus cash in the latest replay.")
    ledger_report.add_argument("--min-regime-excess-equal", type=float, help="Minimum worst excess return versus equal weight in the latest replay.")
    ledger_report.add_argument("--trust-policy", type=Path, help="Path to a trust-policy JSON file.")
    ledger_report.add_argument(
        "--promotions-dir",
        type=Path,
        default=default_promotions_dir(),
        help="Directory containing promotion attempts to summarize.",
    )
    ledger_report.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_outcomes = ledger_sub.add_parser("outcomes", help="Summarize realized outcome performance.")
    ledger_outcomes.add_argument("--min-outcomes", type=int, default=0, help="Outcomes required before readiness.")
    ledger_outcomes.add_argument("--min-ok-rate", type=float, help="Minimum realized outcome scorecard ok rate.")
    ledger_outcomes.add_argument("--min-excess-cash", type=float, help="Minimum average realized excess return versus cash.")
    ledger_outcomes.add_argument("--min-excess-equal", type=float, help="Minimum average realized excess return versus equal weight.")
    ledger_outcomes.add_argument("--max-forecast-error", type=float, help="Maximum average absolute forecast error.")
    ledger_outcomes.add_argument("--max-drawdown", type=float, help="Maximum realized drawdown across outcomes.")
    ledger_outcomes.add_argument("--min-sentiment-outcomes", type=int, default=0, help="Minimum realized sentiment directional outcomes.")
    ledger_outcomes.add_argument("--min-sentiment-hit-rate", type=float, help="Minimum realized sentiment directional hit rate.")
    ledger_outcomes.add_argument("--min-sentiment-alignment", type=float, help="Minimum average confidence-weighted sentiment/return alignment.")
    ledger_outcomes.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_regimes = ledger_sub.add_parser("regimes", help="Summarize deterministic regime replay performance.")
    ledger_regimes.add_argument("--min-regime-replays", type=int, default=0, help="Regime replays required before readiness.")
    ledger_regimes.add_argument("--require-latest-regime-replay", action=argparse.BooleanOptionalAction, default=False, help="Require a regime replay for the latest ledger run.")
    ledger_regimes.add_argument("--require-regime-ok", action=argparse.BooleanOptionalAction, default=False, help="Require the latest regime replay to have no fragile regimes.")
    ledger_regimes.add_argument("--max-regime-fragile-count", type=int, help="Maximum fragile regimes allowed in the latest replay.")
    ledger_regimes.add_argument("--max-regime-drawdown", type=float, help="Maximum worst drawdown allowed in the latest replay.")
    ledger_regimes.add_argument("--min-regime-excess-cash", type=float, help="Minimum worst excess return versus cash in the latest replay.")
    ledger_regimes.add_argument("--min-regime-excess-equal", type=float, help="Minimum worst excess return versus equal weight in the latest replay.")
    ledger_regimes.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_calibrate = ledger_sub.add_parser(
        "calibrate-outcomes",
        help="Recommend production outcome thresholds from realized ledger evidence.",
    )
    ledger_calibrate.add_argument(
        "--min-sample",
        type=int,
        default=20,
        help="Minimum realized outcomes required before calibration is considered ready.",
    )
    ledger_calibrate.add_argument(
        "--sentiment-min-sample",
        type=int,
        default=10,
        help="Minimum directional sentiment outcomes before recommending sentiment gates.",
    )
    ledger_calibrate.add_argument(
        "--write-gates",
        action="store_true",
        help="Write calibrated promotion gates when calibration is ready.",
    )
    ledger_calibrate.add_argument(
        "--gates-output",
        type=Path,
        default=Path(DEFAULT_PROMOTION_GATES_FILE),
        help="Output path for --write-gates.",
    )
    ledger_calibrate.add_argument(
        "--gate-min-runs",
        type=int,
        default=3,
        help="min_runs value to write into calibrated gates.",
    )
    ledger_calibrate.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_backfill = ledger_sub.add_parser(
        "backfill-outcomes",
        help="Evaluate and ingest realized outcomes for saved ledger packets.",
    )
    ledger_backfill.add_argument(
        "--price-dir",
        type=Path,
        help="Directory containing Date,Close CSVs. Defaults to <namespace_root>/monte-carlo/sample_data.",
    )
    ledger_backfill.add_argument(
        "--output-dir",
        type=Path,
        default=default_outcome_dir(),
        help="Directory for saved outcome artifacts.",
    )
    ledger_backfill.add_argument("--start-date", help="Start date present in every ticker CSV.")
    ledger_backfill.add_argument("--end-date", help="End date present in every ticker CSV.")
    ledger_backfill.add_argument(
        "--horizon-rows",
        type=int,
        help="Rows after each start date to evaluate when end date is omitted.",
    )
    ledger_backfill.add_argument(
        "--cash-return",
        type=float,
        default=0.0,
        help="Cash return over each evaluation window.",
    )
    ledger_backfill.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        help="Backfill only the selected run id. Repeatable.",
    )
    ledger_backfill.add_argument(
        "--limit",
        type=int,
        help="Backfill only the latest N matching ledger runs.",
    )
    ledger_backfill.add_argument(
        "--rolling",
        action="store_true",
        help="Evaluate every possible rolling window instead of one window per run.",
    )
    ledger_backfill.add_argument(
        "--stride-rows",
        type=int,
        default=1,
        help="Start-date stride for rolling windows.",
    )
    ledger_backfill.add_argument(
        "--max-windows",
        type=int,
        help="Maximum rolling windows per run.",
    )
    ledger_backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate and report what would be ingested without writing artifacts.",
    )
    ledger_backfill.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_trust = ledger_sub.add_parser("trust", help="Audit repo trust metadata for the latest or selected run.")
    ledger_trust.add_argument("run_id", nargs="?", help="Run id to audit. Defaults to the latest ledger entry.")
    ledger_trust.add_argument("--trust-policy", type=Path, help="Path to a trust-policy JSON file.")
    ledger_trust.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_sync = ledger_sub.add_parser("sync", help="Export ledger data for an external platform.")
    ledger_sync.add_argument(
        "target",
        choices=["research-run-platform"],
        help="Platform export target.",
    )
    ledger_sync.add_argument(
        "--output-dir",
        type=Path,
        default=default_platform_export_dir(),
        help="Directory for platform export bundles.",
    )
    ledger_sync.add_argument(
        "--promotions-dir",
        type=Path,
        default=default_promotions_dir(),
        help="Directory containing promotion attempts to include.",
    )
    ledger_sync.add_argument(
        "--allow-missing-artifacts",
        action="store_true",
        help="Validate the bundle but do not fail when packet/outcome artifact copies are missing.",
    )
    ledger_sync.add_argument(
        "--signing-key-file",
        type=Path,
        help=(
            "File containing the platform bundle HMAC key. "
            "If omitted, AGENT_HARNESS_PLATFORM_SIGNING_KEY is used when set."
        ),
    )
    ledger_sync.add_argument(
        "--require-signature",
        action="store_true",
        help="Fail validation unless the written bundle has a verified manifest signature.",
    )
    ledger_sync.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_import = ledger_sub.add_parser(
        "import",
        help="Validate and stage a platform export bundle for receiving-side import.",
    )
    ledger_import.add_argument(
        "target",
        choices=["research-run-platform"],
        help="Platform import target.",
    )
    ledger_import.add_argument("export_dir", type=Path, help="Path to a platform export bundle.")
    ledger_import.add_argument(
        "--allow-missing-artifacts",
        action="store_true",
        help="Stage rows even when artifact copies are missing.",
    )
    ledger_import.add_argument(
        "--signing-key-file",
        type=Path,
        help=(
            "File containing the platform bundle HMAC verification key. "
            "If omitted, AGENT_HARNESS_PLATFORM_SIGNING_KEY is used when set."
        ),
    )
    ledger_import.add_argument(
        "--require-signature",
        action="store_true",
        help="Fail import unless the bundle has a verified manifest signature.",
    )
    ledger_import.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_verify = ledger_sub.add_parser(
        "verify-production",
        help="Run fixture, outcome, regime, ledger, and platform import checks as one production gate.",
    )
    ledger_verify.add_argument(
        "--price-dir",
        type=Path,
        help="Directory containing Date,Close CSVs. Defaults to <namespace_root>/monte-carlo/sample_data.",
    )
    ledger_verify.add_argument("--sector-map", type=Path, help="Optional ticker-to-sector JSON map.")
    ledger_verify.add_argument("--min-tickers", type=int, default=5, help="Minimum valid fixture tickers.")
    ledger_verify.add_argument("--min-rows", type=int, default=10, help="Minimum rows required for every valid ticker.")
    ledger_verify.add_argument("--min-common-dates", type=int, default=5, help="Minimum aligned dates across the universe.")
    ledger_verify.add_argument("--min-sectors", type=int, default=3, help="Minimum known sectors required.")
    ledger_verify.add_argument(
        "--max-pairwise-correlation",
        type=float,
        default=0.98,
        help="Maximum allowed absolute pairwise return correlation.",
    )
    ledger_verify.add_argument("--min-runs", type=int, default=3, help="Runs required before promotion readiness.")
    ledger_verify.add_argument("--min-outcomes", type=int, default=0, help="Realized outcomes required before promotion readiness.")
    ledger_verify.add_argument("--min-outcome-ok-rate", type=float, help="Minimum realized outcome scorecard ok rate.")
    ledger_verify.add_argument("--min-outcome-excess-cash", type=float, help="Minimum average realized excess return versus cash.")
    ledger_verify.add_argument("--min-outcome-excess-equal", type=float, help="Minimum average realized excess return versus equal weight.")
    ledger_verify.add_argument("--max-outcome-forecast-error", type=float, help="Maximum average absolute forecast error.")
    ledger_verify.add_argument("--max-outcome-drawdown", type=float, help="Maximum realized drawdown across outcomes.")
    ledger_verify.add_argument("--min-outcome-sentiment-outcomes", type=int, default=0, help="Minimum realized sentiment directional outcomes.")
    ledger_verify.add_argument("--min-outcome-sentiment-hit-rate", type=float, help="Minimum realized sentiment directional hit rate.")
    ledger_verify.add_argument("--min-outcome-sentiment-alignment", type=float, help="Minimum average confidence-weighted sentiment/return alignment.")
    ledger_verify.add_argument("--min-regime-replays", type=int, default=0, help="Deterministic regime replays required before promotion readiness.")
    ledger_verify.add_argument("--require-latest-regime-replay", action=argparse.BooleanOptionalAction, default=False, help="Require a regime replay for the latest ledger run.")
    ledger_verify.add_argument("--require-regime-ok", action=argparse.BooleanOptionalAction, default=False, help="Require the latest regime replay to have no fragile regimes.")
    ledger_verify.add_argument("--max-regime-fragile-count", type=int, help="Maximum fragile regimes allowed in the latest replay.")
    ledger_verify.add_argument("--max-regime-drawdown", type=float, help="Maximum worst drawdown allowed in the latest replay.")
    ledger_verify.add_argument("--min-regime-excess-cash", type=float, help="Minimum worst excess return versus cash in the latest replay.")
    ledger_verify.add_argument("--min-regime-excess-equal", type=float, help="Minimum worst excess return versus equal weight in the latest replay.")
    ledger_verify.add_argument("--trust-policy", type=Path, help="Path to a trust-policy JSON file.")
    ledger_verify.add_argument(
        "--platform-output-dir",
        type=Path,
        default=default_platform_export_dir(),
        help="Directory for production-verification platform export bundles.",
    )
    ledger_verify.add_argument(
        "--promotions-dir",
        type=Path,
        default=default_promotions_dir(),
        help="Directory containing promotion attempts to include.",
    )
    ledger_verify.add_argument(
        "--allow-missing-artifacts",
        action="store_true",
        help="Validate platform export/import without failing on missing artifact copies.",
    )
    ledger_verify.add_argument(
        "--platform-signing-key-file",
        type=Path,
        help=(
            "File containing the platform bundle HMAC key for signing and verification. "
            "If omitted, AGENT_HARNESS_PLATFORM_SIGNING_KEY is used when set."
        ),
    )
    ledger_verify.add_argument(
        "--require-platform-signature",
        action="store_true",
        help="Require production verifier platform export/import signature verification.",
    )
    ledger_verify.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_promote = ledger_sub.add_parser("promote", help="Promote the latest ready ledger run to canonical.")
    ledger_promote.add_argument("--min-runs", type=int, default=3, help="Runs required before promotion readiness.")
    ledger_promote.add_argument("--min-outcomes", type=int, default=0, help="Realized outcomes required before promotion readiness.")
    ledger_promote.add_argument("--min-outcome-ok-rate", type=float, help="Minimum realized outcome scorecard ok rate.")
    ledger_promote.add_argument("--min-outcome-excess-cash", type=float, help="Minimum average realized excess return versus cash.")
    ledger_promote.add_argument("--min-outcome-excess-equal", type=float, help="Minimum average realized excess return versus equal weight.")
    ledger_promote.add_argument("--max-outcome-forecast-error", type=float, help="Maximum average absolute forecast error.")
    ledger_promote.add_argument("--max-outcome-drawdown", type=float, help="Maximum realized drawdown across outcomes.")
    ledger_promote.add_argument("--min-outcome-sentiment-outcomes", type=int, default=0, help="Minimum realized sentiment directional outcomes.")
    ledger_promote.add_argument("--min-outcome-sentiment-hit-rate", type=float, help="Minimum realized sentiment directional hit rate.")
    ledger_promote.add_argument("--min-outcome-sentiment-alignment", type=float, help="Minimum average confidence-weighted sentiment/return alignment.")
    ledger_promote.add_argument("--min-regime-replays", type=int, default=0, help="Deterministic regime replays required before promotion readiness.")
    ledger_promote.add_argument("--require-latest-regime-replay", action=argparse.BooleanOptionalAction, default=False, help="Require a regime replay for the latest ledger run.")
    ledger_promote.add_argument("--require-regime-ok", action=argparse.BooleanOptionalAction, default=False, help="Require the latest regime replay to have no fragile regimes.")
    ledger_promote.add_argument("--max-regime-fragile-count", type=int, help="Maximum fragile regimes allowed in the latest replay.")
    ledger_promote.add_argument("--max-regime-drawdown", type=float, help="Maximum worst drawdown allowed in the latest replay.")
    ledger_promote.add_argument("--min-regime-excess-cash", type=float, help="Minimum worst excess return versus cash in the latest replay.")
    ledger_promote.add_argument("--min-regime-excess-equal", type=float, help="Minimum worst excess return versus equal weight in the latest replay.")
    ledger_promote.add_argument("--trust-policy", type=Path, help="Path to a trust-policy JSON file.")
    ledger_promote.add_argument(
        "--promotions-dir",
        type=Path,
        default=default_promotions_dir(),
        help="Directory for promotion artifacts.",
    )
    ledger_promote.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def _build_statuses(namespace_root: Path, *, ledger_dir: Path | None = None) -> dict[str, Any]:
    specs = {spec.name: spec for spec in known_repo_specs(namespace_root)}
    return {
        "monte-carlo": MonteCarloAdapter(specs["monte-carlo"].path).status(),
        "stock-sentiment-analysis": StockSentimentAdapter(
            specs["stock-sentiment-analysis"].path
        ).status(),
        "research-run-platform": ResearchRunPlatformAdapter(
            specs["research-run-platform"].path
        ).status(),
        "agent-harness-ledger": LocalLedgerAdapter(
            specs.get("agent-harness", specs["monte-carlo"]).path
            if "agent-harness" in specs
            else Path(__file__).resolve().parents[1],
            ledger_dir or default_ledger_dir(),
        ).status(),
    }


def _render_scan(namespace_root: Path, *, json_output: bool) -> int:
    discovered = discover_repositories(namespace_root)
    specs = known_repo_specs(namespace_root)
    statuses = _build_statuses(namespace_root)
    payload = {
        "namespace_root": str(namespace_root.expanduser().resolve()),
        "discovered": {name: str(path) for name, path in discovered.items()},
        "known_repos": [
            {
                "name": spec.name,
                "path": str(spec.path),
                "exists": spec.exists,
                "purpose": spec.purpose,
                "stack": spec.stack,
                "capabilities": list(spec.capabilities),
            }
            for spec in specs
        ],
        "adapters": {name: _status_payload(status) for name, status in statuses.items()},
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"Namespace: {payload['namespace_root']}")
    print("")
    print("Known engines")
    for spec in specs:
        marker = "ready" if spec.exists else "missing"
        print(f"- {spec.name}: {marker} | {spec.stack} | {spec.purpose}")
    print("")
    print("Adapter readiness")
    for status in statuses.values():
        marker = "ready" if status.available else "blocked"
        print(f"- {status.name}: {marker} | {status.reason}")
    return 0


def _render_thesis(args: argparse.Namespace) -> int:
    namespace_root = args.namespace_root.expanduser().resolve()
    statuses = _build_statuses(namespace_root, ledger_dir=args.ledger_dir)
    monte_run = None
    monte_backtest = None
    stock_sentiment_run = None
    if not args.no_run:
        monte_path = namespace_root / "monte-carlo"
        monte_adapter = MonteCarloAdapter(monte_path)
        monte_run = monte_adapter.run_offline_simulation(
            tuple(args.tickers or DEFAULT_THESIS_TICKERS),
            days=int(args.days),
            scenarios=int(args.scenarios),
            seed=int(args.seed),
        )
        if args.backtest:
            monte_backtest = monte_adapter.run_offline_backtest(
                tuple(args.tickers or DEFAULT_THESIS_TICKERS),
                lookback=int(args.backtest_lookback),
                hold=int(args.backtest_hold),
                rebalance=int(args.backtest_rebalance),
                scenarios=int(args.backtest_scenarios),
                seed=int(args.seed),
            )
        if args.sentiment:
            sentiment_path = namespace_root / "stock-sentiment-analysis"
            sentiment_adapter = StockSentimentAdapter(sentiment_path)
            sentiment_ticker = str(args.tickers[0] if args.tickers else DEFAULT_THESIS_TICKERS[0])
            action_plan = (
                monte_run.payload.get("action_plan", {})
                if monte_run is not None and isinstance(monte_run.payload, dict)
                else {}
            )
            primary = action_plan.get("primary_pick", {}) if isinstance(action_plan, dict) else {}
            if isinstance(primary, dict) and primary.get("ticker"):
                sentiment_ticker = str(primary["ticker"])
            stock_sentiment_run = sentiment_adapter.run_analysis(
                sentiment_ticker,
                days=int(args.sentiment_days),
                max_articles=int(args.sentiment_max_articles),
                source=str(args.sentiment_source),
                half_life_hours=float(args.sentiment_half_life_hours),
                include_reasons=bool(args.sentiment_include_reasons),
            )
        if args.allocation_repair:
            monte_run = repair_monte_carlo_allocation(
                monte_run,
                tickers=list(args.tickers or DEFAULT_THESIS_TICKERS),
                backtest_run=monte_backtest,
            )

    loops = build_capital_loops(
        statuses,
        monte_carlo_run=monte_run,
        monte_carlo_backtest=monte_backtest,
        stock_sentiment_run=stock_sentiment_run,
    )
    packet = build_run_packet(
        namespace_root=namespace_root,
        invocation=["agent-harness", *getattr(args, "_argv", [])],
        inputs={
            "tickers": list(args.tickers or DEFAULT_THESIS_TICKERS),
            "days": int(args.days),
            "scenarios": int(args.scenarios),
            "seed": int(args.seed),
            "ran_engines": not bool(args.no_run),
            "ran_backtest": bool(monte_backtest is not None),
            "ran_sentiment": bool(stock_sentiment_run is not None),
            "allocation_repair": {
                "enabled": bool(args.allocation_repair),
                "applied": bool(
                    monte_run is not None
                    and isinstance(monte_run.payload, dict)
                    and isinstance(monte_run.payload.get("allocation_repair"), dict)
                    and monte_run.payload["allocation_repair"].get("applied")
                ),
            },
            "backtest": {
                "lookback": int(args.backtest_lookback),
                "hold": int(args.backtest_hold),
                "rebalance": int(args.backtest_rebalance),
                "scenarios": int(args.backtest_scenarios),
            },
            "sentiment": {
                "enabled": bool(args.sentiment),
                "days": int(args.sentiment_days),
                "max_articles": int(args.sentiment_max_articles),
                "source": str(args.sentiment_source),
                "half_life_hours": float(args.sentiment_half_life_hours),
                "include_reasons": bool(args.sentiment_include_reasons),
            },
        },
        statuses=statuses,
        monte_carlo_run=monte_run,
        monte_carlo_backtest=monte_backtest,
        stock_sentiment_run=stock_sentiment_run,
        ranked_loops=loops,
    )
    saved_path = write_packet(packet, args.output_dir) if args.save else None
    ledger_entry = None
    if args.ledger and saved_path is not None:
        ledger_entry = ingest_packet(packet, packet_path=saved_path, ledger_dir=args.ledger_dir)
    if args.json:
        output_payload = dict(packet)
        if saved_path is not None:
            output_payload["artifact_path"] = str(saved_path)
        if ledger_entry is not None:
            output_payload["ledger_entry"] = ledger_entry
        print(json.dumps(output_payload, indent=2, sort_keys=True))
        return 0

    print("Capital thesis")
    print(f"Namespace: {namespace_root}")
    print(f"Run id: {packet['run_id']}")
    print("")
    if monte_run is not None:
        print("Monte Carlo smoke")
        print(monte_run.summary)
        print("")
    if monte_backtest is not None:
        print("Walk-forward backtest")
        print(monte_backtest.summary)
        print("")
    if stock_sentiment_run is not None:
        print("Sentiment overlay")
        print(stock_sentiment_run.summary)
        print("")
    print("Ranked implementation loops")
    for index, loop in enumerate(packet["ranked_loops"], start=1):
        print(f"{index}. {loop['name']} [{loop['repo']}] score={loop['score']:.3f}")
        print(f"   {loop['thesis']}")
        print(f"   Evidence: {'; '.join(loop['evidence'])}")
    if saved_path is not None:
        print("")
        print(f"Saved run packet: {saved_path}")
    if ledger_entry is not None:
        print(f"Ledger entry: {ledger_entry['run_id']} -> {ledger_entry['content_digest'][:12]}")
    return 0


def _render_replay(args: argparse.Namespace) -> int:
    packet = load_packet(args.packet)
    problems = validate_run_packet(packet)
    if args.json:
        print(json.dumps(packet, indent=2, sort_keys=True))
        return 0 if not problems else 2

    print(f"Replay: {packet.get('run_id', 'unknown')}")
    print(f"Created: {packet.get('created_at', 'unknown')}")
    if problems:
        print(f"Packet problems: {'; '.join(problems)}")
    monte_run = packet.get("engine_runs", {}).get("monte_carlo")
    if isinstance(monte_run, dict) and monte_run.get("summary"):
        print("")
        print("Monte Carlo smoke")
        print(monte_run["summary"])
    backtest_run = packet.get("engine_runs", {}).get("monte_carlo_backtest")
    if isinstance(backtest_run, dict) and backtest_run.get("summary"):
        print("")
        print("Walk-forward backtest")
        print(backtest_run["summary"])
    sentiment_run = packet.get("engine_runs", {}).get("stock_sentiment")
    if isinstance(sentiment_run, dict) and sentiment_run.get("summary"):
        print("")
        print("Sentiment overlay")
        print(sentiment_run["summary"])
    print("")
    print("Ranked implementation loops")
    loops = packet.get("ranked_loops", [])
    if isinstance(loops, list):
        for index, loop in enumerate(loops, start=1):
            if not isinstance(loop, dict):
                continue
            print(f"{index}. {loop.get('name')} [{loop.get('repo')}] score={loop.get('score')}")
            print(f"   {loop.get('thesis')}")
    return 0 if not problems else 2


def _render_eval(args: argparse.Namespace) -> int:
    packet = load_packet(args.packet)
    result = evaluate_packet(packet)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 2

    marker = "PASS" if result["ok"] else "FAIL"
    print(f"{marker} score={result['score']:.2f} checks={result['passed']}/{result['total']}")
    if result["dirty_repos"]:
        print(f"Dirty repos: {', '.join(result['dirty_repos'])}")
    for check in result["checks"]:
        state = "ok" if check["passed"] else "fail"
        print(f"- {state}: {check['name']} | {check['detail']}")
    return 0 if result["ok"] else 2


def _default_price_dir(packet: dict[str, Any]) -> Path:
    namespace_root = Path(str(packet.get("namespace_root") or Path.cwd()))
    return namespace_root / "monte-carlo" / "sample_data"


def _render_outcome(args: argparse.Namespace) -> int:
    packet = load_packet(args.packet)
    price_dir = args.price_dir or _default_price_dir(packet)
    outcome = evaluate_outcome(
        packet,
        price_dir=price_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        horizon_rows=args.horizon_rows,
        cash_return=float(args.cash_return),
    )
    saved_path = write_outcome(outcome, args.output_dir) if args.save else None
    ledger_entry = None
    if args.ledger and saved_path is not None:
        ledger_entry = ingest_outcome(
            outcome,
            outcome_path=saved_path,
            ledger_dir=args.ledger_dir,
        )

    if args.json:
        payload = dict(outcome)
        if saved_path is not None:
            payload["artifact_path"] = str(saved_path)
        if ledger_entry is not None:
            payload["ledger_entry"] = ledger_entry
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if outcome["scorecard"]["ok"] else 2

    scorecard = outcome["scorecard"]
    returns = outcome["returns"]
    primary = outcome["primary_pick"]
    risk = outcome["risk"]
    attribution = outcome["attribution"]
    sentiment = outcome["sentiment"]
    drivers = attribution["drivers"]
    active_excess = attribution["active_excess"]
    marker = "PASS" if scorecard["ok"] else "FAIL"
    print(f"Outcome: {marker}")
    print(f"Run: {outcome['run_id']}")
    print(
        "Window: "
        f"{outcome['window']['start_date']} -> {outcome['window']['end_date']} "
        f"({outcome['window']['horizon_rows']} rows)"
    )
    print(
        "Primary: "
        f"{primary['ticker']} realized={returns['by_ticker'].get(primary['ticker'])} "
        f"expected={primary['expected_return']} "
        f"forecast_error={primary['forecast_error']}"
    )
    print(
        "Returns: "
        f"allocation={returns['allocation']} "
        f"equal_weight={returns['equal_weight']} "
        f"cash={returns['cash']} "
        f"excess_equal={returns['excess_vs_equal_weight']} "
        f"excess_cash={returns['excess_vs_cash']}"
    )
    print(
        "Attribution: "
        f"active_positions={active_excess['from_positions']} "
        f"cash_return_contribution={active_excess['from_cash']} "
        f"cash_drag={attribution['cash']['drag_vs_equal_weight']} "
        f"top_active={drivers['top_active_contributor']} "
        f"weakest_active={drivers['weakest_active_contributor']} "
        f"largest_active_drag={drivers['largest_active_drag']}"
    )
    if sentiment.get("present"):
        weighted_alignment = sentiment["confidence_weighted_alignment"]
        weighted_alignment_text = (
            f"{weighted_alignment:.6f}"
            if isinstance(weighted_alignment, (int, float))
            else str(weighted_alignment)
        )
        print(
            "Sentiment outcome: "
            f"signal={sentiment['signal']} "
            f"score={sentiment['score']} "
            f"confidence={sentiment['confidence']} "
            f"directional_hit={sentiment['directional_hit']} "
            f"weighted_alignment={weighted_alignment_text}"
        )
    print(f"Risk: realized_max_drawdown={risk['realized_max_drawdown']}")
    if saved_path is not None:
        print(f"Saved outcome: {saved_path}")
    if ledger_entry is not None:
        print(f"Ledger outcome: {ledger_entry['run_id']} -> {ledger_entry['outcome_digest'][:12]}")
    return 0 if scorecard["ok"] else 2


def _render_regime_replay(args: argparse.Namespace) -> int:
    packet = load_packet(args.packet)
    report = evaluate_packet_regimes(
        packet,
        output_dir=args.output_dir,
        rows=int(args.rows),
        start_date=args.start_date,
        cash_return=float(args.cash_return),
        max_drawdown=float(args.max_drawdown),
    )
    saved_path = write_regime_replay(report, args.output_dir)
    ledger_entry = None
    if args.ledger and saved_path is not None:
        ledger_entry = ingest_regime_replay(
            report,
            regime_path=saved_path,
            ledger_dir=args.ledger_dir,
        )
    payload = dict(report)
    payload["artifact_path"] = str(saved_path)
    if ledger_entry is not None:
        payload["ledger_entry"] = ledger_entry
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report["summary"]["ok"] else 2

    summary = report["summary"]
    state = "PASS" if summary["ok"] else "FAIL"
    print(f"Regime replay: {state}")
    print(f"Run: {report['run_id']}")
    print(f"Primary: {report['primary_ticker']}")
    print(
        "Regimes: "
        f"count={summary['regime_count']} "
        f"scorecard_pass={summary['scorecard_pass_count']} "
        f"fragile={summary['fragile_count']}"
    )
    print(
        "Worst case: "
        f"excess_cash={summary['worst_excess_vs_cash']} "
        f"excess_equal={summary['worst_excess_vs_equal_weight']} "
        f"drawdown={summary['worst_drawdown']} "
        f"max_drawdown={summary['max_drawdown']}"
    )
    for regime in report["regimes"]:
        fragility = regime["fragility"]
        returns = regime["returns"]
        risk = regime["risk"]
        primary = regime["primary_pick"]
        marker = "PASS" if fragility["ok"] else "FAIL"
        print(
            f"- {regime['name']}: {marker} "
            f"allocation={returns['allocation']} "
            f"excess_cash={returns['excess_vs_cash']} "
            f"excess_equal={returns['excess_vs_equal_weight']} "
            f"drawdown={risk['realized_max_drawdown']} "
            f"primary_return={primary['realized_return']}"
        )
        if fragility["reasons"]:
            print(f"  blockers: {'; '.join(fragility['reasons'])}")
    print(f"Saved regime replay: {saved_path}")
    if ledger_entry is not None:
        print(f"Ledger regime replay: {ledger_entry['run_id']} -> {ledger_entry['report_digest'][:12]}")
    return 0 if summary["ok"] else 2


def _render_fixtures(args: argparse.Namespace) -> int:
    if args.fixtures_command == "audit":
        namespace_root = args.namespace_root.expanduser().resolve()
        price_dir = args.price_dir or default_price_fixture_dir(namespace_root)
        report = audit_fixture_universe(
            price_dir=price_dir,
            tickers=list(args.tickers) if args.tickers else None,
            sector_map=args.sector_map,
            min_tickers=int(args.min_tickers),
            min_rows=int(args.min_rows),
            min_common_dates=int(args.min_common_dates),
            min_sectors=int(args.min_sectors),
            max_pairwise_abs_correlation=float(args.max_pairwise_correlation),
        )
        saved_path = write_fixture_universe_report(report, args.output_dir) if args.save else None
        payload = dict(report)
        if saved_path is not None:
            payload["artifact_path"] = str(saved_path)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if report["summary"]["ok"] else 2

        summary = report["summary"]
        state = "READY" if summary["ok"] else "NOT READY"
        print(f"Fixture universe: {state}")
        print(f"Price dir: {report['price_dir']}")
        print(
            "Coverage: "
            f"tickers={summary['ticker_count']}/{report['parameters']['min_tickers']} "
            f"common_dates={summary['common_date_count']}/{report['parameters']['min_common_dates']} "
            f"known_sectors={summary['known_sector_count']}/{report['parameters']['min_sectors']}"
        )
        print(f"Sectors: {summary['sector_counts']}")
        print(
            "Correlation: "
            f"max_abs={summary['max_abs_correlation']} "
            f"pair={summary['max_abs_correlation_pair']} "
            f"limit={report['parameters']['max_pairwise_abs_correlation']}"
        )
        if saved_path is not None:
            print(f"Saved fixture audit: {saved_path}")
        if summary["blockers"]:
            print("Blockers:")
            for blocker in summary["blockers"]:
                print(f"- {blocker}")
        return 0 if summary["ok"] else 2
    if args.fixtures_command == "refresh":
        namespace_root = args.namespace_root.expanduser().resolve()
        price_dir = args.price_dir or default_price_fixture_dir(namespace_root)
        scoped_tickers = list(args.tickers) if args.tickers else list(DEFAULT_THESIS_TICKERS)
        report = refresh_fixture_universe(
            price_dir=price_dir,
            tickers=scoped_tickers,
            source=str(args.source),
            source_dir=args.source_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            timeout=float(args.timeout),
            verify_tls=bool(args.verify_tls),
            run_audit=bool(args.audit),
            sector_map=args.sector_map,
            min_tickers=int(args.min_tickers),
            min_rows=int(args.min_rows),
            min_common_dates=int(args.min_common_dates),
            min_sectors=int(args.min_sectors),
            max_pairwise_abs_correlation=float(args.max_pairwise_correlation),
        )
        saved_path = write_fixture_refresh_report(report, args.output_dir) if args.save else None
        payload = dict(report)
        if saved_path is not None:
            payload["artifact_path"] = str(saved_path)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if report["summary"]["ok"] else 2

        summary = report["summary"]
        state = "READY" if summary["ok"] else "NOT READY"
        print(f"Fixture refresh: {state}")
        print(f"Source: {report['source']}")
        print(f"Price dir: {report['price_dir']}")
        print(
            "Refresh: "
            f"tickers={summary['ticker_count']} "
            f"written={summary['refreshed_count']} "
            f"failed={summary['failed_count']} "
            f"audit_ok={summary['audit_ok']}"
        )
        for row in report["fixtures"]:
            status = "ok" if row.get("ok") else "fail"
            detail = f"rows={row.get('rows')} {row.get('first_date')}->{row.get('last_date')}"
            if not row.get("ok"):
                detail = str(row.get("error"))
            print(f"- {status}: {row['ticker']} | {detail}")
        if saved_path is not None:
            print(f"Saved fixture refresh: {saved_path}")
        if summary["blockers"]:
            print("Blockers:")
            for blocker in summary["blockers"]:
                print(f"- {blocker}")
        return 0 if summary["ok"] else 2
    return 1


def _load_policy_arg(args: argparse.Namespace) -> dict[str, Any]:
    return load_trust_policy(getattr(args, "trust_policy", None))


def _load_gates_arg(args: argparse.Namespace) -> dict[str, Any]:
    return load_promotion_gates(
        getattr(args, "gates", None),
        disabled=bool(getattr(args, "no_gates", False)),
    )


def _flag_provided(args: argparse.Namespace, *flags: str) -> bool:
    raw_argv = list(getattr(args, "_argv", []))
    return any(
        token == flag or token.startswith(f"{flag}=")
        for token in raw_argv
        for flag in flags
    )


def _gated_min_runs(args: argparse.Namespace, gates: dict[str, Any]) -> int:
    if _flag_provided(args, "--min-runs"):
        return int(args.min_runs)
    if gates.get("loaded") and gates.get("min_runs") is not None:
        return int(gates["min_runs"])
    return int(args.min_runs)


def _gated_min_outcomes(args: argparse.Namespace, gates: dict[str, Any]) -> int:
    if _flag_provided(args, "--min-outcomes"):
        return int(args.min_outcomes)
    outcomes = gates.get("outcomes", {}) if isinstance(gates.get("outcomes"), dict) else {}
    if gates.get("loaded") and outcomes.get("min_outcomes") is not None:
        return int(outcomes["min_outcomes"])
    return int(args.min_outcomes)


def _gated_min_regime_replays(args: argparse.Namespace, gates: dict[str, Any]) -> int:
    if _flag_provided(args, "--min-regime-replays"):
        return int(args.min_regime_replays)
    regimes = gates.get("regimes", {}) if isinstance(gates.get("regimes"), dict) else {}
    if gates.get("loaded") and regimes.get("min_regime_replays") is not None:
        return int(regimes["min_regime_replays"])
    return int(args.min_regime_replays)


def _gated_value(
    args: argparse.Namespace,
    gates: dict[str, Any],
    gate_key: str,
    candidates: list[tuple[str, tuple[str, ...]]],
) -> Any:
    for attr, flags in candidates:
        if hasattr(args, attr) and _flag_provided(args, *flags):
            return getattr(args, attr)
    outcomes = gates.get("outcomes", {}) if isinstance(gates.get("outcomes"), dict) else {}
    if gates.get("loaded") and gate_key in outcomes:
        return outcomes[gate_key]
    for attr, _ in candidates:
        if hasattr(args, attr):
            return getattr(args, attr)
    return None


def _gated_regime_value(
    args: argparse.Namespace,
    gates: dict[str, Any],
    gate_key: str,
    candidates: list[tuple[str, tuple[str, ...]]],
) -> Any:
    for attr, flags in candidates:
        if hasattr(args, attr) and _flag_provided(args, *flags):
            return getattr(args, attr)
    regimes = gates.get("regimes", {}) if isinstance(gates.get("regimes"), dict) else {}
    if gates.get("loaded") and gate_key in regimes:
        return regimes[gate_key]
    for attr, _ in candidates:
        if hasattr(args, attr):
            return getattr(args, attr)
    return None


def _outcome_thresholds(
    args: argparse.Namespace,
    gates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoped_gates = gates or {}
    thresholds = {
        "min_ok_rate": _gated_value(
            args,
            scoped_gates,
            "min_ok_rate",
            [
                ("min_outcome_ok_rate", ("--min-outcome-ok-rate",)),
                ("min_ok_rate", ("--min-ok-rate",)),
            ],
        ),
        "min_avg_excess_cash": _gated_value(
            args,
            scoped_gates,
            "min_avg_excess_cash",
            [
                ("min_outcome_excess_cash", ("--min-outcome-excess-cash",)),
                ("min_excess_cash", ("--min-excess-cash",)),
            ],
        ),
        "min_avg_excess_equal_weight": _gated_value(
            args,
            scoped_gates,
            "min_avg_excess_equal_weight",
            [
                ("min_outcome_excess_equal", ("--min-outcome-excess-equal",)),
                ("min_excess_equal", ("--min-excess-equal",)),
            ],
        ),
        "max_avg_abs_forecast_error": _gated_value(
            args,
            scoped_gates,
            "max_avg_abs_forecast_error",
            [
                ("max_outcome_forecast_error", ("--max-outcome-forecast-error",)),
                ("max_forecast_error", ("--max-forecast-error",)),
            ],
        ),
        "max_realized_drawdown": _gated_value(
            args,
            scoped_gates,
            "max_realized_drawdown",
            [
                ("max_outcome_drawdown", ("--max-outcome-drawdown",)),
                ("max_drawdown", ("--max-drawdown",)),
            ],
        ),
        "min_sentiment_directional_count": _gated_value(
            args,
            scoped_gates,
            "min_sentiment_directional_count",
            [
                ("min_outcome_sentiment_outcomes", ("--min-outcome-sentiment-outcomes",)),
                ("min_sentiment_outcomes", ("--min-sentiment-outcomes",)),
            ],
        ),
        "min_sentiment_hit_rate": _gated_value(
            args,
            scoped_gates,
            "min_sentiment_hit_rate",
            [
                ("min_outcome_sentiment_hit_rate", ("--min-outcome-sentiment-hit-rate",)),
                ("min_sentiment_hit_rate", ("--min-sentiment-hit-rate",)),
            ],
        ),
        "min_avg_sentiment_alignment": _gated_value(
            args,
            scoped_gates,
            "min_avg_sentiment_alignment",
            [
                ("min_outcome_sentiment_alignment", ("--min-outcome-sentiment-alignment",)),
                ("min_sentiment_alignment", ("--min-sentiment-alignment",)),
            ],
        ),
    }
    if thresholds["min_sentiment_directional_count"] is None:
        thresholds["min_sentiment_directional_count"] = 0
    return thresholds


def _regime_thresholds(
    args: argparse.Namespace,
    gates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoped_gates = gates or {}
    return {
        "require_latest_run": _gated_regime_value(
            args,
            scoped_gates,
            "require_latest_run",
            [
                (
                    "require_latest_regime_replay",
                    ("--require-latest-regime-replay", "--no-require-latest-regime-replay"),
                ),
            ],
        ),
        "require_ok": _gated_regime_value(
            args,
            scoped_gates,
            "require_ok",
            [
                (
                    "require_regime_ok",
                    ("--require-regime-ok", "--no-require-regime-ok"),
                ),
            ],
        ),
        "max_fragile_count": _gated_regime_value(
            args,
            scoped_gates,
            "max_fragile_count",
            [("max_regime_fragile_count", ("--max-regime-fragile-count",))],
        ),
        "max_worst_drawdown": _gated_regime_value(
            args,
            scoped_gates,
            "max_worst_drawdown",
            [("max_regime_drawdown", ("--max-regime-drawdown",))],
        ),
        "min_worst_excess_cash": _gated_regime_value(
            args,
            scoped_gates,
            "min_worst_excess_cash",
            [("min_regime_excess_cash", ("--min-regime-excess-cash",))],
        ),
        "min_worst_excess_equal_weight": _gated_regime_value(
            args,
            scoped_gates,
            "min_worst_excess_equal_weight",
            [("min_regime_excess_equal", ("--min-regime-excess-equal",))],
        ),
    }


def _entry_trust_payload(
    entry: dict[str, Any],
    ledger_dir: Path,
    *,
    trust_policy: dict[str, Any],
) -> dict[str, Any]:
    trust = entry.get("repo_trust")
    if not isinstance(trust, dict) or not trust.get("adapters"):
        try:
            trust = build_repo_trust(load_ledger_packet(str(entry["run_id"]), ledger_dir))
        except Exception:
            trust = {
                "adapter_count": 0,
                "dirty_count": 0,
                "dirty_details": [],
                "adapters": [],
            }
    return {
        "run_id": entry.get("run_id"),
        "content_digest": entry.get("content_digest"),
        "repo_trust": trust,
        "policy_evaluation": evaluate_repo_trust(trust, trust_policy=trust_policy),
    }


def _render_trust_payload(payload: dict[str, Any]) -> None:
    trust = payload.get("repo_trust", {})
    policy_evaluation = payload.get("policy_evaluation", {})
    adapters = trust.get("adapters", []) if isinstance(trust, dict) else []
    print(f"Trust audit: {payload.get('run_id')}")
    print(f"Digest: {str(payload.get('content_digest') or '')[:12]}")
    if isinstance(policy_evaluation, dict):
        policy = policy_evaluation.get("policy", {})
        print(
            "Policy: "
            f"loaded={policy.get('loaded')} "
            f"digest={str(policy.get('digest') or '')[:12]} "
            f"allowed={policy_evaluation.get('allowed_change_count')} "
            f"blocking={policy_evaluation.get('blocking_change_count')}"
        )
    if not isinstance(adapters, list) or not adapters:
        print("No adapter trust metadata.")
        return
    for adapter in adapters:
        if not isinstance(adapter, dict):
            continue
        dirty = adapter.get("repo_dirty")
        marker = "dirty" if dirty is True else "clean" if dirty is False else "unknown"
        sha = str(adapter.get("repo_sha") or "unknown")
        short_sha = sha[:12] if sha != "unknown" else sha
        branch = adapter.get("repo_branch") or "unknown"
        count = adapter.get("repo_status_count")
        status_lines = adapter.get("repo_status", [])
        status_lines = status_lines if isinstance(status_lines, list) else []
        print(
            f"- {adapter.get('name')}: {marker} | "
            f"branch={branch} | sha={short_sha} | changes={count}"
        )
        if adapter.get("repo_path"):
            print(f"  path: {adapter.get('repo_path')}")
        for line in status_lines[:10]:
            print(f"  {line}")
        if adapter.get("repo_status_truncated") or (
            isinstance(count, int) and count > len(status_lines[:10])
        ):
            print(f"  ... {count} total changes")
    if isinstance(policy_evaluation, dict) and policy_evaluation.get("blocking_changes"):
        print("Blocking changes:")
        for change in policy_evaluation["blocking_changes"][:20]:
            if not isinstance(change, dict):
                continue
            path = change.get("path") or "<repo>"
            print(
                f"- {change.get('repo')}:{path} "
                f"[{change.get('status') or 'dirty'}] {change.get('reason')}"
            )
    if isinstance(policy_evaluation, dict) and policy_evaluation.get("allowed_changes"):
        print("Policy-allowed changes:")
        for change in policy_evaluation["allowed_changes"][:20]:
            if not isinstance(change, dict):
                continue
            path = change.get("path") or "<repo>"
            rule = change.get("rule") or "unnamed"
            print(f"- {change.get('repo')}:{path} [{change.get('status')}] rule={rule}")


def _build_production_verification(args: argparse.Namespace) -> dict[str, Any]:
    namespace_root = args.namespace_root.expanduser().resolve()
    price_dir = args.price_dir or default_price_fixture_dir(namespace_root)
    entries = read_ledger_entries(args.ledger_dir)
    outcome_entries = read_outcome_entries(args.ledger_dir)
    regime_entries = read_regime_entries(args.ledger_dir)
    trust_policy = _load_policy_arg(args)
    promotion_gates = _load_gates_arg(args)
    gates_summary = promotion_gates_summary(promotion_gates)
    latest_run_id = str(entries[-1].get("run_id")) if entries else None

    fixture_parameters = {
        "tickers": list(DEFAULT_THESIS_TICKERS),
        "min_tickers": int(args.min_tickers),
        "min_rows": int(args.min_rows),
        "min_common_dates": int(args.min_common_dates),
        "min_sectors": int(args.min_sectors),
        "max_pairwise_abs_correlation": float(args.max_pairwise_correlation),
    }
    try:
        fixture_report = audit_fixture_universe(
            price_dir=price_dir,
            tickers=DEFAULT_THESIS_TICKERS,
            sector_map=args.sector_map,
            min_tickers=fixture_parameters["min_tickers"],
            min_rows=fixture_parameters["min_rows"],
            min_common_dates=fixture_parameters["min_common_dates"],
            min_sectors=fixture_parameters["min_sectors"],
            max_pairwise_abs_correlation=fixture_parameters["max_pairwise_abs_correlation"],
        )
    except Exception as exc:
        fixture_report = {
            "price_dir": str(price_dir.expanduser().resolve()),
            "parameters": fixture_parameters,
            "summary": {
                "ok": False,
                "ticker_count": 0,
                "requested_ticker_count": len(DEFAULT_THESIS_TICKERS),
                "sector_count": 0,
                "known_sector_count": 0,
                "sector_counts": {},
                "common_date_count": 0,
                "first_common_date": None,
                "last_common_date": None,
                "max_abs_correlation": None,
                "max_abs_correlation_pair": None,
                "blockers": [f"fixture audit failed: {type(exc).__name__}: {exc}"],
            },
            "fixtures": [],
            "correlations": {"return_observation_count": 0, "matrix": {}, "pairs": []},
        }
    outcome_report = build_outcome_report(
        outcome_entries,
        min_outcomes_for_promotion=_gated_min_outcomes(args, promotion_gates),
        **_outcome_thresholds(args, promotion_gates),
    )
    outcome_report["promotion"]["gates"] = gates_summary
    regime_report = build_regime_report(
        regime_entries,
        latest_run_id=latest_run_id,
        min_regime_replays_for_promotion=_gated_min_regime_replays(args, promotion_gates),
        **_regime_thresholds(args, promotion_gates),
    )
    regime_report["promotion"]["gates"] = gates_summary
    ledger_report = build_ledger_report(
        entries,
        min_runs_for_promotion=_gated_min_runs(args, promotion_gates),
        trust_policy=trust_policy,
        promotion_attempts=read_promotion_attempts(args.promotions_dir),
        outcome_entries=outcome_entries,
        min_outcomes_for_promotion=_gated_min_outcomes(args, promotion_gates),
        outcome_thresholds=_outcome_thresholds(args, promotion_gates),
        regime_entries=regime_entries,
        min_regime_replays_for_promotion=_gated_min_regime_replays(args, promotion_gates),
        regime_thresholds=_regime_thresholds(args, promotion_gates),
    )
    ledger_report["promotion"]["gates"] = gates_summary

    platform_export: dict[str, Any]
    platform_import: dict[str, Any]
    try:
        manifest, paths = write_platform_export(
            ledger_entries=entries,
            outcome_entries=outcome_entries,
            regime_entries=regime_entries,
            ledger_dir=args.ledger_dir,
            output_dir=args.platform_output_dir,
            promotions_dir=args.promotions_dir,
            signing_key_file=args.platform_signing_key_file,
            target="research-run-platform",
        )
        validation = validate_platform_export(
            paths["export_dir"],
            require_artifacts=not args.allow_missing_artifacts,
            require_signature=args.require_platform_signature,
            signing_key_file=args.platform_signing_key_file,
            target="research-run-platform",
        )
        platform_export = {
            "ok": bool(validation["ok"]),
            "manifest": manifest,
            "paths": {key: str(value) for key, value in paths.items()},
            "validation": validation,
        }
        bundle = load_platform_export_bundle(
            paths["export_dir"],
            require_artifacts=not args.allow_missing_artifacts,
            require_signature=args.require_platform_signature,
            signing_key_file=args.platform_signing_key_file,
            target="research-run-platform",
        )
        platform_import = bundle
    except Exception as exc:
        platform_export = {
            "ok": False,
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
        platform_import = {
            "ok": False,
            "errors": ["platform export failed before import validation"],
        }

    component_ready = {
        "fixtures": bool(fixture_report["summary"]["ok"]),
        "outcomes": bool(outcome_report["promotion"]["ready"]),
        "regimes": bool(regime_report["promotion"]["ready"]),
        "ledger": bool(ledger_report["promotion"]["ready"]),
        "platform_export": bool(platform_export.get("ok")),
        "platform_import": bool(platform_import.get("ok")),
    }
    blockers: list[str] = []
    if not component_ready["fixtures"]:
        blockers.extend(
            f"fixtures: {blocker}" for blocker in fixture_report["summary"]["blockers"]
        )
    if not component_ready["outcomes"]:
        blockers.extend(
            f"outcomes: {blocker}" for blocker in outcome_report["promotion"]["blockers"]
        )
    if not component_ready["regimes"]:
        blockers.extend(
            f"regimes: {blocker}" for blocker in regime_report["promotion"]["blockers"]
        )
    if not component_ready["ledger"]:
        blockers.extend(
            f"ledger: {blocker}" for blocker in ledger_report["promotion"]["blockers"]
        )
    if not component_ready["platform_export"]:
        validation = platform_export.get("validation")
        errors = (
            validation.get("errors", [])
            if isinstance(validation, dict)
            else platform_export.get("errors", [])
        )
        blockers.extend(f"platform_export: {error}" for error in errors)
    if not component_ready["platform_import"]:
        blockers.extend(
            f"platform_import: {error}"
            for error in platform_import.get("errors", [])
        )

    return {
        "ok": all(component_ready.values()),
        "component_ready": component_ready,
        "blockers": blockers,
        "price_dir": str(price_dir.expanduser().resolve()),
        "ledger_dir": str(args.ledger_dir.expanduser().resolve()),
        "promotion_gates": gates_summary,
        "fixtures": fixture_report,
        "outcomes": outcome_report,
        "regimes": regime_report,
        "ledger": ledger_report,
        "platform_export": platform_export,
        "platform_import": platform_import,
    }


def _render_production_verification(report: dict[str, Any]) -> None:
    state = "READY" if report["ok"] else "NOT READY"
    components = report["component_ready"]
    fixtures = report["fixtures"]["summary"]
    outcomes = report["outcomes"]
    regimes = report["regimes"]
    ledger = report["ledger"]
    platform_export = report["platform_export"]
    platform_import = report["platform_import"]
    print(f"Production verification: {state}")
    print(f"Price dir: {report['price_dir']}")
    print(
        "Components: "
        + " ".join(
            f"{name}={'ok' if ok else 'fail'}"
            for name, ok in components.items()
        )
    )
    print(
        "Fixtures: "
        f"tickers={fixtures['ticker_count']}/{report['fixtures']['parameters']['min_tickers']} "
        f"common_dates={fixtures['common_date_count']} "
        f"sectors={fixtures['known_sector_count']} "
        f"max_abs_corr={fixtures['max_abs_correlation']}"
    )
    print(
        "Outcomes: "
        f"ready={outcomes['promotion']['ready']} "
        f"count={outcomes['outcome_count']} "
        f"ok_rate={outcomes['scorecard']['ok_rate']:.2f} "
        f"avg_excess_cash={outcomes['returns']['excess_vs_cash']['avg']}"
    )
    latest_regime = regimes["latest_summary"]
    print(
        "Regimes: "
        f"ready={regimes['promotion']['ready']} "
        f"count={regimes['replay_count']} "
        f"latest_ok={latest_regime.get('ok')} "
        f"fragile={latest_regime.get('fragile_count')}"
    )
    print(
        "Ledger: "
        f"ready={ledger['promotion']['ready']} "
        f"runs={ledger['run_count']} "
        f"latest={ledger['latest_run_id']} "
        f"blockers={len(ledger['promotion']['blockers'])}"
    )
    export_validation = platform_export.get("validation", {})
    artifact_counts = (
        export_validation.get("artifact_counts", {})
        if isinstance(export_validation, dict)
        else {}
    )
    signature = (
        export_validation.get("signature", {})
        if isinstance(export_validation, dict)
        else {}
    )
    print(
        "Platform: "
        f"export_ok={platform_export.get('ok')} "
        f"import_ok={platform_import.get('ok')} "
        f"artifacts={artifact_counts.get('verified')}/{artifact_counts.get('total')} "
        f"signature={'verified' if signature.get('verified') else 'unverified'}"
    )
    gates = report["promotion_gates"]
    print(
        "Promotion gates: "
        f"loaded={gates['loaded']} "
        f"digest={gates['digest'][:12]} "
        f"min_outcomes={gates['min_outcomes']} "
        f"min_regime_replays={gates['min_regime_replays']}"
    )
    if report["blockers"]:
        print("Blockers:")
        for blocker in report["blockers"]:
            print(f"- {blocker}")


def _render_outcome_report(report: dict[str, Any]) -> None:
    promotion = report["promotion"]
    scorecard = report["scorecard"]
    returns = report["returns"]
    calibration = report["calibration"]
    risk = report["risk"]
    attribution = report["attribution"]
    sentiment = report["sentiment"]
    state = "READY" if promotion["ready"] else "NOT READY"
    print(f"Outcome report: {state}")
    print(f"Outcomes: {report['outcome_count']} latest={report['latest_run_id']}")
    print(
        "Scorecard: "
        f"ok_rate={scorecard['ok_rate']:.2f} "
        f"beat_cash={scorecard['beat_cash_rate']:.2f} "
        f"beat_equal={scorecard['beat_equal_weight_rate']:.2f} "
        f"hit_rate={scorecard['primary_hit_rate']:.2f}"
    )
    print(
        "Returns: "
        f"avg_allocation={returns['allocation']['avg']} "
        f"avg_excess_cash={returns['excess_vs_cash']['avg']} "
        f"avg_excess_equal={returns['excess_vs_equal_weight']['avg']}"
    )
    print(
        "Calibration: "
        f"avg_error={calibration['forecast_error']['avg']} "
        f"avg_abs_error={calibration['absolute_forecast_error']['avg']}"
    )
    print(
        "Attribution: "
        f"avg_active_positions={attribution['active_excess']['from_positions']['avg']} "
        f"avg_cash_return_contribution={attribution['active_excess']['from_cash']['avg']} "
        f"avg_cash_drag={attribution['cash']['drag_vs_equal_weight']['avg']}"
    )
    print(
        "Sentiment outcomes: "
        f"present={sentiment['present_count']} "
        f"directional={sentiment['directional_count']} "
        f"hit_rate={sentiment['directional_hit_rate']:.2f} "
        f"avg_alignment={sentiment['confidence_weighted_alignment']['avg']}"
    )
    print(f"Risk: max_drawdown={risk['realized_max_drawdown']['max']}")
    print(f"Thresholds: {promotion['thresholds']}")
    gates = promotion.get("gates")
    if isinstance(gates, dict):
        print(
            "Promotion gates: "
            f"loaded={gates.get('loaded')} "
            f"digest={str(gates.get('digest') or '')[:12]} "
            f"source={gates.get('source_path')} "
            f"min_outcomes={gates.get('min_outcomes')}"
        )
    if promotion["blockers"]:
        print("Blockers:")
        for blocker in promotion["blockers"]:
            print(f"- {blocker}")


def _render_regime_report(report: dict[str, Any]) -> None:
    promotion = report["promotion"]
    summary = report.get("latest_summary", {})
    fragility = report["fragility"]
    returns = report["returns"]
    risk = report["risk"]
    state = "READY" if promotion["ready"] else "NOT READY"
    print(f"Regime report: {state}")
    print(
        "Replays: "
        f"{report['replay_count']} latest={report['latest_run_id']} "
        f"matches_latest_run={report['latest_matches_run']}"
    )
    print(
        "Latest: "
        f"ok={summary.get('ok')} "
        f"fragile={summary.get('fragile_count')} "
        f"fragile_regimes={summary.get('fragile_regimes')} "
        f"worst_drawdown={summary.get('worst_drawdown')} "
        f"worst_excess_cash={summary.get('worst_excess_vs_cash')} "
        f"worst_excess_equal={summary.get('worst_excess_vs_equal_weight')}"
    )
    print(
        "History: "
        f"ok_rate={report['scorecard']['ok_rate']:.2f} "
        f"avg_fragile={fragility['fragile_count']['avg']} "
        f"worst_drawdown={risk['worst_drawdown']['max']} "
        f"worst_excess_cash={returns['worst_excess_vs_cash']['min']} "
        f"worst_excess_equal={returns['worst_excess_vs_equal_weight']['min']}"
    )
    print(f"Failed regimes: {fragility['failed_regime_counts']}")
    print(f"Thresholds: {promotion['thresholds']}")
    gates = promotion.get("gates")
    if isinstance(gates, dict):
        print(
            "Promotion gates: "
            f"loaded={gates.get('loaded')} "
            f"digest={str(gates.get('digest') or '')[:12]} "
            f"source={gates.get('source_path')} "
            f"min_regime_replays={gates.get('min_regime_replays')}"
        )
    if promotion["blockers"]:
        print("Blockers:")
        for blocker in promotion["blockers"]:
            print(f"- {blocker}")


def _render_ledger(args: argparse.Namespace) -> int:
    if args.ledger_command == "ingest":
        packet = load_packet(args.packet)
        entry = ingest_packet(
            packet,
            packet_path=args.packet,
            ledger_dir=args.ledger_dir,
        )
        if args.json:
            print(json.dumps(entry, indent=2, sort_keys=True))
        else:
            print(f"Ingested {entry['run_id']} -> {entry['content_digest'][:12]}")
        return 0

    if args.ledger_command == "list":
        entries = read_ledger_entries(args.ledger_dir)
        if args.limit >= 0:
            entries = entries[-args.limit :] if args.limit else []
        if args.json:
            print(json.dumps(entries, indent=2, sort_keys=True))
        else:
            for entry in reversed(entries):
                top_loop = entry.get("top_loop", {})
                primary = entry.get("primary_pick", {})
                backtest = entry.get("backtest", {})
                print(
                    f"{entry.get('run_id')} | eval={entry.get('eval_score')} | "
                    f"top={top_loop.get('repo')} | pick={primary.get('ticker')} | "
                    f"bt_cash={backtest.get('excess_return_vs_cash')} | "
                    f"digest={str(entry.get('content_digest', ''))[:12]}"
                )
        return 0

    if args.ledger_command == "show":
        payload = (
            load_ledger_packet(args.run_id, args.ledger_dir)
            if args.packet
            else get_ledger_entry(args.run_id, args.ledger_dir)
        )
        if args.json or args.packet:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            top_loop = payload.get("top_loop", {})
            primary = payload.get("primary_pick", {})
            backtest = payload.get("backtest", {})
            stress = payload.get("stress", {})
            print(f"Run: {payload.get('run_id')}")
            print(f"Created: {payload.get('created_at')}")
            print(f"Digest: {payload.get('content_digest')}")
            print(f"Eval: {payload.get('eval_ok')} score={payload.get('eval_score')}")
            print(f"Top loop: {top_loop.get('repo')} / {top_loop.get('name')}")
            print(f"Primary pick: {primary.get('ticker')} weight={primary.get('weight')}")
            print(
                "Backtest: "
                f"return={backtest.get('strategy_total_return')} "
                f"excess_cash={backtest.get('excess_return_vs_cash')} "
                f"drawdown={backtest.get('strategy_max_drawdown')}"
            )
            print(
                "Stress: "
                f"ok={stress.get('ok')} "
                f"worst_margin={stress.get('worst_margin')}"
            )
        return 0

    if args.ledger_command == "report":
        entries = read_ledger_entries(args.ledger_dir)
        outcome_entries = read_outcome_entries(args.ledger_dir)
        regime_entries = read_regime_entries(args.ledger_dir)
        trust_policy = _load_policy_arg(args)
        promotion_gates = _load_gates_arg(args)
        report = build_ledger_report(
            entries,
            min_runs_for_promotion=_gated_min_runs(args, promotion_gates),
            trust_policy=trust_policy,
            promotion_attempts=read_promotion_attempts(args.promotions_dir),
            outcome_entries=outcome_entries,
            min_outcomes_for_promotion=_gated_min_outcomes(args, promotion_gates),
            outcome_thresholds=_outcome_thresholds(args, promotion_gates),
            regime_entries=regime_entries,
            min_regime_replays_for_promotion=_gated_min_regime_replays(args, promotion_gates),
            regime_thresholds=_regime_thresholds(args, promotion_gates),
        )
        report["promotion"]["gates"] = promotion_gates_summary(promotion_gates)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            promotion = report["promotion"]
            backtest = report["backtest"]
            stress = report["stress"]
            sentiment = report["sentiment"]
            picks = report["primary_picks"]
            trust = report["trust"]
            outcomes = report["outcomes"]
            regimes = report["regimes"]
            promotion_attempts = report["promotion_attempts"]
            outcome_attribution = outcomes["attribution"]
            outcome_sentiment = outcomes["sentiment"]
            state = "READY" if promotion["ready"] else "NOT READY"
            print(f"Ledger report: {state}")
            print(f"Runs: {report['run_count']} latest={report['latest_run_id']}")
            print(
                "Eval: "
                f"ok_rate={report['eval']['ok_rate']:.2f} "
                f"avg_score={report['eval']['score']['avg']}"
            )
            print(
                "Backtest: "
                f"avg_excess_cash={backtest['excess_return_vs_cash']['avg']} "
                f"positive_cash_rate={backtest['positive_excess_cash_rate']:.2f} "
                f"max_drawdown={backtest['strategy_max_drawdown']['max']}"
            )
            print(
                "Stress: "
                f"ok_rate={stress['ok_rate']:.2f} "
                f"worst_margin={stress['worst_margin']['min']}"
            )
            latest_sentiment = sentiment.get("latest", {})
            print(
                "Sentiment: "
                f"ok_rate={sentiment['ok_rate']:.2f} "
                f"avg_score={sentiment['score']['avg']} "
                f"avg_confidence={sentiment['confidence']['avg']} "
                f"latest_signal={latest_sentiment.get('signal') if isinstance(latest_sentiment, dict) else None}"
            )
            print(
                "Primary picks: "
                f"most_common={picks['most_common']} "
                f"share={picks['most_common_share']:.2f} "
                f"counts={picks['counts']}"
            )
            print(
                "Trust: "
                f"dirty_run_rate={trust['dirty_run_rate']:.2f} "
                f"dirty_repos={trust['dirty_repos']} "
                f"allowed={trust['latest_allowed_change_count']} "
                f"blocking={trust['latest_blocking_change_count']}"
            )
            print(
                "Outcomes: "
                f"count={outcomes['outcome_count']} "
                f"ok_rate={outcomes['scorecard']['ok_rate']:.2f} "
                f"avg_excess_cash={outcomes['returns']['excess_vs_cash']['avg']} "
                f"avg_abs_forecast_error={outcomes['calibration']['absolute_forecast_error']['avg']} "
                f"avg_active_positions={outcome_attribution['active_excess']['from_positions']['avg']} "
                f"avg_cash_drag={outcome_attribution['cash']['drag_vs_equal_weight']['avg']} "
                f"sentiment_hit_rate={outcome_sentiment['directional_hit_rate']:.2f}"
            )
            regime_latest = regimes["latest_summary"]
            print(
                "Regimes: "
                f"count={regimes['replay_count']} "
                f"latest_ok={regime_latest.get('ok')} "
                f"matches_latest_run={regimes['latest_matches_run']} "
                f"fragile={regime_latest.get('fragile_count')} "
                f"worst_drawdown={regime_latest.get('worst_drawdown')} "
                f"worst_excess_cash={regime_latest.get('worst_excess_vs_cash')}"
            )
            policy = trust["latest_policy_evaluation"]["policy"]
            print(
                "Trust policy: "
                f"loaded={policy['loaded']} "
                f"digest={policy['digest'][:12]} "
                f"source={policy['source_path']}"
            )
            gates = promotion["gates"]
            print(
                "Promotion gates: "
                f"loaded={gates['loaded']} "
                f"digest={gates['digest'][:12]} "
                f"source={gates['source_path']} "
                f"min_outcomes={gates['min_outcomes']} "
                f"min_regime_replays={gates['min_regime_replays']}"
            )
            latest_attempt = promotion_attempts["latest"]
            print(
                "Promotion attempts: "
                f"count={promotion_attempts['attempt_count']} "
                f"blocked={promotion_attempts['blocked_count']} "
                f"promoted={promotion_attempts['promoted_count']} "
                f"promotion_rate={promotion_attempts['promotion_rate']:.2f} "
                f"recent_rate={promotion_attempts['recent']['promotion_rate']:.2f} "
                f"latest={latest_attempt.get('promotion_id')} "
                f"latest_status={latest_attempt.get('status')}"
            )
            top_attempt_categories = promotion_attempts["categories"]["top"]
            if top_attempt_categories:
                print("Top promotion blocker categories:")
                for row in top_attempt_categories[:5]:
                    latest_category = row.get("latest", {})
                    print(
                        f"- {row['count']}x "
                        f"recent={row['recent_count']}x "
                        f"latest_run={latest_category.get('run_id')} "
                        f"{row['category']}"
                    )
            top_attempt_blockers = promotion_attempts["blockers"]["top"]
            if top_attempt_blockers:
                print("Top promotion attempt blockers:")
                for row in top_attempt_blockers[:5]:
                    latest_blocker = row.get("latest", {})
                    print(
                        f"- {row['count']}x "
                        f"share={row['share_of_attempts']:.2f} "
                        f"recent={row['recent_count']}x "
                        f"category={row['category']} "
                        f"latest_run={latest_blocker.get('run_id')} "
                        f"{row['blocker']}"
                    )
            latest_dirty_details = trust.get("latest_dirty_details", [])
            if latest_dirty_details:
                print("Latest dirty repos:")
                for detail in latest_dirty_details:
                    if not isinstance(detail, dict):
                        continue
                    sha = str(detail.get("repo_sha") or "unknown")
                    short_sha = sha[:12] if sha != "unknown" else sha
                    print(
                        f"- {detail.get('name')}: "
                        f"branch={detail.get('repo_branch') or 'unknown'} "
                        f"sha={short_sha} "
                        f"changes={detail.get('repo_status_count')}"
                    )
                    status_lines = detail.get("repo_status", [])
                    if isinstance(status_lines, list):
                        for line in status_lines[:5]:
                            print(f"  {line}")
            if promotion["blockers"]:
                print("Blockers:")
                for blocker in promotion["blockers"]:
                    print(f"- {blocker}")
        return 0 if report["promotion"]["ready"] else 2

    if args.ledger_command == "outcomes":
        entries = read_outcome_entries(args.ledger_dir)
        promotion_gates = _load_gates_arg(args)
        report = build_outcome_report(
            entries,
            min_outcomes_for_promotion=_gated_min_outcomes(args, promotion_gates),
            **_outcome_thresholds(args, promotion_gates),
        )
        report["promotion"]["gates"] = promotion_gates_summary(promotion_gates)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _render_outcome_report(report)
        return 0 if report["promotion"]["ready"] else 2

    if args.ledger_command == "regimes":
        entries = read_ledger_entries(args.ledger_dir)
        regime_entries = read_regime_entries(args.ledger_dir)
        latest_run_id = entries[-1].get("run_id") if entries else None
        promotion_gates = _load_gates_arg(args)
        report = build_regime_report(
            regime_entries,
            latest_run_id=str(latest_run_id) if latest_run_id else None,
            min_regime_replays_for_promotion=_gated_min_regime_replays(args, promotion_gates),
            **_regime_thresholds(args, promotion_gates),
        )
        report["promotion"]["gates"] = promotion_gates_summary(promotion_gates)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _render_regime_report(report)
        return 0 if report["promotion"]["ready"] else 2

    if args.ledger_command == "calibrate-outcomes":
        entries = read_outcome_entries(args.ledger_dir)
        report = build_outcome_calibration_report(
            entries,
            min_sample=int(args.min_sample),
            sentiment_min_sample=int(args.sentiment_min_sample),
        )
        gates_written = None
        if args.write_gates and report["ready"]:
            gates_payload = build_gates_from_calibration(
                report,
                min_runs=int(args.gate_min_runs),
            )
            gates_path = write_promotion_gates(args.gates_output, gates_payload)
            loaded_gates = load_promotion_gates(gates_path)
            gates_written = {
                "path": str(gates_path),
                "summary": promotion_gates_summary(loaded_gates),
            }
            report["written_gates"] = gates_written
        elif args.write_gates:
            report["written_gates"] = None
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            state = "READY" if report["ready"] else "INSUFFICIENT"
            recommended = report["recommended_thresholds"]
            distributions = report["distributions"]
            rates = report["rates"]
            print(f"Outcome calibration: {state}")
            print(
                "Sample: "
                f"outcomes={report['outcome_count']} "
                f"min_sample={report['min_sample']} "
                f"sentiment_directional={rates['sentiment_directional_count']}"
            )
            print(
                "Rates: "
                f"ok={rates['ok_rate']:.2f} "
                f"beat_cash={rates['beat_cash_rate']:.2f} "
                f"beat_equal={rates['beat_equal_weight_rate']:.2f} "
                f"primary_hit={rates['primary_hit_rate']:.2f}"
            )
            print(
                "Forecast error: "
                f"avg={distributions['absolute_forecast_error']['avg']} "
                f"p75={distributions['absolute_forecast_error']['p75']} "
                f"suggested_max_avg={recommended['max_avg_abs_forecast_error']}"
            )
            print(
                "Drawdown: "
                f"p95={distributions['realized_max_drawdown']['p95']} "
                f"max={distributions['realized_max_drawdown']['max']} "
                f"suggested_max={recommended['max_realized_drawdown']}"
            )
            print(
                "Excess gates: "
                f"cash={recommended['min_avg_excess_cash']} "
                f"equal={recommended['min_avg_excess_equal_weight']} "
                f"ok_rate={recommended['min_ok_rate']}"
            )
            print("Ledger report flags:")
            print(" ".join(report["ledger_report_flags"]))
            if gates_written is not None:
                summary = gates_written["summary"]
                print(
                    "Wrote gates: "
                    f"{gates_written['path']} "
                    f"digest={summary['digest'][:12]} "
                    f"min_outcomes={summary['min_outcomes']}"
                )
            elif args.write_gates:
                print("Wrote gates: no")
            if report["blockers"]:
                print("Blockers:")
                for blocker in report["blockers"]:
                    print(f"- {blocker}")
        return 0 if report["ready"] else 2

    if args.ledger_command == "backfill-outcomes":
        price_dir = (
            args.price_dir
            if args.price_dir is not None
            else args.namespace_root.expanduser().resolve() / "monte-carlo" / "sample_data"
        )
        result = backfill_ledger_outcomes(
            ledger_dir=args.ledger_dir,
            price_dir=price_dir,
            output_dir=args.output_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            horizon_rows=args.horizon_rows,
            cash_return=float(args.cash_return),
            run_ids=set(args.run_ids or []) or None,
            limit=args.limit,
            rolling=bool(args.rolling),
            stride_rows=int(args.stride_rows),
            max_windows=args.max_windows,
            dry_run=bool(args.dry_run),
        )
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            mode = "dry-run" if result["dry_run"] else "write"
            rolling = "rolling" if result["rolling"] else "single-window"
            print(f"Outcome backfill: {mode} {rolling}")
            print(
                "Rows: "
                f"runs={result['run_count']} "
                f"evaluated={result['evaluated']} "
                f"created={result['created']} "
                f"would_create={result['would_create']} "
                f"skipped={result['skipped_existing']} "
                f"failed={result['failed']}"
            )
            print(f"Price dir: {result['price_dir']}")
            print(f"Output dir: {result['output_dir']}")
            for row in result["rows"][:20]:
                window = row.get("window", {})
                window_text = (
                    f"{window.get('start_date')}->{window.get('end_date')}"
                    if isinstance(window, dict)
                    else "window=?"
                )
                detail = row.get("outcome_digest") or row.get("error")
                print(
                    f"- {row.get('status')} {row.get('run_id')} "
                    f"{window_text} {detail}"
                )
            if len(result["rows"]) > 20:
                print(f"... {len(result['rows'])} total rows")
        return 0 if result["failed"] == 0 else 2

    if args.ledger_command == "trust":
        trust_policy = _load_policy_arg(args)
        if args.run_id:
            entry = get_ledger_entry(args.run_id, args.ledger_dir)
        else:
            entries = read_ledger_entries(args.ledger_dir)
            if not entries:
                if args.json:
                    print(json.dumps({"error": "ledger has no entries"}, indent=2))
                else:
                    print("Ledger has no entries.")
                return 2
            entry = entries[-1]
        payload = _entry_trust_payload(
            entry,
            args.ledger_dir,
            trust_policy=trust_policy,
        )
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _render_trust_payload(payload)
        return 0

    if args.ledger_command == "sync":
        entries = read_ledger_entries(args.ledger_dir)
        outcome_entries = read_outcome_entries(args.ledger_dir)
        regime_entries = read_regime_entries(args.ledger_dir)
        manifest, paths = write_platform_export(
            ledger_entries=entries,
            outcome_entries=outcome_entries,
            regime_entries=regime_entries,
            ledger_dir=args.ledger_dir,
            output_dir=args.output_dir,
            promotions_dir=args.promotions_dir,
            signing_key_file=args.signing_key_file,
            target=args.target,
        )
        validation = validate_platform_export(
            paths["export_dir"],
            require_artifacts=not args.allow_missing_artifacts,
            require_signature=args.require_signature,
            signing_key_file=args.signing_key_file,
            target=args.target,
        )
        payload = {
            "manifest": manifest,
            "paths": {key: str(value) for key, value in paths.items()},
            "validation": validation,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Platform sync: {manifest['target']}")
            print(f"Export id: {manifest['export_id']}")
            print(
                "Rows: "
                f"runs={manifest['counts']['runs']} "
                f"outcomes={manifest['counts']['outcomes']} "
                f"regimes={manifest['counts']['regimes']} "
                f"promotions={manifest['counts']['promotions']}"
            )
            print(f"Bundle: {paths['export_dir']}")
            print(f"Manifest: {paths['manifest']}")
            if "manifest_signature" in paths:
                print(f"Signature: {paths['manifest_signature']}")
            artifact_counts = validation["artifact_counts"]
            signature = validation["signature"]
            print(
                "Validation: "
                f"{'ok' if validation['ok'] else 'failed'} "
                f"artifacts={artifact_counts['verified']}/{artifact_counts['total']} "
                f"signature={'verified' if signature['verified'] else 'unverified'}"
            )
            if validation["errors"]:
                print("Validation errors:")
                for error in validation["errors"]:
                    print(f"- {error}")
        return 0 if validation["ok"] else 2

    if args.ledger_command == "import":
        bundle = load_platform_export_bundle(
            args.export_dir,
            require_artifacts=not args.allow_missing_artifacts,
            require_signature=args.require_signature,
            signing_key_file=args.signing_key_file,
            target=args.target,
        )
        if args.json:
            print(json.dumps(bundle, indent=2, sort_keys=True))
        else:
            summary = bundle["summary"]
            counts = summary["counts"]
            validation = bundle["validation"]
            artifact_counts = validation["artifact_counts"]
            print(f"Platform import: {bundle['target']}")
            print(f"Bundle: {bundle['export_dir']}")
            print(
                "Rows: "
                f"runs={counts['runs']} "
                f"outcomes={counts['outcomes']} "
                f"regimes={counts['regimes']} "
                f"promotions={counts['promotions']}"
            )
            print(
                "Relationships: "
                f"runs_with_outcomes={summary['runs_with_outcomes']} "
                f"runs_with_regimes={summary['runs_with_regime_replays']} "
                f"runs_with_promotions={summary['runs_with_promotions']}"
            )
            print(
                "Latest: "
                f"run={summary['latest'].get('run_id')} "
                f"outcome={summary['latest'].get('outcome_digest')} "
                f"regime={summary['latest'].get('regime_report_digest')} "
                f"promotion={summary['latest'].get('promotion_id')}"
            )
            print(
                "Validation: "
                f"{'ok' if validation['ok'] else 'failed'} "
                f"artifacts={artifact_counts['verified']}/{artifact_counts['total']} "
                f"signature={'verified' if validation['signature']['verified'] else 'unverified'}"
            )
            print(f"Import contract: {'ok' if bundle['ok'] else 'failed'}")
            if bundle["errors"]:
                print("Import errors:")
                for error in bundle["errors"]:
                    print(f"- {error}")
        return 0 if bundle["ok"] else 2

    if args.ledger_command == "verify-production":
        report = _build_production_verification(args)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _render_production_verification(report)
        return 0 if report["ok"] else 2

    if args.ledger_command == "promote":
        trust_policy = _load_policy_arg(args)
        promotion_gates = _load_gates_arg(args)
        record, paths = promote_latest(
            ledger_dir=args.ledger_dir,
            promotions_dir=args.promotions_dir,
            min_runs=_gated_min_runs(args, promotion_gates),
            min_outcomes=_gated_min_outcomes(args, promotion_gates),
            outcome_thresholds=_outcome_thresholds(args, promotion_gates),
            min_regime_replays=_gated_min_regime_replays(args, promotion_gates),
            regime_thresholds=_regime_thresholds(args, promotion_gates),
            trust_policy=trust_policy,
            promotion_gates=promotion_gates_summary(promotion_gates),
        )
        if args.json:
            payload = dict(record)
            payload["paths"] = {
                key: str(value) if value is not None else None
                for key, value in paths.items()
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Promotion: {record['status']}")
            print(f"Run: {record.get('run_id')}")
            print(f"Attempt: {paths['attempt_path']}")
            if paths.get("canonical_path") is not None:
                print(f"Canonical: {paths['canonical_path']}")
            if record["blockers"]:
                print("Blockers:")
                for blocker in record["blockers"]:
                    print(f"- {blocker}")
        return 0 if record["status"] == "promoted" else 2

    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    args._argv = raw_argv
    if args.command is None:
        args.command = "thesis"
        args.tickers = list(DEFAULT_THESIS_TICKERS)
        args.days = 30
        args.scenarios = 100
        args.seed = 42
        args.no_run = False
        args.backtest = True
        args.backtest_lookback = 3
        args.backtest_hold = 2
        args.backtest_rebalance = 2
        args.backtest_scenarios = 20
        args.sentiment = False
        args.sentiment_days = 3
        args.sentiment_max_articles = 10
        args.sentiment_source = "auto"
        args.sentiment_half_life_hours = 24.0
        args.sentiment_include_reasons = False
        args.allocation_repair = True
        args.save = True
        args.output_dir = default_run_dir()
        args.ledger = True
        args.ledger_dir = default_ledger_dir()
        args.json = False
    if args.command == "scan":
        return _render_scan(args.namespace_root, json_output=bool(args.json))
    if args.command == "thesis":
        return _render_thesis(args)
    if args.command == "replay":
        return _render_replay(args)
    if args.command == "eval":
        return _render_eval(args)
    if args.command == "outcome":
        return _render_outcome(args)
    if args.command == "regime-replay":
        return _render_regime_replay(args)
    if args.command == "fixtures":
        return _render_fixtures(args)
    if args.command == "ledger":
        return _render_ledger(args)
    parser.print_help()
    return 1
