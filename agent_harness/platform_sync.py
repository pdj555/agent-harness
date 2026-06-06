"""Export ledger data into a research-run-platform import bundle."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import uuid
from json import JSONDecodeError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_harness.reports import build_promotion_attempt_report


PLATFORM_EXPORT_SCHEMA_VERSION = "agent-harness.platform-export.v1"
PLATFORM_IMPORT_SCHEMA_VERSION = "agent-harness.platform-import.v1"
PLATFORM_SIGNATURE_SCHEMA_VERSION = "agent-harness.platform-signature.v1"
PLATFORM_EXPORT_TARGET = "research-run-platform"
PLATFORM_SIGNATURE_FILE = "manifest.signature.json"
PLATFORM_SIGNING_KEY_ENV = "AGENT_HARNESS_PLATFORM_SIGNING_KEY"
_JSONL_CONTRACTS = {
    "runs_jsonl": ("runs", "run", "run_id"),
    "outcomes_jsonl": ("outcomes", "outcome", "outcome_digest"),
    "regimes_jsonl": ("regimes", "regime_replay", "report_digest"),
    "promotions_jsonl": ("promotions", "promotion", "promotion_id"),
}


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


def _resolve_signing_key(
    *,
    signing_key: str | None = None,
    signing_key_file: Path | None = None,
    use_env: bool = True,
) -> bytes | None:
    raw: str | None = None
    if signing_key is not None:
        raw = signing_key
    elif signing_key_file is not None:
        raw = signing_key_file.expanduser().read_text(encoding="utf-8")
    elif use_env:
        raw = os.environ.get(PLATFORM_SIGNING_KEY_ENV)
    if raw is None:
        return None
    raw = raw.strip()
    return raw.encode("utf-8") if raw else None


def _manifest_signature_payload(
    manifest_bytes: bytes,
    *,
    signing_key: bytes,
) -> dict[str, Any]:
    return {
        "schema_version": PLATFORM_SIGNATURE_SCHEMA_VERSION,
        "algorithm": "hmac-sha256",
        "signed_file": "manifest.json",
        "signed_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "key_id": hashlib.sha256(signing_key).hexdigest()[:16],
        "signature": hmac.new(signing_key, manifest_bytes, hashlib.sha256).hexdigest(),
    }


def _safe_component(value: Any) -> str:
    raw = str(value or "artifact")
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)
    safe = safe.strip("._")
    return safe or "artifact"


def _artifact_bundle_path(*, kind: str, logical_id: str | None, path: Path) -> str:
    stem = _safe_component(logical_id or path.stem)
    suffix = path.suffix or ".json"
    if stem.endswith(suffix):
        name = stem
    else:
        name = f"{stem}{suffix}"
    return f"artifacts/{_safe_component(kind)}/{name}"


def _artifact_ref(path_raw: Any, *, kind: str, logical_id: str | None = None) -> dict[str, Any]:
    if not isinstance(path_raw, str) or not path_raw:
        return {
            "kind": kind,
            "logical_id": logical_id,
            "path": None,
            "source_path": None,
            "bundle_path": None,
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
        "source_path": str(path),
        "bundle_path": _artifact_bundle_path(kind=kind, logical_id=logical_id, path=path)
        if exists
        else None,
        "exists": exists,
        "sha256": _file_sha256(path) if exists else None,
        "bytes": path.stat().st_size if exists else None,
    }


def _attach_entry_digest(row: dict[str, Any]) -> dict[str, Any]:
    scoped = dict(row)
    scoped.pop("platform_entry_digest", None)
    row["platform_entry_digest"] = _stable_digest(scoped)
    return row


def _run_export_row(entry: dict[str, Any]) -> dict[str, Any]:
    row = dict(entry)
    row["platform_entry_type"] = "run"
    row["artifacts"] = {
        "packet": _artifact_ref(
            row.get("packet_copy_path") or row.get("packet_path"),
            kind="run_packet",
            logical_id=str(row.get("run_id") or ""),
        )
    }
    return _attach_entry_digest(row)


def _outcome_export_row(entry: dict[str, Any]) -> dict[str, Any]:
    row = dict(entry)
    row["platform_entry_type"] = "outcome"
    row["artifacts"] = {
        "outcome": _artifact_ref(
            row.get("outcome_copy_path") or row.get("outcome_path"),
            kind="realized_outcome",
            logical_id=str(row.get("outcome_digest") or row.get("run_id") or ""),
        )
    }
    return _attach_entry_digest(row)


def _regime_export_row(entry: dict[str, Any]) -> dict[str, Any]:
    row = dict(entry)
    row["platform_entry_type"] = "regime_replay"
    row["artifacts"] = {
        "regime_replay": _artifact_ref(
            row.get("regime_copy_path") or row.get("regime_path"),
            kind="regime_replay",
            logical_id=str(row.get("report_digest") or row.get("run_id") or ""),
        )
    }
    return _attach_entry_digest(row)


def _promotion_export_row(record: dict[str, Any]) -> dict[str, Any]:
    row = dict(record)
    row["platform_entry_type"] = "promotion"
    attempt_report = build_promotion_attempt_report([row])
    row["promotion_attempt_report"] = {
        "latest": attempt_report["latest"],
        "blockers": attempt_report["blockers"],
        "categories": attempt_report["categories"],
    }
    return _attach_entry_digest(row)


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def _jsonl_payload_rows(payload: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in payload.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _artifact_refs(row: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    artifacts = row.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    refs: list[tuple[str, dict[str, Any]]] = []
    for name, value in artifacts.items():
        if isinstance(value, dict):
            refs.append((str(name), value))
    return refs


def _copy_artifact_ref(ref: dict[str, Any], export_dir: Path) -> Path | None:
    if not ref.get("exists"):
        return None
    source_raw = ref.get("source_path") or ref.get("path")
    bundle_raw = ref.get("bundle_path")
    if not isinstance(source_raw, str) or not source_raw:
        return None
    if not isinstance(bundle_raw, str) or not bundle_raw:
        return None
    source = Path(source_raw).expanduser().resolve()
    if not source.exists() or not source.is_file():
        return None
    destination = export_dir / bundle_raw
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(f".{destination.name}.tmp")
    shutil.copyfile(source, temp_path)
    os.replace(temp_path, destination)
    return destination


def _read_promotion_record(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _promotion_records(promotions_dir: Path | None) -> list[dict[str, Any]]:
    if promotions_dir is None:
        return []
    root = promotions_dir.expanduser().resolve()
    records_by_id: dict[str, dict[str, Any]] = {}
    attempts_dir = root / "attempts"
    if attempts_dir.exists():
        for path in sorted(attempts_dir.glob("*.json")):
            record = _read_promotion_record(path)
            promotion_id = record.get("promotion_id") if isinstance(record, dict) else None
            if isinstance(promotion_id, str) and promotion_id:
                records_by_id[promotion_id] = record
    latest = _read_promotion_record(root / "latest.json")
    latest_id = latest.get("promotion_id") if isinstance(latest, dict) else None
    if isinstance(latest_id, str) and latest_id:
        records_by_id[latest_id] = latest
    return sorted(
        records_by_id.values(),
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("promotion_id") or ""),
        ),
    )


def _effective_promotions_dir(
    *,
    ledger_dir: Path,
    promotions_dir: Path | None,
    discover_promotions: bool,
) -> Path | None:
    if promotions_dir is not None:
        return promotions_dir
    if not discover_promotions:
        return None
    candidate = ledger_dir.expanduser().resolve().parent / "promotions"
    return candidate if candidate.exists() and candidate.is_dir() else None


def _duckdb_import_sql() -> str:
    return "\n".join(
        [
            "-- Run from a validated platform export directory.",
            "-- First stage the bundle with: agent-harness ledger import research-run-platform <export-dir>",
            "CREATE OR REPLACE TABLE agent_harness_runs_stage AS",
            "SELECT * FROM read_json_auto('runs.jsonl');",
            "",
            "CREATE OR REPLACE TABLE agent_harness_outcomes_stage AS",
            "SELECT * FROM read_json_auto('outcomes.jsonl');",
            "",
            "CREATE OR REPLACE TABLE agent_harness_regimes_stage AS",
            "SELECT * FROM read_json_auto('regimes.jsonl');",
            "",
            "CREATE OR REPLACE TABLE agent_harness_promotions_stage AS",
            "SELECT * FROM read_json_auto('promotions.jsonl');",
            "",
            "CREATE OR REPLACE VIEW agent_harness_bundle_summary AS",
            "SELECT 'runs' AS table_name, count(*) AS row_count FROM agent_harness_runs_stage",
            "UNION ALL SELECT 'outcomes', count(*) FROM agent_harness_outcomes_stage",
            "UNION ALL SELECT 'regimes', count(*) FROM agent_harness_regimes_stage",
            "UNION ALL SELECT 'promotions', count(*) FROM agent_harness_promotions_stage;",
            "",
        ]
    )


def _read_json_object(path: Path, errors: list[str], label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{label} missing: {path}")
        return {}
    except JSONDecodeError as exc:
        errors.append(f"{label} is invalid JSON: {exc.msg}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object")
        return {}
    return payload


def _read_jsonl_file(path: Path, errors: list[str], label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        errors.append(f"{label} missing: {path.name}")
        return rows
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{path.name}:{line_number}: row must be a JSON object")
            continue
        rows.append(payload)
    return rows


def _bundle_member_path(root: Path, raw_path: str, errors: list[str], label: str) -> Path:
    path = (root / raw_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        errors.append(f"{label} escapes export directory")
        return root / "__invalid_export_path__"
    return path


def _validate_jsonl_rows(
    *,
    file_name: str,
    rows: list[dict[str, Any]],
    expected_type: str,
    id_field: str,
    errors: list[str],
) -> None:
    seen_ids: set[str] = set()
    seen_entry_digests: set[str] = set()
    for index, row in enumerate(rows, 1):
        row_label = f"{file_name}:{index}"
        if row.get("platform_entry_type") != expected_type:
            errors.append(
                f"{row_label}: platform_entry_type must be {expected_type!r}"
            )
        row_digest = row.get("platform_entry_digest")
        if not isinstance(row_digest, str) or not row_digest:
            errors.append(f"{row_label}: missing platform_entry_digest")
        else:
            scoped = dict(row)
            scoped.pop("platform_entry_digest", None)
            expected_digest = _stable_digest(scoped)
            if row_digest != expected_digest:
                errors.append(f"{row_label}: platform_entry_digest mismatch")
            if row_digest in seen_entry_digests:
                errors.append(f"{row_label}: duplicate platform_entry_digest")
            seen_entry_digests.add(row_digest)

        identifier = row.get(id_field)
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{row_label}: missing {id_field}")
        elif identifier in seen_ids:
            errors.append(f"{row_label}: duplicate {id_field} {identifier!r}")
        else:
            seen_ids.add(identifier)

        if expected_type in {"run", "outcome", "regime_replay"} and not _artifact_refs(row):
            errors.append(f"{row_label}: missing artifact reference")


def _validate_manifest_signature(
    *,
    root: Path,
    manifest_path: Path,
    signing_key: bytes | None,
    require_signature: bool,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    signature_path = root / PLATFORM_SIGNATURE_FILE
    present = signature_path.exists() and signature_path.is_file()
    status: dict[str, Any] = {
        "present": present,
        "required": require_signature,
        "verified": False,
        "path": str(signature_path),
        "algorithm": None,
        "key_id": None,
        "signed_sha256": None,
    }
    if not present:
        if require_signature:
            errors.append("manifest signature missing")
        return status

    signature = _read_json_object(signature_path, errors, "manifest signature")
    status["algorithm"] = signature.get("algorithm")
    status["key_id"] = signature.get("key_id")
    status["signed_sha256"] = signature.get("signed_sha256")
    if signature.get("schema_version") != PLATFORM_SIGNATURE_SCHEMA_VERSION:
        errors.append(
            f"manifest signature schema_version must be {PLATFORM_SIGNATURE_SCHEMA_VERSION!r}"
        )
    if signature.get("algorithm") != "hmac-sha256":
        errors.append("manifest signature algorithm must be 'hmac-sha256'")
    if signature.get("signed_file") != "manifest.json":
        errors.append("manifest signature signed_file must be 'manifest.json'")
    try:
        manifest_bytes = manifest_path.read_bytes()
    except FileNotFoundError:
        errors.append("manifest signature cannot be checked because manifest is missing")
        return status
    actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if signature.get("signed_sha256") != actual_manifest_sha:
        errors.append("manifest signature signed_sha256 mismatch")
    if signing_key is None:
        if require_signature:
            errors.append("manifest signature verification key missing")
        else:
            warnings.append("manifest signature present but no verification key was provided")
        return status

    expected = hmac.new(signing_key, manifest_bytes, hashlib.sha256).hexdigest()
    actual = signature.get("signature")
    if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
        errors.append("manifest signature mismatch")
        return status
    status["verified"] = True
    return status


def validate_platform_export(
    export_dir: Path,
    *,
    require_artifacts: bool = True,
    require_signature: bool = False,
    signing_key: str | None = None,
    signing_key_file: Path | None = None,
    target: str = PLATFORM_EXPORT_TARGET,
) -> dict[str, Any]:
    """Validate a platform export bundle before an external importer trusts it."""

    root = export_dir.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = root / "manifest.json"
    manifest = _read_json_object(manifest_path, errors, "manifest")
    signing_key_bytes = _resolve_signing_key(
        signing_key=signing_key,
        signing_key_file=signing_key_file,
    )
    effective_require_signature = require_signature or signing_key_bytes is not None
    signature_status = _validate_manifest_signature(
        root=root,
        manifest_path=manifest_path,
        signing_key=signing_key_bytes,
        require_signature=effective_require_signature,
        errors=errors,
        warnings=warnings,
    )
    checked_files: dict[str, dict[str, Any]] = {}
    actual_counts = {"runs": 0, "outcomes": 0, "regimes": 0, "promotions": 0}
    artifact_counts = {
        "total": 0,
        "verified": 0,
        "missing": 0,
        "sha256_mismatch": 0,
        "bytes_mismatch": 0,
    }

    if manifest:
        if manifest.get("schema_version") != PLATFORM_EXPORT_SCHEMA_VERSION:
            errors.append(
                f"manifest schema_version must be {PLATFORM_EXPORT_SCHEMA_VERSION!r}"
            )
        if manifest.get("target") != target:
            errors.append(f"manifest target must be {target!r}")
        if manifest.get("contract_version") != "1":
            errors.append("manifest contract_version must be '1'")

    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    file_digests = (
        manifest.get("file_digests")
        if isinstance(manifest.get("file_digests"), dict)
        else {}
    )
    counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    if manifest and not isinstance(files, dict):
        errors.append("manifest files must be a JSON object")
    if manifest and not isinstance(file_digests, dict):
        errors.append("manifest file_digests must be a JSON object")
    if manifest and not isinstance(counts, dict):
        errors.append("manifest counts must be a JSON object")

    all_rows: list[dict[str, Any]] = []
    for logical_name, (count_name, expected_type, id_field) in _JSONL_CONTRACTS.items():
        file_name = files.get(logical_name)
        if not isinstance(file_name, str) or not file_name:
            errors.append(f"manifest files.{logical_name} missing")
            continue
        path = _bundle_member_path(root, file_name, errors, file_name)
        actual_digest = _file_sha256(path)
        expected_digest = file_digests.get(file_name)
        checked_files[file_name] = {
            "exists": path.exists() and path.is_file(),
            "sha256": actual_digest,
            "expected_sha256": expected_digest,
            "bytes": path.stat().st_size if path.exists() and path.is_file() else None,
        }
        if actual_digest is None:
            errors.append(f"{file_name} missing")
            continue
        if not isinstance(expected_digest, str) or not expected_digest:
            errors.append(f"manifest file_digests missing {file_name}")
        elif actual_digest != expected_digest:
            errors.append(f"{file_name} sha256 mismatch")
        rows = _read_jsonl_file(path, errors, logical_name)
        actual_counts[count_name] = len(rows)
        expected_count = counts.get(count_name)
        if expected_count != len(rows):
            errors.append(
                f"{file_name} row count {len(rows)} does not match manifest counts.{count_name}={expected_count}"
            )
        _validate_jsonl_rows(
            file_name=file_name,
            rows=rows,
            expected_type=expected_type,
            id_field=id_field,
            errors=errors,
        )
        all_rows.extend(rows)

    duckdb_file = files.get("duckdb_import_sql")
    if not isinstance(duckdb_file, str) or not duckdb_file:
        errors.append("manifest files.duckdb_import_sql missing")
    else:
        path = _bundle_member_path(root, duckdb_file, errors, duckdb_file)
        actual_digest = _file_sha256(path)
        expected_digest = file_digests.get(duckdb_file)
        checked_files[duckdb_file] = {
            "exists": path.exists() and path.is_file(),
            "sha256": actual_digest,
            "expected_sha256": expected_digest,
            "bytes": path.stat().st_size if path.exists() and path.is_file() else None,
        }
        if actual_digest is None:
            errors.append(f"{duckdb_file} missing")
        elif actual_digest != expected_digest:
            errors.append(f"{duckdb_file} sha256 mismatch")

    for row in all_rows:
        row_label = (
            f"{row.get('platform_entry_type', 'row')}:"
            f"{row.get('run_id') or row.get('outcome_digest') or row.get('report_digest') or row.get('promotion_id') or '?'}"
        )
        for artifact_name, ref in _artifact_refs(row):
            artifact_counts["total"] += 1
            if not ref.get("exists"):
                artifact_counts["missing"] += 1
                if require_artifacts:
                    errors.append(f"{row_label}: artifact {artifact_name} is missing")
                continue
            bundle_raw = ref.get("bundle_path")
            if not isinstance(bundle_raw, str) or not bundle_raw:
                artifact_counts["missing"] += 1
                if require_artifacts:
                    errors.append(
                        f"{row_label}: artifact {artifact_name} missing bundle_path"
                    )
                continue
            artifact_path = _bundle_member_path(
                root,
                bundle_raw,
                errors,
                f"{row_label}: artifact {artifact_name}",
            )
            if not artifact_path.exists() or not artifact_path.is_file():
                artifact_counts["missing"] += 1
                if require_artifacts:
                    errors.append(
                        f"{row_label}: artifact {artifact_name} bundle copy missing"
                    )
                continue
            expected_bytes = ref.get("bytes")
            actual_bytes = artifact_path.stat().st_size
            if isinstance(expected_bytes, int) and actual_bytes != expected_bytes:
                artifact_counts["bytes_mismatch"] += 1
                errors.append(
                    f"{row_label}: artifact {artifact_name} byte count mismatch"
                )
                continue
            expected_sha = ref.get("sha256")
            actual_sha = _file_sha256(artifact_path)
            if isinstance(expected_sha, str) and actual_sha != expected_sha:
                artifact_counts["sha256_mismatch"] += 1
                errors.append(f"{row_label}: artifact {artifact_name} sha256 mismatch")
                continue
            artifact_counts["verified"] += 1

    latest = manifest.get("latest") if isinstance(manifest.get("latest"), dict) else {}
    if manifest and not isinstance(latest, dict):
        warnings.append("manifest latest is not a JSON object")

    return {
        "ok": not errors,
        "schema_version": PLATFORM_EXPORT_SCHEMA_VERSION,
        "target": target,
        "export_dir": str(root),
        "manifest": str(manifest_path),
        "export_id": manifest.get("export_id") if isinstance(manifest, dict) else None,
        "counts": actual_counts,
        "checked_files": checked_files,
        "artifact_counts": artifact_counts,
        "signature": signature_status,
        "errors": errors,
        "warnings": warnings,
    }


def _read_contract_tables(
    *,
    root: Path,
    manifest: dict[str, Any],
    errors: list[str],
) -> dict[str, list[dict[str, Any]]]:
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    tables: dict[str, list[dict[str, Any]]] = {
        "runs": [],
        "outcomes": [],
        "regimes": [],
        "promotions": [],
    }
    for logical_name, (table_name, _, _) in _JSONL_CONTRACTS.items():
        file_name = files.get(logical_name)
        if not isinstance(file_name, str) or not file_name:
            errors.append(f"manifest files.{logical_name} missing")
            continue
        path = _bundle_member_path(root, file_name, errors, file_name)
        tables[table_name] = _read_jsonl_file(path, errors, logical_name)
    return tables


def _artifact_ref_by_name(row: dict[str, Any], name: str) -> dict[str, Any] | None:
    for artifact_name, ref in _artifact_refs(row):
        if artifact_name == name:
            return ref
    return None


def _load_artifact_payload(
    *,
    root: Path,
    row: dict[str, Any],
    artifact_name: str,
    errors: list[str],
    row_label: str,
) -> dict[str, Any] | None:
    ref = _artifact_ref_by_name(row, artifact_name)
    if ref is None:
        errors.append(f"{row_label}: missing artifact {artifact_name}")
        return None
    bundle_raw = ref.get("bundle_path")
    if not isinstance(bundle_raw, str) or not bundle_raw:
        errors.append(f"{row_label}: artifact {artifact_name} missing bundle_path")
        return None
    path = _bundle_member_path(root, bundle_raw, errors, f"{row_label}: artifact {artifact_name}")
    payload = _read_json_object(path, errors, f"{row_label}: artifact {artifact_name}")
    return payload if payload else None


def _require_equal(
    *,
    left: Any,
    right: Any,
    errors: list[str],
    label: str,
) -> None:
    if left != right:
        errors.append(f"{label}: expected {right!r}, got {left!r}")


def _packet_artifact_summary(packet: dict[str, Any]) -> dict[str, Any]:
    inputs = packet.get("inputs", {}) if isinstance(packet.get("inputs"), dict) else {}
    return {
        "schema_version": packet.get("schema_version"),
        "run_id": packet.get("run_id"),
        "content_digest": packet.get("content_digest"),
        "created_at": packet.get("created_at"),
        "tickers": inputs.get("tickers"),
    }


def _outcome_artifact_summary(outcome: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": outcome.get("schema_version"),
        "run_id": outcome.get("run_id"),
        "content_digest": outcome.get("content_digest"),
        "outcome_digest": outcome.get("outcome_digest"),
        "window": outcome.get("window"),
        "scorecard": outcome.get("scorecard"),
    }


def _regime_artifact_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": report.get("schema_version"),
        "run_id": report.get("run_id"),
        "report_digest": report.get("report_digest"),
        "summary": report.get("summary"),
    }


def _ids(rows: list[dict[str, Any]], field: str) -> list[str]:
    return [str(row[field]) for row in rows if isinstance(row.get(field), str)]


def load_platform_export_bundle(
    export_dir: Path,
    *,
    require_artifacts: bool = True,
    require_signature: bool = False,
    signing_key: str | None = None,
    signing_key_file: Path | None = None,
    target: str = PLATFORM_EXPORT_TARGET,
) -> dict[str, Any]:
    """Validate and stage a platform export bundle for import.

    This is the receiving-side contract for ``research-run-platform``. It first
    runs the byte-level export validator, then checks semantic joins between
    JSONL rows and bundled artifact payloads.
    """

    root = export_dir.expanduser().resolve()
    validation = validate_platform_export(
        root,
        require_artifacts=require_artifacts,
        require_signature=require_signature,
        signing_key=signing_key,
        signing_key_file=signing_key_file,
        target=target,
    )
    errors: list[str] = list(validation["errors"])
    warnings: list[str] = list(validation["warnings"])
    manifest_path = root / "manifest.json"
    manifest = _read_json_object(manifest_path, errors, "manifest") if validation["ok"] else {}
    tables = {
        "runs": [],
        "outcomes": [],
        "regimes": [],
        "promotions": [],
    }
    records: list[dict[str, Any]] = []

    if manifest:
        tables = _read_contract_tables(root=root, manifest=manifest, errors=errors)

    runs = tables["runs"]
    outcomes = tables["outcomes"]
    regimes = tables["regimes"]
    promotions = tables["promotions"]
    run_ids = set(_ids(runs, "run_id"))

    outcomes_by_run: dict[str, list[dict[str, Any]]] = {}
    regimes_by_run: dict[str, list[dict[str, Any]]] = {}
    promotions_by_run: dict[str, list[dict[str, Any]]] = {}
    packet_summaries: dict[str, dict[str, Any]] = {}
    outcome_summaries: dict[str, dict[str, Any]] = {}
    regime_summaries: dict[str, dict[str, Any]] = {}
    orphan_outcomes: list[str] = []
    orphan_regimes: list[str] = []
    orphan_promotions: list[str] = []

    for row in runs:
        run_id = row.get("run_id")
        row_label = f"run:{run_id or '?'}"
        if not isinstance(run_id, str) or not run_id:
            continue
        packet = _load_artifact_payload(
            root=root,
            row=row,
            artifact_name="packet",
            errors=errors,
            row_label=row_label,
        )
        if packet is None:
            continue
        _require_equal(
            left=packet.get("run_id"),
            right=run_id,
            errors=errors,
            label=f"{row_label}: packet run_id",
        )
        _require_equal(
            left=packet.get("content_digest"),
            right=row.get("content_digest"),
            errors=errors,
            label=f"{row_label}: packet content_digest",
        )
        packet_summaries[run_id] = _packet_artifact_summary(packet)

    for row in outcomes:
        run_id = row.get("run_id")
        outcome_digest = row.get("outcome_digest")
        row_label = f"outcome:{outcome_digest or '?'}"
        if isinstance(run_id, str):
            outcomes_by_run.setdefault(run_id, []).append(row)
            if run_id not in run_ids:
                orphan_outcomes.append(str(outcome_digest or row_label))
                errors.append(f"{row_label}: run_id {run_id!r} is not present in runs")
        outcome = _load_artifact_payload(
            root=root,
            row=row,
            artifact_name="outcome",
            errors=errors,
            row_label=row_label,
        )
        if outcome is None:
            continue
        _require_equal(
            left=outcome.get("run_id"),
            right=run_id,
            errors=errors,
            label=f"{row_label}: artifact run_id",
        )
        _require_equal(
            left=outcome.get("content_digest"),
            right=row.get("content_digest"),
            errors=errors,
            label=f"{row_label}: artifact content_digest",
        )
        _require_equal(
            left=outcome.get("outcome_digest"),
            right=outcome_digest,
            errors=errors,
            label=f"{row_label}: artifact outcome_digest",
        )
        if isinstance(outcome_digest, str):
            outcome_summaries[outcome_digest] = _outcome_artifact_summary(outcome)

    for row in regimes:
        run_id = row.get("run_id")
        report_digest = row.get("report_digest")
        row_label = f"regime_replay:{report_digest or '?'}"
        if isinstance(run_id, str):
            regimes_by_run.setdefault(run_id, []).append(row)
            if run_id not in run_ids:
                orphan_regimes.append(str(report_digest or row_label))
                errors.append(f"{row_label}: run_id {run_id!r} is not present in runs")
        report = _load_artifact_payload(
            root=root,
            row=row,
            artifact_name="regime_replay",
            errors=errors,
            row_label=row_label,
        )
        if report is None:
            continue
        _require_equal(
            left=report.get("run_id"),
            right=run_id,
            errors=errors,
            label=f"{row_label}: artifact run_id",
        )
        _require_equal(
            left=report.get("report_digest"),
            right=report_digest,
            errors=errors,
            label=f"{row_label}: artifact report_digest",
        )
        if isinstance(report_digest, str):
            regime_summaries[report_digest] = _regime_artifact_summary(report)

    for row in promotions:
        run_id = row.get("run_id")
        promotion_id = row.get("promotion_id")
        if isinstance(run_id, str):
            promotions_by_run.setdefault(run_id, []).append(row)
            if run_id not in run_ids:
                orphan_promotions.append(str(promotion_id or f"promotion:{run_id}"))
                errors.append(
                    f"promotion:{promotion_id or '?'}: run_id {run_id!r} is not present in runs"
                )

    for row in runs:
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        run_outcomes = outcomes_by_run.get(run_id, [])
        run_regimes = regimes_by_run.get(run_id, [])
        run_promotions = promotions_by_run.get(run_id, [])
        latest_outcome = run_outcomes[-1] if run_outcomes else None
        latest_regime = run_regimes[-1] if run_regimes else None
        latest_promotion = run_promotions[-1] if run_promotions else None
        promotion_attempts = build_promotion_attempt_report(run_promotions)
        records.append(
            {
                "run_id": run_id,
                "content_digest": row.get("content_digest"),
                "run": row,
                "packet_artifact": packet_summaries.get(run_id),
                "outcome_count": len(run_outcomes),
                "regime_replay_count": len(run_regimes),
                "promotion_count": len(run_promotions),
                "latest_outcome_digest": latest_outcome.get("outcome_digest")
                if isinstance(latest_outcome, dict)
                else None,
                "latest_outcome_artifact": outcome_summaries.get(
                    str(latest_outcome.get("outcome_digest"))
                )
                if isinstance(latest_outcome, dict)
                else None,
                "latest_regime_report_digest": latest_regime.get("report_digest")
                if isinstance(latest_regime, dict)
                else None,
                "latest_regime_artifact": regime_summaries.get(
                    str(latest_regime.get("report_digest"))
                )
                if isinstance(latest_regime, dict)
                else None,
                "latest_promotion_id": latest_promotion.get("promotion_id")
                if isinstance(latest_promotion, dict)
                else None,
                "promotion_attempts": promotion_attempts,
            }
        )

    latest = manifest.get("latest") if isinstance(manifest.get("latest"), dict) else {}
    if records:
        _require_equal(
            left=latest.get("run_id"),
            right=records[-1]["run_id"],
            errors=errors,
            label="manifest latest.run_id",
        )
    if outcomes:
        _require_equal(
            left=latest.get("outcome_digest"),
            right=outcomes[-1].get("outcome_digest"),
            errors=errors,
            label="manifest latest.outcome_digest",
        )
    elif latest.get("outcome_digest") is not None:
        errors.append("manifest latest.outcome_digest must be null when outcomes table is empty")
    if regimes:
        _require_equal(
            left=latest.get("regime_report_digest"),
            right=regimes[-1].get("report_digest"),
            errors=errors,
            label="manifest latest.regime_report_digest",
        )
    elif latest.get("regime_report_digest") is not None:
        errors.append("manifest latest.regime_report_digest must be null when regimes table is empty")
    if promotions:
        _require_equal(
            left=latest.get("promotion_id"),
            right=promotions[-1].get("promotion_id"),
            errors=errors,
            label="manifest latest.promotion_id",
        )
    elif latest.get("promotion_id") is not None:
        errors.append("manifest latest.promotion_id must be null when promotions table is empty")

    summary = {
        "ok": not errors,
        "counts": {
            "runs": len(runs),
            "outcomes": len(outcomes),
            "regimes": len(regimes),
            "promotions": len(promotions),
        },
        "latest": latest,
        "runs_with_outcomes": sum(1 for row in records if row["outcome_count"] > 0),
        "runs_with_regime_replays": sum(
            1 for row in records if row["regime_replay_count"] > 0
        ),
        "runs_with_promotions": sum(1 for row in records if row["promotion_count"] > 0),
        "promotion_attempts": build_promotion_attempt_report(promotions),
        "orphan_outcomes": orphan_outcomes,
        "orphan_regimes": orphan_regimes,
        "orphan_promotions": orphan_promotions,
        "latest_run_eval_ok": records[-1]["run"].get("eval_ok") if records else None,
        "latest_outcome_ok": (
            records[-1]["latest_outcome_artifact"].get("scorecard", {}).get("ok")
            if records
            and isinstance(records[-1].get("latest_outcome_artifact"), dict)
            and isinstance(records[-1]["latest_outcome_artifact"].get("scorecard"), dict)
            else None
        ),
        "latest_regime_ok": (
            records[-1]["latest_regime_artifact"].get("summary", {}).get("ok")
            if records
            and isinstance(records[-1].get("latest_regime_artifact"), dict)
            and isinstance(records[-1]["latest_regime_artifact"].get("summary"), dict)
            else None
        ),
    }
    return {
        "schema_version": PLATFORM_IMPORT_SCHEMA_VERSION,
        "ok": not errors,
        "target": target,
        "export_dir": str(root),
        "manifest": manifest,
        "validation": validation,
        "summary": summary,
        "records": records,
        "tables": tables,
        "errors": errors,
        "warnings": warnings,
    }


def build_platform_export(
    *,
    ledger_entries: list[dict[str, Any]],
    outcome_entries: list[dict[str, Any]],
    regime_entries: list[dict[str, Any]] | None = None,
    ledger_dir: Path,
    promotions_dir: Path | None = None,
    discover_promotions: bool = False,
    target: str = PLATFORM_EXPORT_TARGET,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build a platform export manifest and file payloads."""

    if target != PLATFORM_EXPORT_TARGET:
        raise ValueError(f"unsupported platform export target: {target}")
    created_at = _utc_now()
    export_id = _new_export_id(created_at)
    scoped_promotions_dir = _effective_promotions_dir(
        ledger_dir=ledger_dir,
        promotions_dir=promotions_dir,
        discover_promotions=discover_promotions,
    )
    run_rows = [_run_export_row(entry) for entry in ledger_entries]
    outcome_rows = [_outcome_export_row(entry) for entry in outcome_entries]
    regime_rows = [_regime_export_row(entry) for entry in (regime_entries or [])]
    run_ids = {row.get("run_id") for row in run_rows if isinstance(row.get("run_id"), str)}
    promotion_rows = [
        _promotion_export_row(record)
        for record in _promotion_records(scoped_promotions_dir)
        if record.get("run_id") in run_ids
    ]
    files = {
        "runs_jsonl": "runs.jsonl",
        "outcomes_jsonl": "outcomes.jsonl",
        "regimes_jsonl": "regimes.jsonl",
        "promotions_jsonl": "promotions.jsonl",
        "duckdb_import_sql": "duckdb_import.sql",
    }
    file_payloads = {
        files["runs_jsonl"]: _jsonl(run_rows),
        files["outcomes_jsonl"]: _jsonl(outcome_rows),
        files["regimes_jsonl"]: _jsonl(regime_rows),
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
            "promotions_dir": (
                str(scoped_promotions_dir.expanduser().resolve())
                if scoped_promotions_dir
                else None
            ),
        },
        "counts": {
            "runs": len(run_rows),
            "outcomes": len(outcome_rows),
            "regimes": len(regime_rows),
            "promotions": len(promotion_rows),
        },
        "latest": {
            "run_id": run_rows[-1].get("run_id") if run_rows else None,
            "content_digest": run_rows[-1].get("content_digest") if run_rows else None,
            "outcome_digest": outcome_rows[-1].get("outcome_digest") if outcome_rows else None,
            "regime_report_digest": regime_rows[-1].get("report_digest") if regime_rows else None,
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
    regime_entries: list[dict[str, Any]] | None = None,
    ledger_dir: Path,
    output_dir: Path | None = None,
    promotions_dir: Path | None = None,
    discover_promotions: bool = False,
    signing_key: str | None = None,
    signing_key_file: Path | None = None,
    target: str = PLATFORM_EXPORT_TARGET,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Write a research-run-platform export bundle and return its manifest."""

    manifest, file_payloads = build_platform_export(
        ledger_entries=ledger_entries,
        outcome_entries=outcome_entries,
        regime_entries=regime_entries,
        ledger_dir=ledger_dir,
        promotions_dir=promotions_dir,
        discover_promotions=discover_promotions,
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
    for payload in file_payloads.values():
        if not payload.strip():
            continue
        try:
            rows = _jsonl_payload_rows(payload)
        except JSONDecodeError:
            continue
        for row in rows:
            for _, ref in _artifact_refs(row):
                artifact_path = _copy_artifact_ref(ref, export_dir)
                if artifact_path is not None:
                    key = (
                        f"artifact:{ref.get('kind')}:"
                        f"{ref.get('logical_id') or artifact_path.name}"
                    )
                    paths[key] = artifact_path
    manifest_payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    _atomic_write_text(paths["manifest"], manifest_payload)
    signing_key_bytes = _resolve_signing_key(
        signing_key=signing_key,
        signing_key_file=signing_key_file,
    )
    if signing_key_bytes is not None:
        signature_path = export_dir / PLATFORM_SIGNATURE_FILE
        _atomic_write_json(
            signature_path,
            _manifest_signature_payload(
                manifest_payload.encode("utf-8"),
                signing_key=signing_key_bytes,
            ),
        )
        paths["manifest_signature"] = signature_path
    _atomic_write_json(root / "latest.json", manifest)
    paths["latest_manifest"] = root / "latest.json"
    return manifest, paths
