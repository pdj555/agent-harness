"""Export ledger data into a research-run-platform import bundle."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLATFORM_EXPORT_SCHEMA_VERSION = "agent-harness.platform-export.v1"
PLATFORM_EXPORT_TARGET = "research-run-platform"


def default_platform_export_dir(cwd: Path | None = None) -> Path:
    """Return the default research-run-platform export directory."""

    return (cwd or Path.cwd()) / ".agent-harness" / "platform_exports"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_export_id(created_at: str) -> str:
    stamp = created_at.replace("+00:00", "Z").replace(":", "").replace("-", "")
    return f"platform_export_{stamp}_{uuid.uuid4().hex[:12]}"


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(payload, encoding="utf-8")
    os.replace(temp_path, path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def _stable_digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_ref(path_raw: Any, *, kind: str, logical_id: str | None = None) -> dict[str, Any]:
    if not isinstance(path_raw, str) or not path_raw:
        return {
            "kind": kind,
            "logical_id": logical_id,
            "path": None,
            "exists": False,
            "sha256": None,
            "bytes": None,
        }
    path = Path(path_raw).expanduser().resolve()
    exists = path.exists() and path.is_file()
    return {
        "kind": kind,
        "logical_id": logical_id,
        "path": str(path),
        "exists": exists,
        "sha256": _file_sha256(path) if exists else None,
        "bytes": path.stat().st_size if exists else None,
    }


def _run_export_row(entry: dict[str, Any]) -> dict[str, Any]:
    row = dict(entry)
    row["platform_entry_type"] = "run"
    row["platform_entry_digest"] = _stable_digest(row)
    row["artifacts"] = {
        "packet": _artifact_ref(
            row.get("packet_copy_path") or row.get("packet_path"),
            kind="run_packet",
            logical_id=str(row.get("run_id") or ""),
        )
    }
    return row


def _outcome_export_row(entry: dict[str, Any]) -> dict[str, Any]:
    row = dict(entry)
    row["platform_entry_type"] = "outcome"
    row["platform_entry_digest"] = _stable_digest(row)
    row["artifacts"] = {
        "outcome": _artifact_ref(
            row.get("outcome_copy_path") or row.get("outcome_path"),
            kind="realized_outcome",
            logical_id=str(row.get("outcome_digest") or row.get("run_id") or ""),
        )
    }
    return row


def _promotion_export_row(record: dict[str, Any]) -> dict[str, Any]:
    row = dict(record)
    row["platform_entry_type"] = "promotion"
    row["platform_entry_digest"] = _stable_digest(row)
    return row


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def _latest_promotion(promotions_dir: Path | None) -> dict[str, Any] | None:
    if promotions_dir is None:
        return None
    path = promotions_dir.expanduser().resolve() / "latest.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _duckdb_import_sql() -> str:
    return "\n".join(
        [
            "-- Import this bundle from its export directory.",
            "CREATE TABLE IF NOT EXISTS agent_harness_runs AS",
            "SELECT * FROM read_json_auto('runs.jsonl');",
            "",
            "CREATE TABLE IF NOT EXISTS agent_harness_outcomes AS",
            "SELECT * FROM read_json_auto('outcomes.jsonl');",
            "",
            "CREATE TABLE IF NOT EXISTS agent_harness_promotions AS",
            "SELECT * FROM read_json_auto('promotions.jsonl');",
            "",
        ]
    )


def build_platform_export(
    *,
    ledger_entries: list[dict[str, Any]],
    outcome_entries: list[dict[str, Any]],
    ledger_dir: Path,
    promotions_dir: Path | None = None,
    target: str = PLATFORM_EXPORT_TARGET,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build a platform export manifest and file payloads."""

    if target != PLATFORM_EXPORT_TARGET:
        raise ValueError(f"unsupported platform export target: {target}")
    created_at = _utc_now()
    export_id = _new_export_id(created_at)
    run_rows = [_run_export_row(entry) for entry in ledger_entries]
    outcome_rows = [_outcome_export_row(entry) for entry in outcome_entries]
    promotion = _latest_promotion(promotions_dir)
    promotion_rows = [_promotion_export_row(promotion)] if promotion is not None else []
    files = {
        "runs_jsonl": "runs.jsonl",
        "outcomes_jsonl": "outcomes.jsonl",
        "promotions_jsonl": "promotions.jsonl",
        "duckdb_import_sql": "duckdb_import.sql",
    }
    file_payloads = {
        files["runs_jsonl"]: _jsonl(run_rows),
        files["outcomes_jsonl"]: _jsonl(outcome_rows),
        files["promotions_jsonl"]: _jsonl(promotion_rows),
        files["duckdb_import_sql"]: _duckdb_import_sql(),
    }
    manifest = {
        "schema_version": PLATFORM_EXPORT_SCHEMA_VERSION,
        "target": target,
        "contract_version": "1",
        "export_id": export_id,
        "created_at": created_at,
        "source": {
            "ledger_dir": str(ledger_dir.expanduser().resolve()),
            "promotions_dir": str(promotions_dir.expanduser().resolve()) if promotions_dir else None,
        },
        "counts": {
            "runs": len(run_rows),
            "outcomes": len(outcome_rows),
            "promotions": len(promotion_rows),
        },
        "latest": {
            "run_id": run_rows[-1].get("run_id") if run_rows else None,
            "content_digest": run_rows[-1].get("content_digest") if run_rows else None,
            "outcome_digest": outcome_rows[-1].get("outcome_digest") if outcome_rows else None,
            "promotion_id": promotion_rows[-1].get("promotion_id") if promotion_rows else None,
        },
        "files": files,
        "file_digests": {
            name: hashlib.sha256(payload.encode("utf-8")).hexdigest()
            for name, payload in file_payloads.items()
        },
    }
    return manifest, file_payloads


def write_platform_export(
    *,
    ledger_entries: list[dict[str, Any]],
    outcome_entries: list[dict[str, Any]],
    ledger_dir: Path,
    output_dir: Path | None = None,
    promotions_dir: Path | None = None,
    target: str = PLATFORM_EXPORT_TARGET,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Write a research-run-platform export bundle and return its manifest."""

    manifest, file_payloads = build_platform_export(
        ledger_entries=ledger_entries,
        outcome_entries=outcome_entries,
        ledger_dir=ledger_dir,
        promotions_dir=promotions_dir,
        target=target,
    )
    root = (output_dir or default_platform_export_dir()).expanduser().resolve()
    export_dir = root / str(manifest["export_id"])
    paths: dict[str, Path] = {
        "export_dir": export_dir,
        "manifest": export_dir / "manifest.json",
    }
    for name, payload in file_payloads.items():
        path = export_dir / name
        _atomic_write_text(path, payload)
        paths[name] = path
    _atomic_write_json(paths["manifest"], manifest)
    _atomic_write_json(root / "latest.json", manifest)
    paths["latest_manifest"] = root / "latest.json"
    return manifest, paths
