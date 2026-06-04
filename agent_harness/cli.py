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
    default_ledger_dir,
    get_ledger_entry,
    ingest_packet,
    load_ledger_packet,
    read_ledger_entries,
)
from agent_harness.packets import build_run_packet, status_to_payload, validate_run_packet
from agent_harness.registry import (
    default_namespace_root,
    discover_repositories,
    known_repo_specs,
)
from agent_harness.store import default_run_dir, load_packet, write_packet


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
        return 0

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
    if args.command == "ledger":
        return _render_ledger(args)
    parser.print_help()
    return 1
