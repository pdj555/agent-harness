"""Append-only provenance ledger for run packets."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_harness.evals import evaluate_packet
from agent_harness.packets import packet_digest, validate_run_packet


LEDGER_SCHEMA_VERSION = "agent-harness.ledger.v1"


def default_ledger_dir(cwd: Path | None = None) -> Path:
    """Return the default local provenance-ledger directory."""

    return (cwd or Path.cwd()) / ".agent-harness" / "ledger"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _load_index(ledger_dir: Path) -> dict[str, Any]:
    path = ledger_dir / "index.json"
    if not path.exists():
        return {"schema_version": LEDGER_SCHEMA_VERSION, "runs": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ledger index must be a JSON object")
    runs = payload.setdefault("runs", {})
    if not isinstance(runs, dict):
        raise ValueError("ledger index runs must be a JSON object")
    return payload


def _primary_pick(packet: dict[str, Any]) -> dict[str, Any]:
    monte_run = packet.get("engine_runs", {}).get("monte_carlo")
    if not isinstance(monte_run, dict):
        return {}
    payload = monte_run.get("payload", {})
    if not isinstance(payload, dict):
        return {}
    action_plan = payload.get("action_plan", {})
    if not isinstance(action_plan, dict):
        return {}
    primary = action_plan.get("primary_pick", {})
    return primary if isinstance(primary, dict) else {}


def _top_loop(packet: dict[str, Any]) -> dict[str, Any]:
    loops = packet.get("ranked_loops", [])
    if isinstance(loops, list) and loops and isinstance(loops[0], dict):
        return loops[0]
    return {}


def _backtest_summary(packet: dict[str, Any]) -> dict[str, Any]:
    backtest_run = packet.get("engine_runs", {}).get("monte_carlo_backtest")
    if not isinstance(backtest_run, dict):
        return {}
    payload = backtest_run.get("payload", {})
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary", {})
    return summary if isinstance(summary, dict) else {}


def build_ledger_entry(
    packet: dict[str, Any],
    *,
    packet_path: Path | None = None,
    packet_copy_path: Path | None = None,
) -> dict[str, Any]:
    """Build the compact queryable row for a saved run packet."""

    problems = validate_run_packet(packet)
    if problems:
        raise ValueError("; ".join(problems))

    evaluation = evaluate_packet(packet)
    primary = _primary_pick(packet)
    top_loop = _top_loop(packet)
    backtest = _backtest_summary(packet)
    monte_run = packet.get("engine_runs", {}).get("monte_carlo")
    monte_ok = isinstance(monte_run, dict) and bool(monte_run.get("ok"))
    backtest_run = packet.get("engine_runs", {}).get("monte_carlo_backtest")
    backtest_ok = isinstance(backtest_run, dict) and bool(backtest_run.get("ok"))

    return {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "ingested_at": _utc_now(),
        "run_id": packet["run_id"],
        "packet_schema_version": packet["schema_version"],
        "created_at": packet["created_at"],
        "content_digest": packet.get("content_digest") or packet_digest(packet),
        "packet_path": str(packet_path.expanduser().resolve()) if packet_path else None,
        "packet_copy_path": str(packet_copy_path.expanduser().resolve()) if packet_copy_path else None,
        "namespace_root": packet.get("namespace_root"),
        "monte_carlo_ok": monte_ok,
        "monte_carlo_backtest_ok": backtest_ok,
        "eval_ok": bool(evaluation["ok"]),
        "eval_score": evaluation["score"],
        "dirty_repos": list(evaluation["dirty_repos"]),
        "top_loop": {
            "name": top_loop.get("name"),
            "repo": top_loop.get("repo"),
            "score": top_loop.get("score"),
        },
        "primary_pick": {
            "ticker": primary.get("ticker"),
            "weight": primary.get("weight"),
            "expected_return": primary.get("expected_return"),
            "prob_above_current": primary.get("prob_above_current"),
            "value_at_risk_95_pct": primary.get("value_at_risk_95_pct"),
        },
        "backtest": {
            "strategy_total_return": backtest.get("strategy_total_return"),
            "strategy_max_drawdown": backtest.get("strategy_max_drawdown"),
            "strategy_win_rate": backtest.get("strategy_win_rate"),
            "excess_return_vs_equal_weight": backtest.get("excess_return_vs_equal_weight"),
            "excess_return_vs_cash": backtest.get("excess_return_vs_cash"),
            "periods": backtest.get("periods"),
        },
    }


def ingest_packet(
    packet: dict[str, Any],
    *,
    packet_path: Path | None = None,
    ledger_dir: Path | None = None,
) -> dict[str, Any]:
    """Ingest a packet into the append-only provenance ledger.

    Ingest is idempotent by ``run_id`` and ``content_digest``. Re-ingesting the
    same packet returns the existing entry. Reusing a run id for different
    content is rejected.
    """

    root = (ledger_dir or default_ledger_dir()).expanduser().resolve()
    run_id = str(packet.get("run_id") or "")
    if not run_id:
        raise ValueError("packet missing run_id")

    problems = validate_run_packet(packet)
    if problems:
        raise ValueError("; ".join(problems))
    digest = packet_digest(packet)
    index = _load_index(root)
    existing = index["runs"].get(run_id)
    if isinstance(existing, dict):
        if existing.get("content_digest") != digest:
            raise ValueError(f"run id collision with different digest: {run_id}")
        return existing

    packet_copy_path = root / "packets" / f"{run_id}.json"
    _atomic_write_json(packet_copy_path, packet)
    entry = build_ledger_entry(
        packet,
        packet_path=packet_path,
        packet_copy_path=packet_copy_path,
    )
    _append_jsonl(root / "runs.jsonl", entry)
    index["schema_version"] = LEDGER_SCHEMA_VERSION
    index["runs"][run_id] = entry
    _atomic_write_json(root / "index.json", index)
    _atomic_write_json(root / "latest.json", entry)
    return entry


def read_ledger_entries(ledger_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read entries in append order."""

    root = (ledger_dir or default_ledger_dir()).expanduser().resolve()
    path = root / "runs.jsonl"
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def get_ledger_entry(run_id: str, ledger_dir: Path | None = None) -> dict[str, Any]:
    """Return a single ledger entry by run id."""

    root = (ledger_dir or default_ledger_dir()).expanduser().resolve()
    index = _load_index(root)
    entry = index["runs"].get(run_id)
    if not isinstance(entry, dict):
        raise KeyError(run_id)
    return entry


def load_ledger_packet(run_id: str, ledger_dir: Path | None = None) -> dict[str, Any]:
    """Load the packet copy for a ledger entry."""

    entry = get_ledger_entry(run_id, ledger_dir)
    packet_copy_path = entry.get("packet_copy_path")
    if not isinstance(packet_copy_path, str) or not packet_copy_path:
        raise ValueError(f"ledger entry has no packet copy path: {run_id}")
    payload = json.loads(Path(packet_copy_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ledger packet copy must be a JSON object")
    return payload
