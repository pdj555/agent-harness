"""Promotion gate for canonical capital research decisions."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_harness.ledger import read_ledger_entries
from agent_harness.reports import build_ledger_report


PROMOTION_SCHEMA_VERSION = "agent-harness.promotion.v1"


def default_promotions_dir(cwd: Path | None = None) -> Path:
    """Return the default promotion artifact directory."""

    return (cwd or Path.cwd()) / ".agent-harness" / "promotions"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_promotion_id(now: str) -> str:
    stamp = now.replace("+00:00", "Z").replace(":", "").replace("-", "")
    return f"promotion_{stamp}_{uuid.uuid4().hex[:12]}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def build_promotion_record(
    *,
    report: dict[str, Any],
    latest_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a promotion or blocked-promotion record."""

    created_at = _utc_now()
    ready = bool(report.get("promotion", {}).get("ready"))
    blockers = list(report.get("promotion", {}).get("blockers", []))
    primary_pick = latest_entry.get("primary_pick", {}) if latest_entry else {}
    backtest = latest_entry.get("backtest", {}) if latest_entry else {}
    stress = latest_entry.get("stress", {}) if latest_entry else {}
    top_loop = latest_entry.get("top_loop", {}) if latest_entry else {}

    canonical_decision = None
    if ready and latest_entry:
        canonical_decision = {
            "run_id": latest_entry.get("run_id"),
            "content_digest": latest_entry.get("content_digest"),
            "top_loop": top_loop,
            "primary_pick": primary_pick,
            "backtest": backtest,
            "stress": stress,
            "risk_authority": "research_only",
        }

    return {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "promotion_id": _new_promotion_id(created_at),
        "created_at": created_at,
        "status": "promoted" if ready else "blocked",
        "run_id": latest_entry.get("run_id") if latest_entry else None,
        "content_digest": latest_entry.get("content_digest") if latest_entry else None,
        "blockers": blockers,
        "report": report,
        "canonical_decision": canonical_decision,
    }


def write_promotion_record(
    record: dict[str, Any],
    *,
    promotions_dir: Path | None = None,
) -> dict[str, Path | None]:
    """Write a promotion attempt and canonical artifact when promoted."""

    root = (promotions_dir or default_promotions_dir()).expanduser().resolve()
    promotion_id = str(record.get("promotion_id") or "promotion")
    attempt_path = root / "attempts" / f"{promotion_id}.json"
    _atomic_write_json(attempt_path, record)
    _atomic_write_json(root / "latest.json", record)

    canonical_path = None
    if record.get("status") == "promoted":
        canonical_path = root / "canonical.json"
        _atomic_write_json(canonical_path, record)

    return {"attempt_path": attempt_path, "canonical_path": canonical_path}


def promote_latest(
    *,
    ledger_dir: Path | None = None,
    promotions_dir: Path | None = None,
    min_runs: int = 3,
) -> tuple[dict[str, Any], dict[str, Path | None]]:
    """Attempt to promote the latest ledger entry as the canonical decision."""

    entries = read_ledger_entries(ledger_dir)
    report = build_ledger_report(entries, min_runs_for_promotion=min_runs)
    latest_entry = entries[-1] if entries else None
    record = build_promotion_record(report=report, latest_entry=latest_entry)
    paths = write_promotion_record(record, promotions_dir=promotions_dir)
    return record, paths
