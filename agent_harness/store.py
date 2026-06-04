"""Atomic storage for durable run packets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def default_run_dir(cwd: Path | None = None) -> Path:
    """Return the default local run-packet directory."""

    return (cwd or Path.cwd()) / ".agent-harness" / "runs"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def packet_path(output_dir: Path, run_id: str) -> Path:
    """Return the canonical path for a run packet."""

    safe_run_id = "".join(char if char.isalnum() or char in "._-" else "_" for char in run_id)
    return output_dir.expanduser().resolve() / f"{safe_run_id}.json"


def write_packet(packet: dict[str, Any], output_dir: Path | None = None) -> Path:
    """Write a packet and update ``latest.json`` atomically."""

    run_dir = (output_dir or default_run_dir()).expanduser().resolve()
    run_id = str(packet.get("run_id") or "run")
    path = packet_path(run_dir, run_id)
    _atomic_write_json(path, packet)
    _atomic_write_json(run_dir / "latest.json", packet)
    return path


def load_packet(path: Path) -> dict[str, Any]:
    """Load a run packet from disk."""

    raw = path.expanduser().read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("run packet must be a JSON object")
    return payload

