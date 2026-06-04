"""Command line interface for the agent harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agent_harness import __version__
from agent_harness.adapters import (
    LocalLedgerAdapter,
    MonteCarloAdapter,
    ResearchRunPlatformAdapter,
    StockSentimentAdapter,
)
from agent_harness.capital import build_capital_loops
from agent_harness.evals import evaluate_packet
from agent_harness.ledger import (
    build_repo_trust,
    default_ledger_dir,
    get_ledger_entry,
    ingest_packet,
    ingest_outcome,
    load_ledger_packet,
    read_ledger_entries,
    read_outcome_entries,
)
from agent_harness.outcomes import default_outcome_dir, evaluate_outcome, write_outcome
from agent_harness.packets import build_run_packet, status_to_payload, validate_run_packet
from agent_harness.platform_sync import default_platform_export_dir, write_platform_export
from agent_harness.promotions import default_promotions_dir, promote_latest
from agent_harness.registry import (
    default_namespace_root,
    discover_repositories,
    known_repo_specs,
)
from agent_harness.reports import build_ledger_report, build_outcome_report
from agent_harness.store import default_run_dir, load_packet, write_packet
from agent_harness.trust_policy import evaluate_repo_trust, load_trust_policy


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
    thesis.add_argument("tickers", nargs="*", default=["AAPL", "MSFT"], help="Tickers for the offline Monte Carlo smoke.")
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

    ledger = subparsers.add_parser("ledger", help="Inspect or update the provenance ledger.")
    ledger.add_argument(
        "--ledger-dir",
        type=Path,
        default=default_ledger_dir(),
        help="Directory for the append-only provenance ledger.",
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
    ledger_report.add_argument("--trust-policy", type=Path, help="Path to a trust-policy JSON file.")
    ledger_report.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_outcomes = ledger_sub.add_parser("outcomes", help="Summarize realized outcome performance.")
    ledger_outcomes.add_argument("--min-outcomes", type=int, default=0, help="Outcomes required before readiness.")
    ledger_outcomes.add_argument("--min-ok-rate", type=float, help="Minimum realized outcome scorecard ok rate.")
    ledger_outcomes.add_argument("--min-excess-cash", type=float, help="Minimum average realized excess return versus cash.")
    ledger_outcomes.add_argument("--min-excess-equal", type=float, help="Minimum average realized excess return versus equal weight.")
    ledger_outcomes.add_argument("--max-forecast-error", type=float, help="Maximum average absolute forecast error.")
    ledger_outcomes.add_argument("--max-drawdown", type=float, help="Maximum realized drawdown across outcomes.")
    ledger_outcomes.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
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
    ledger_sync.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ledger_promote = ledger_sub.add_parser("promote", help="Promote the latest ready ledger run to canonical.")
    ledger_promote.add_argument("--min-runs", type=int, default=3, help="Runs required before promotion readiness.")
    ledger_promote.add_argument("--min-outcomes", type=int, default=0, help="Realized outcomes required before promotion readiness.")
    ledger_promote.add_argument("--min-outcome-ok-rate", type=float, help="Minimum realized outcome scorecard ok rate.")
    ledger_promote.add_argument("--min-outcome-excess-cash", type=float, help="Minimum average realized excess return versus cash.")
    ledger_promote.add_argument("--min-outcome-excess-equal", type=float, help="Minimum average realized excess return versus equal weight.")
    ledger_promote.add_argument("--max-outcome-forecast-error", type=float, help="Maximum average absolute forecast error.")
    ledger_promote.add_argument("--max-outcome-drawdown", type=float, help="Maximum realized drawdown across outcomes.")
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
    if not args.no_run:
        monte_path = namespace_root / "monte-carlo"
        monte_adapter = MonteCarloAdapter(monte_path)
        monte_run = monte_adapter.run_offline_simulation(
            tuple(args.tickers or ["AAPL", "MSFT"]),
            days=int(args.days),
            scenarios=int(args.scenarios),
            seed=int(args.seed),
        )
        if args.backtest:
            monte_backtest = monte_adapter.run_offline_backtest(
                tuple(args.tickers or ["AAPL", "MSFT"]),
                lookback=int(args.backtest_lookback),
                hold=int(args.backtest_hold),
                rebalance=int(args.backtest_rebalance),
                scenarios=int(args.backtest_scenarios),
                seed=int(args.seed),
            )

    loops = build_capital_loops(
        statuses,
        monte_carlo_run=monte_run,
        monte_carlo_backtest=monte_backtest,
    )
    packet = build_run_packet(
        namespace_root=namespace_root,
        invocation=["agent-harness", *getattr(args, "_argv", [])],
        inputs={
            "tickers": list(args.tickers or ["AAPL", "MSFT"]),
            "days": int(args.days),
            "scenarios": int(args.scenarios),
            "seed": int(args.seed),
            "ran_engines": not bool(args.no_run),
            "ran_backtest": bool(monte_backtest is not None),
            "backtest": {
                "lookback": int(args.backtest_lookback),
                "hold": int(args.backtest_hold),
                "rebalance": int(args.backtest_rebalance),
                "scenarios": int(args.backtest_scenarios),
            },
        },
        statuses=statuses,
        monte_carlo_run=monte_run,
        monte_carlo_backtest=monte_backtest,
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
    print(f"Risk: realized_max_drawdown={risk['realized_max_drawdown']}")
    if saved_path is not None:
        print(f"Saved outcome: {saved_path}")
    if ledger_entry is not None:
        print(f"Ledger outcome: {ledger_entry['run_id']} -> {ledger_entry['outcome_digest'][:12]}")
    return 0 if scorecard["ok"] else 2


def _load_policy_arg(args: argparse.Namespace) -> dict[str, Any]:
    return load_trust_policy(getattr(args, "trust_policy", None))


def _outcome_thresholds(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "min_ok_rate": getattr(args, "min_outcome_ok_rate", None)
        if hasattr(args, "min_outcome_ok_rate")
        else getattr(args, "min_ok_rate", None),
        "min_avg_excess_cash": getattr(args, "min_outcome_excess_cash", None)
        if hasattr(args, "min_outcome_excess_cash")
        else getattr(args, "min_excess_cash", None),
        "min_avg_excess_equal_weight": getattr(args, "min_outcome_excess_equal", None)
        if hasattr(args, "min_outcome_excess_equal")
        else getattr(args, "min_excess_equal", None),
        "max_avg_abs_forecast_error": getattr(args, "max_outcome_forecast_error", None)
        if hasattr(args, "max_outcome_forecast_error")
        else getattr(args, "max_forecast_error", None),
        "max_realized_drawdown": getattr(args, "max_outcome_drawdown", None)
        if hasattr(args, "max_outcome_drawdown")
        else getattr(args, "max_drawdown", None),
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


def _render_outcome_report(report: dict[str, Any]) -> None:
    promotion = report["promotion"]
    scorecard = report["scorecard"]
    returns = report["returns"]
    calibration = report["calibration"]
    risk = report["risk"]
    attribution = report["attribution"]
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
    print(f"Risk: max_drawdown={risk['realized_max_drawdown']['max']}")
    print(f"Thresholds: {promotion['thresholds']}")
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
        trust_policy = _load_policy_arg(args)
        report = build_ledger_report(
            entries,
            min_runs_for_promotion=int(args.min_runs),
            trust_policy=trust_policy,
            outcome_entries=outcome_entries,
            min_outcomes_for_promotion=int(args.min_outcomes),
            outcome_thresholds=_outcome_thresholds(args),
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            promotion = report["promotion"]
            backtest = report["backtest"]
            stress = report["stress"]
            picks = report["primary_picks"]
            trust = report["trust"]
            outcomes = report["outcomes"]
            outcome_attribution = outcomes["attribution"]
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
                f"avg_cash_drag={outcome_attribution['cash']['drag_vs_equal_weight']['avg']}"
            )
            policy = trust["latest_policy_evaluation"]["policy"]
            print(
                "Trust policy: "
                f"loaded={policy['loaded']} "
                f"digest={policy['digest'][:12]} "
                f"source={policy['source_path']}"
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
        return 0

    if args.ledger_command == "outcomes":
        entries = read_outcome_entries(args.ledger_dir)
        report = build_outcome_report(
            entries,
            min_outcomes_for_promotion=int(args.min_outcomes),
            **_outcome_thresholds(args),
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _render_outcome_report(report)
        return 0 if report["promotion"]["ready"] else 2

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
        manifest, paths = write_platform_export(
            ledger_entries=entries,
            outcome_entries=outcome_entries,
            ledger_dir=args.ledger_dir,
            output_dir=args.output_dir,
            promotions_dir=args.promotions_dir,
            target=args.target,
        )
        payload = {
            "manifest": manifest,
            "paths": {key: str(value) for key, value in paths.items()},
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
                f"promotions={manifest['counts']['promotions']}"
            )
            print(f"Bundle: {paths['export_dir']}")
            print(f"Manifest: {paths['manifest']}")
        return 0

    if args.ledger_command == "promote":
        trust_policy = _load_policy_arg(args)
        record, paths = promote_latest(
            ledger_dir=args.ledger_dir,
            promotions_dir=args.promotions_dir,
            min_runs=int(args.min_runs),
            min_outcomes=int(args.min_outcomes),
            outcome_thresholds=_outcome_thresholds(args),
            trust_policy=trust_policy,
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
        args.tickers = ["AAPL", "MSFT"]
        args.days = 30
        args.scenarios = 100
        args.seed = 42
        args.no_run = False
        args.backtest = True
        args.backtest_lookback = 3
        args.backtest_hold = 2
        args.backtest_rebalance = 2
        args.backtest_scenarios = 20
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
    if args.command == "ledger":
        return _render_ledger(args)
    parser.print_help()
    return 1
