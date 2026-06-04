"""Stable run-packet schema for agent harness decisions."""

from __future__ import annotations

import hashlib
import json
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_harness import __version__
from agent_harness.adapters import AdapterStatus, EngineRun
from agent_harness.capital import CapitalLoop


SCHEMA_VERSION = "agent-harness.run.v1"

DEFAULT_RISK_CONTROLS = {
    "max_position_weight": 0.60,
    "min_cash_buffer_when_concentrated": 0.20,
    "concentration_weight": 0.50,
    "execution_authority": "research_only",
    "requires_human_approval_for_orders": True,
}


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_run_id(*, now: str | None = None) -> str:
    """Return a sortable, collision-resistant run id."""

    stamp = (now or utc_now()).replace("+00:00", "Z").replace(":", "").replace("-", "")
    return f"run_{stamp}_{uuid.uuid4().hex[:12]}"


def status_to_payload(status: AdapterStatus) -> dict[str, Any]:
    """Normalize adapter status into the packet schema."""

    return {
        "name": status.name,
        "available": status.available,
        "repo_path": str(status.repo_path),
        "reason": status.reason,
        "command": list(status.command),
        "required_env": list(status.required_env),
        "capabilities": list(status.capabilities),
        "contract_version": status.contract_version,
        "repo_sha": status.repo_sha,
        "repo_dirty": status.repo_dirty,
    }


def engine_run_to_payload(run: EngineRun | None) -> dict[str, Any] | None:
    """Normalize an engine run into the packet schema."""

    if run is None:
        return None
    return {
        "name": run.name,
        "ok": run.ok,
        "summary": run.summary,
        "payload": run.payload,
        "diagnostics": list(run.diagnostics),
        "command": list(run.command),
        "duration_ms": run.duration_ms,
        "repo_sha": run.repo_sha,
        "repo_dirty": run.repo_dirty,
    }


def loop_to_payload(loop: CapitalLoop) -> dict[str, Any]:
    """Normalize a ranked capital loop."""

    return {
        "name": loop.name,
        "repo": loop.repo,
        "score": round(loop.score, 6),
        "thesis": loop.thesis,
        "expected_edge": loop.expected_edge,
        "confidence": loop.confidence,
        "max_loss": loop.max_loss,
        "implementation_effort": loop.implementation_effort,
        "half_life_days": loop.half_life_days,
        "evidence": list(loop.evidence),
    }


def build_run_packet(
    *,
    namespace_root: Path,
    invocation: list[str],
    inputs: dict[str, Any],
    statuses: dict[str, AdapterStatus],
    monte_carlo_run: EngineRun | None,
    ranked_loops: list[CapitalLoop],
    monte_carlo_backtest: EngineRun | None = None,
) -> dict[str, Any]:
    """Build a durable decision packet for this thesis run."""

    created_at = utc_now()
    packet = {
        "schema_version": SCHEMA_VERSION,
        "run_id": new_run_id(now=created_at),
        "created_at": created_at,
        "harness": {
            "version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "namespace_root": str(namespace_root),
        "invocation": invocation,
        "inputs": inputs,
        "risk_controls": dict(DEFAULT_RISK_CONTROLS),
        "adapters": {
            name: status_to_payload(status)
            for name, status in sorted(statuses.items(), key=lambda item: item[0])
        },
        "engine_runs": {
            "monte_carlo": engine_run_to_payload(monte_carlo_run),
            "monte_carlo_backtest": engine_run_to_payload(monte_carlo_backtest),
        },
        "ranked_loops": [loop_to_payload(loop) for loop in ranked_loops],
    }
    packet["content_digest"] = packet_digest(packet)
    return packet


def packet_digest(packet: dict[str, Any]) -> str:
    """Return a stable digest over packet content, excluding the digest field."""

    scoped = dict(packet)
    scoped.pop("content_digest", None)
    encoded = json.dumps(scoped, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_run_packet(packet: dict[str, Any]) -> list[str]:
    """Return schema and contract problems. Empty means valid enough to replay."""

    problems: list[str] = []
    if packet.get("schema_version") != SCHEMA_VERSION:
        problems.append("unsupported schema_version")
    for field in ("run_id", "created_at", "namespace_root", "adapters", "ranked_loops"):
        if not packet.get(field):
            problems.append(f"missing {field}")
    if not isinstance(packet.get("adapters"), dict):
        problems.append("adapters must be an object")
    if not isinstance(packet.get("ranked_loops"), list):
        problems.append("ranked_loops must be a list")
    expected_digest = packet.get("content_digest")
    if expected_digest and expected_digest != packet_digest(packet):
        problems.append("content_digest mismatch")
    return problems
