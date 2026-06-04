"""Append-only provenance ledger for run packets."""

from __future__ import annotations

import json
import os
import hashlib
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


def _stable_digest(payload: dict[str, Any]) -> str:
    scoped = dict(payload)
    scoped.pop("outcome_digest", None)
    scoped.pop("evaluated_at", None)
    encoded = json.dumps(scoped, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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


def _stress_summary(packet: dict[str, Any]) -> dict[str, Any]:
    stress = packet.get("stress_tests", {})
    return stress if isinstance(stress, dict) else {}


def _status_lines(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(line) for line in value if isinstance(line, str) and line.strip()]


def build_repo_trust(packet: dict[str, Any]) -> dict[str, Any]:
    """Return compact repository trust metadata from packet adapters."""

    adapters = packet.get("adapters") if isinstance(packet.get("adapters"), dict) else {}
    rows: list[dict[str, Any]] = []
    dirty_details: list[dict[str, Any]] = []
    for name, adapter in sorted(adapters.items(), key=lambda item: item[0]):
        if not isinstance(adapter, dict):
            continue
        status_lines = _status_lines(adapter.get("repo_status"))
        status_count = adapter.get("repo_status_count")
        if not isinstance(status_count, int):
            status_count = len(status_lines)
        row = {
            "name": str(adapter.get("name") or name),
            "repo_path": adapter.get("repo_path"),
            "repo_sha": adapter.get("repo_sha"),
            "repo_branch": adapter.get("repo_branch"),
            "repo_dirty": adapter.get("repo_dirty"),
            "repo_status": status_lines,
            "repo_status_count": status_count,
            "repo_status_truncated": bool(adapter.get("repo_status_truncated")),
        }
        rows.append(row)
        if row["repo_dirty"] is True:
            dirty_details.append(row)
    return {
        "adapter_count": len(rows),
        "dirty_count": len(dirty_details),
        "dirty_details": dirty_details,
        "adapters": rows,
    }


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
    stress = _stress_summary(packet)
    repo_trust = build_repo_trust(packet)
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
        "repo_trust": repo_trust,
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
        "stress": {
            "ok": stress.get("ok"),
            "worst_margin": stress.get("worst_margin"),
            "scenario_count": len(stress.get("scenarios", []))
            if isinstance(stress.get("scenarios"), list)
            else 0,
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


def _read_jsonl_entries(path: Path) -> list[dict[str, Any]]:
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


def read_outcome_entries(ledger_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read realized-outcome ledger entries in append order."""

    root = (ledger_dir or default_ledger_dir()).expanduser().resolve()
    return _read_jsonl_entries(root / "outcomes.jsonl")


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


def _safe_key(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _outcome_base_key(outcome: dict[str, Any]) -> str:
    window = outcome.get("window", {}) if isinstance(outcome.get("window"), dict) else {}
    return "_".join(
        _safe_key(str(part))
        for part in (
            outcome.get("run_id") or "run",
            window.get("start_date") or "start",
            window.get("end_date") or "end",
        )
    )


def _outcome_key(outcome: dict[str, Any], digest: str) -> str:
    return f"{_outcome_base_key(outcome)}_{_safe_key(digest[:12])}"


def _existing_outcome_matches_digest(existing: dict[str, Any], digest: str) -> bool:
    if existing.get("outcome_digest") == digest:
        return True
    copy_path_raw = existing.get("outcome_copy_path")
    if not isinstance(copy_path_raw, str) or not copy_path_raw:
        return False
    copy_path = Path(copy_path_raw)
    if not copy_path.exists():
        return False
    existing_payload = json.loads(copy_path.read_text(encoding="utf-8"))
    return isinstance(existing_payload, dict) and _stable_digest(existing_payload) == digest


def build_outcome_entry(
    outcome: dict[str, Any],
    *,
    outcome_path: Path | None = None,
    outcome_copy_path: Path | None = None,
) -> dict[str, Any]:
    """Build the compact queryable row for a realized outcome artifact."""

    if outcome.get("schema_version") != "agent-harness.outcome.v1":
        raise ValueError("unsupported outcome schema_version")
    digest = outcome.get("outcome_digest") or _stable_digest(outcome)
    window = outcome.get("window", {}) if isinstance(outcome.get("window"), dict) else {}
    scorecard = outcome.get("scorecard", {}) if isinstance(outcome.get("scorecard"), dict) else {}
    returns = outcome.get("returns", {}) if isinstance(outcome.get("returns"), dict) else {}
    risk = outcome.get("risk", {}) if isinstance(outcome.get("risk"), dict) else {}
    primary = outcome.get("primary_pick", {}) if isinstance(outcome.get("primary_pick"), dict) else {}
    sources = outcome.get("sources", {}) if isinstance(outcome.get("sources"), dict) else {}
    attribution = (
        outcome.get("attribution", {})
        if isinstance(outcome.get("attribution"), dict)
        else {}
    )
    drivers = (
        attribution.get("drivers", {})
        if isinstance(attribution.get("drivers"), dict)
        else {}
    )
    active_excess = (
        attribution.get("active_excess", {})
        if isinstance(attribution.get("active_excess"), dict)
        else {}
    )
    cash = (
        attribution.get("cash", {})
        if isinstance(attribution.get("cash"), dict)
        else {}
    )
    price_sources = (
        sources.get("prices", {})
        if isinstance(sources.get("prices"), dict)
        else {}
    )
    return {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "entry_type": "outcome",
        "ingested_at": _utc_now(),
        "run_id": outcome.get("run_id"),
        "content_digest": outcome.get("content_digest"),
        "outcome_digest": digest,
        "outcome_path": str(outcome_path.expanduser().resolve()) if outcome_path else None,
        "outcome_copy_path": str(outcome_copy_path.expanduser().resolve()) if outcome_copy_path else None,
        "window": {
            "start_date": window.get("start_date"),
            "end_date": window.get("end_date"),
            "horizon_rows": window.get("horizon_rows"),
        },
        "primary_pick": {
            "ticker": primary.get("ticker"),
            "expected_return": primary.get("expected_return"),
            "realized_return": primary.get("realized_return"),
            "forecast_error": primary.get("forecast_error"),
            "hit": primary.get("hit"),
        },
        "returns": {
            "allocation": returns.get("allocation"),
            "equal_weight": returns.get("equal_weight"),
            "cash": returns.get("cash"),
            "excess_vs_equal_weight": returns.get("excess_vs_equal_weight"),
            "excess_vs_cash": returns.get("excess_vs_cash"),
        },
        "risk": {
            "realized_max_drawdown": risk.get("realized_max_drawdown"),
        },
        "sources": {
            "price_dir": sources.get("price_dir"),
            "price_source_digest": sources.get("price_source_digest"),
            "prices": {
                str(ticker): {
                    "sha256": row.get("sha256") if isinstance(row, dict) else None,
                    "rows": row.get("rows") if isinstance(row, dict) else None,
                    "first_date": row.get("first_date") if isinstance(row, dict) else None,
                    "last_date": row.get("last_date") if isinstance(row, dict) else None,
                }
                for ticker, row in sorted(price_sources.items())
            },
        },
        "attribution": {
            "active_excess": {
                "vs_equal_weight": active_excess.get("vs_equal_weight"),
                "from_positions": active_excess.get("from_positions"),
                "from_cash": active_excess.get("from_cash"),
            },
            "cash": {
                "weight": cash.get("weight"),
                "contribution": cash.get("contribution"),
                "drag_vs_equal_weight": cash.get("drag_vs_equal_weight"),
            },
            "drivers": {
                "top_allocation_contributor": drivers.get("top_allocation_contributor"),
                "top_active_contributor": drivers.get("top_active_contributor"),
                "weakest_active_contributor": drivers.get("weakest_active_contributor"),
                "largest_active_drag": drivers.get("largest_active_drag"),
            },
        },
        "scorecard": {
            "ok": scorecard.get("ok"),
            "beat_cash": scorecard.get("beat_cash"),
            "beat_equal_weight": scorecard.get("beat_equal_weight"),
            "primary_hit": scorecard.get("primary_hit"),
        },
    }


def ingest_outcome(
    outcome: dict[str, Any],
    *,
    outcome_path: Path | None = None,
    ledger_dir: Path | None = None,
) -> dict[str, Any]:
    """Ingest a realized outcome into the ledger."""

    root = (ledger_dir or default_ledger_dir()).expanduser().resolve()
    index_path = root / "outcome_index.json"
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.exists()
        else {"schema_version": LEDGER_SCHEMA_VERSION, "outcomes": {}}
    )
    outcomes = index.setdefault("outcomes", {})
    if not isinstance(outcomes, dict):
        raise ValueError("outcome index outcomes must be a JSON object")
    digest = outcome.get("outcome_digest") or _stable_digest(outcome)
    base_key = _outcome_base_key(outcome)
    key = _outcome_key(outcome, str(digest))
    for existing_key, existing_entry in outcomes.items():
        if not isinstance(existing_entry, dict):
            continue
        if existing_key == base_key or existing_key.startswith(f"{base_key}_"):
            if _existing_outcome_matches_digest(existing_entry, str(digest)):
                return existing_entry
    existing = outcomes.get(key)
    if isinstance(existing, dict):
        if not _existing_outcome_matches_digest(existing, str(digest)):
            raise ValueError(f"outcome key collision with different digest: {key}")
        return existing

    outcome = dict(outcome)
    outcome["outcome_digest"] = digest
    copy_path = root / "outcomes" / f"{key}.json"
    _atomic_write_json(copy_path, outcome)
    entry = build_outcome_entry(
        outcome,
        outcome_path=outcome_path,
        outcome_copy_path=copy_path,
    )
    _append_jsonl(root / "outcomes.jsonl", entry)
    outcomes[key] = entry
    index["schema_version"] = LEDGER_SCHEMA_VERSION
    _atomic_write_json(index_path, index)
    _atomic_write_json(root / "latest_outcome.json", entry)
    return entry
