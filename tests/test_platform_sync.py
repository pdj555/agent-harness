from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_harness.platform_sync import (
    PLATFORM_IMPORT_SCHEMA_VERSION,
    PLATFORM_EXPORT_SCHEMA_VERSION,
    PLATFORM_SIGNING_KEY_ENV,
    build_platform_export,
    load_platform_export_bundle,
    validate_platform_export,
    write_platform_export,
)


def test_build_platform_export_includes_artifact_hashes(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text('{"run_id":"run_a"}\n', encoding="utf-8")
    outcome_path = ledger_dir / "outcomes" / "run_a_window_digest.json"
    outcome_path.parent.mkdir(parents=True)
    outcome_path.write_text('{"run_id":"run_a","ok":true}\n', encoding="utf-8")
    regime_path = ledger_dir / "regimes" / "run_a_regime_digest.json"
    regime_path.parent.mkdir(parents=True)
    regime_path.write_text('{"run_id":"run_a","ok":false}\n', encoding="utf-8")

    manifest, payloads = build_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[
            {
                "run_id": "run_a",
                "outcome_digest": "outcome",
                "outcome_copy_path": str(outcome_path),
            }
        ],
        regime_entries=[
            {
                "run_id": "run_a",
                "report_digest": "regime",
                "regime_copy_path": str(regime_path),
            }
        ],
        ledger_dir=ledger_dir,
    )

    assert manifest["schema_version"] == PLATFORM_EXPORT_SCHEMA_VERSION
    assert manifest["counts"] == {"runs": 1, "outcomes": 1, "regimes": 1, "promotions": 0}
    assert "runs.jsonl" in payloads
    run_row = json.loads(payloads["runs.jsonl"])
    outcome_row = json.loads(payloads["outcomes.jsonl"])
    regime_row = json.loads(payloads["regimes.jsonl"])
    run_digest_payload = dict(run_row)
    run_digest = run_digest_payload.pop("platform_entry_digest")
    assert run_digest == hashlib.sha256(
        json.dumps(
            run_digest_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert run_row["artifacts"]["packet"]["sha256"] == hashlib.sha256(
        packet_path.read_bytes()
    ).hexdigest()
    assert (
        run_row["artifacts"]["packet"]["bundle_path"]
        == "artifacts/run_packet/run_a.json"
    )
    assert outcome_row["artifacts"]["outcome"]["sha256"] == hashlib.sha256(
        outcome_path.read_bytes()
    ).hexdigest()
    assert regime_row["artifacts"]["regime_replay"]["sha256"] == hashlib.sha256(
        regime_path.read_bytes()
    ).hexdigest()
    assert (
        regime_row["artifacts"]["regime_replay"]["bundle_path"]
        == "artifacts/regime_replay/regime.json"
    )
    assert payloads["duckdb_import.sql"].startswith("-- Run from a validated")
    assert "agent_harness_regimes_stage" in payloads["duckdb_import.sql"]


def test_build_platform_export_skips_promotion_for_nonexported_run(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        '{"run_id":"run_a","content_digest":"content"}\n',
        encoding="utf-8",
    )
    promotions_dir = tmp_path / "promotions"
    promotions_dir.mkdir()
    (promotions_dir / "latest.json").write_text(
        json.dumps(
            {
                "schema_version": "agent-harness.promotion.v1",
                "promotion_id": "promotion_stale",
                "run_id": "run_z",
                "content_digest": "stale",
                "status": "blocked",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manifest, payloads = build_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
        promotions_dir=promotions_dir,
    )

    assert manifest["counts"]["promotions"] == 0
    assert manifest["latest"]["promotion_id"] is None
    assert payloads["promotions.jsonl"] == ""


def test_platform_export_does_not_discover_colocated_promotions_by_default(
    tmp_path: Path,
) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        '{"run_id":"run_a","content_digest":"content"}\n',
        encoding="utf-8",
    )
    promotions_dir = tmp_path / "promotions"
    promotions_dir.mkdir()
    (promotions_dir / "latest.json").write_text(
        json.dumps(
            {
                "schema_version": "agent-harness.promotion.v1",
                "promotion_id": "promotion_discovered",
                "run_id": "run_a",
                "content_digest": "content",
                "status": "promoted",
                "blockers": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manifest, payloads = build_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
    )

    assert manifest["source"]["promotions_dir"] is None
    assert manifest["counts"]["promotions"] == 0
    assert manifest["latest"]["promotion_id"] is None
    assert payloads["promotions.jsonl"] == ""


def test_platform_export_can_discover_colocated_promotions_dir(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        '{"run_id":"run_a","content_digest":"content"}\n',
        encoding="utf-8",
    )
    promotions_dir = tmp_path / "promotions"
    promotions_dir.mkdir()
    (promotions_dir / "latest.json").write_text(
        json.dumps(
            {
                "schema_version": "agent-harness.promotion.v1",
                "promotion_id": "promotion_discovered",
                "run_id": "run_a",
                "content_digest": "content",
                "status": "promoted",
                "blockers": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manifest, payloads = build_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
        discover_promotions=True,
    )

    promotion_rows = [
        json.loads(line)
        for line in payloads["promotions.jsonl"].splitlines()
        if line.strip()
    ]
    assert manifest["source"]["promotions_dir"] == str(promotions_dir.resolve())
    assert manifest["counts"]["promotions"] == 1
    assert manifest["latest"]["promotion_id"] == "promotion_discovered"
    assert promotion_rows[0]["promotion_id"] == "promotion_discovered"


def test_platform_export_includes_promotion_attempt_history(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        json.dumps(
            {
                "schema_version": "agent-harness.run.v1",
                "run_id": "run_a",
                "content_digest": "content",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    promotions_dir = tmp_path / "promotions"
    attempts_dir = promotions_dir / "attempts"
    attempts_dir.mkdir(parents=True)
    blocked = {
        "schema_version": "agent-harness.promotion.v1",
        "promotion_id": "promotion_blocked",
        "created_at": "2026-06-05T00:00:00+00:00",
        "run_id": "run_a",
        "content_digest": "content",
        "status": "blocked",
        "blockers": [
            "latest backtest did not beat cash",
            "walk-forward backtest failed threshold",
        ],
    }
    promoted = {
        "schema_version": "agent-harness.promotion.v1",
        "promotion_id": "promotion_promoted",
        "created_at": "2026-06-05T00:01:00+00:00",
        "run_id": "run_a",
        "content_digest": "content",
        "status": "promoted",
        "blockers": [],
    }
    stale = {
        "schema_version": "agent-harness.promotion.v1",
        "promotion_id": "promotion_stale",
        "created_at": "2026-06-05T00:02:00+00:00",
        "run_id": "run_z",
        "content_digest": "stale",
        "status": "blocked",
    }
    for record in (blocked, promoted, stale):
        (attempts_dir / f"{record['promotion_id']}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (promotions_dir / "latest.json").write_text(
        json.dumps(promoted, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest, paths = write_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
        output_dir=tmp_path / "exports",
        promotions_dir=promotions_dir,
    )
    bundle = load_platform_export_bundle(paths["export_dir"])

    promotion_rows = [
        json.loads(line)
        for line in paths["promotions.jsonl"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert manifest["counts"]["promotions"] == 2
    assert manifest["latest"]["promotion_id"] == "promotion_promoted"
    assert [row["promotion_id"] for row in promotion_rows] == [
        "promotion_blocked",
        "promotion_promoted",
    ]
    assert [row["status"] for row in promotion_rows] == ["blocked", "promoted"]
    blocked_report = promotion_rows[0]["promotion_attempt_report"]
    promoted_report = promotion_rows[1]["promotion_attempt_report"]
    assert blocked_report["latest"]["promotion_id"] == "promotion_blocked"
    assert blocked_report["categories"]["top"][0]["category"] == "backtest"
    assert blocked_report["categories"]["top"][0]["count"] == 1
    assert {
        row["blocker"] for row in blocked_report["blockers"]["top"]
    } == {
        "latest backtest did not beat cash",
        "walk-forward backtest failed threshold",
    }
    assert promoted_report["latest"]["promotion_id"] == "promotion_promoted"
    assert promoted_report["blockers"]["unique_count"] == 0
    assert promoted_report["categories"]["unique_count"] == 0
    assert bundle["ok"], bundle["errors"]
    assert bundle["summary"]["runs_with_promotions"] == 1
    assert bundle["summary"]["promotion_attempts"]["attempt_count"] == 2
    assert bundle["summary"]["promotion_attempts"]["latest"]["promotion_id"] == "promotion_promoted"
    assert (
        bundle["summary"]["promotion_attempts"]["categories"]["top"][0]["category"]
        == "backtest"
    )
    assert bundle["summary"]["promotion_attempts"]["categories"]["top"][0]["count"] == 1
    assert bundle["records"][0]["promotion_count"] == 2
    assert bundle["records"][0]["latest_promotion_id"] == "promotion_promoted"
    assert bundle["records"][0]["promotion_attempts"]["attempt_count"] == 2
    assert (
        bundle["records"][0]["promotion_attempts"]["blockers"]["top"][0]["category"]
        == "backtest"
    )
    assert {
        row["blocker"]
        for row in bundle["records"][0]["promotion_attempts"]["blockers"]["top"]
    } == {
        "latest backtest did not beat cash",
        "walk-forward backtest failed threshold",
    }


def test_write_platform_export_writes_manifest_and_jsonl(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text('{"run_id":"run_a"}\n', encoding="utf-8")
    regime_path = ledger_dir / "regimes" / "run_a_regime_digest.json"
    regime_path.parent.mkdir(parents=True)
    regime_path.write_text('{"run_id":"run_a","ok":true}\n', encoding="utf-8")

    manifest, paths = write_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        regime_entries=[
            {
                "run_id": "run_a",
                "report_digest": "regime",
                "regime_copy_path": str(regime_path),
            }
        ],
        ledger_dir=ledger_dir,
        output_dir=tmp_path / "exports",
    )

    assert paths["export_dir"].exists()
    assert paths["manifest"].exists()
    assert paths["runs.jsonl"].exists()
    assert paths["outcomes.jsonl"].exists()
    assert paths["regimes.jsonl"].exists()
    assert paths["promotions.jsonl"].exists()
    assert paths["duckdb_import.sql"].exists()
    assert paths["artifact:run_packet:run_a"].exists()
    assert paths["artifact:regime_replay:regime"].exists()
    assert (tmp_path / "exports" / "latest.json").exists()
    saved_manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert saved_manifest["export_id"] == manifest["export_id"]
    assert saved_manifest["file_digests"]["runs.jsonl"] == hashlib.sha256(
        paths["runs.jsonl"].read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    validation = validate_platform_export(paths["export_dir"])
    assert validation["ok"]
    assert validation["artifact_counts"]["verified"] == 2


def test_load_platform_export_bundle_stages_joined_records(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        json.dumps(
            {
                "schema_version": "agent-harness.run.v1",
                "run_id": "run_a",
                "created_at": "2026-06-05T00:00:00+00:00",
                "content_digest": "content",
                "inputs": {"tickers": ["AAPL"]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    outcome_path = ledger_dir / "outcomes" / "outcome.json"
    outcome_path.parent.mkdir(parents=True)
    outcome_path.write_text(
        json.dumps(
            {
                "schema_version": "agent-harness.outcome.v1",
                "run_id": "run_a",
                "content_digest": "content",
                "outcome_digest": "outcome",
                "window": {"start_date": "2024-01-02", "end_date": "2024-01-04"},
                "scorecard": {"ok": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    regime_path = ledger_dir / "regimes" / "regime.json"
    regime_path.parent.mkdir(parents=True)
    regime_path.write_text(
        json.dumps(
            {
                "schema_version": "agent-harness.regime-replay.v1",
                "run_id": "run_a",
                "report_digest": "regime",
                "summary": {"ok": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    promotions_dir = tmp_path / "promotions"
    promotions_dir.mkdir()
    (promotions_dir / "latest.json").write_text(
        json.dumps(
            {
                "schema_version": "agent-harness.promotion.v1",
                "promotion_id": "promotion_a",
                "run_id": "run_a",
                "content_digest": "content",
                "status": "blocked",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _, paths = write_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
                "eval_ok": True,
            }
        ],
        outcome_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "outcome_digest": "outcome",
                "outcome_copy_path": str(outcome_path),
            }
        ],
        regime_entries=[
            {
                "run_id": "run_a",
                "report_digest": "regime",
                "regime_copy_path": str(regime_path),
            }
        ],
        ledger_dir=ledger_dir,
        output_dir=tmp_path / "exports",
        promotions_dir=promotions_dir,
    )

    bundle = load_platform_export_bundle(paths["export_dir"])

    assert bundle["ok"]
    assert bundle["schema_version"] == PLATFORM_IMPORT_SCHEMA_VERSION
    assert bundle["summary"]["counts"] == {
        "runs": 1,
        "outcomes": 1,
        "regimes": 1,
        "promotions": 1,
    }
    assert bundle["summary"]["latest_run_eval_ok"] is True
    assert bundle["summary"]["latest_outcome_ok"] is True
    assert bundle["summary"]["latest_regime_ok"] is True
    assert bundle["records"][0]["packet_artifact"]["tickers"] == ["AAPL"]
    assert bundle["records"][0]["latest_outcome_digest"] == "outcome"
    assert bundle["records"][0]["latest_regime_report_digest"] == "regime"
    assert bundle["records"][0]["latest_promotion_id"] == "promotion_a"


def test_load_platform_export_bundle_rejects_semantic_artifact_mismatch(
    tmp_path: Path,
) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        json.dumps(
            {
                "schema_version": "agent-harness.run.v1",
                "run_id": "run_b",
                "content_digest": "content",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _, paths = write_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
        output_dir=tmp_path / "exports",
    )

    validation = validate_platform_export(paths["export_dir"])
    bundle = load_platform_export_bundle(paths["export_dir"])

    assert validation["ok"]
    assert not bundle["ok"]
    assert any("packet run_id" in error for error in bundle["errors"])


def test_validate_platform_export_rejects_tampered_artifact(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text('{"run_id":"run_a"}\n', encoding="utf-8")

    _, paths = write_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
        output_dir=tmp_path / "exports",
    )

    paths["artifact:run_packet:run_a"].write_text(
        '{"run_id":"run_b"}\n',
        encoding="utf-8",
    )

    validation = validate_platform_export(paths["export_dir"])

    assert not validation["ok"]
    assert any("artifact packet" in error for error in validation["errors"])


def test_validate_platform_export_rejects_manifest_path_escape(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text('{"run_id":"run_a"}\n', encoding="utf-8")

    _, paths = write_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
        output_dir=tmp_path / "exports",
    )
    manifest_path = paths["manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["runs_jsonl"] = "../runs.jsonl"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validation = validate_platform_export(paths["export_dir"])

    assert not validation["ok"]
    assert any("escapes export directory" in error for error in validation["errors"])


def test_write_platform_export_signs_and_validates_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv(PLATFORM_SIGNING_KEY_ENV, raising=False)
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        '{"run_id":"run_a","content_digest":"content"}\n',
        encoding="utf-8",
    )
    signing_key_file = tmp_path / "platform.key"
    signing_key_file.write_text("production-secret\n", encoding="utf-8")
    wrong_key_file = tmp_path / "wrong.key"
    wrong_key_file.write_text("wrong-secret\n", encoding="utf-8")

    _, paths = write_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
        output_dir=tmp_path / "exports",
        signing_key_file=signing_key_file,
    )

    assert paths["manifest_signature"].exists()
    validation = validate_platform_export(
        paths["export_dir"],
        require_signature=True,
        signing_key_file=signing_key_file,
    )
    assert validation["ok"], validation["errors"]
    assert validation["signature"]["present"] is True
    assert validation["signature"]["verified"] is True

    wrong_key_validation = validate_platform_export(
        paths["export_dir"],
        require_signature=True,
        signing_key_file=wrong_key_file,
    )
    assert not wrong_key_validation["ok"]
    assert any(
        "manifest signature mismatch" in error
        for error in wrong_key_validation["errors"]
    )


def test_load_platform_export_bundle_rejects_tampered_signed_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv(PLATFORM_SIGNING_KEY_ENV, raising=False)
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        '{"run_id":"run_a","content_digest":"content"}\n',
        encoding="utf-8",
    )
    signing_key_file = tmp_path / "platform.key"
    signing_key_file.write_text("production-secret\n", encoding="utf-8")

    _, paths = write_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
        output_dir=tmp_path / "exports",
        signing_key_file=signing_key_file,
    )

    manifest_path = paths["manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"]["runs"] = 2
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validation = validate_platform_export(
        paths["export_dir"],
        require_signature=True,
        signing_key_file=signing_key_file,
    )
    assert not validation["ok"]
    assert validation["signature"]["present"] is True
    assert validation["signature"]["verified"] is False
    assert any(
        "manifest signature signed_sha256 mismatch" in error
        for error in validation["errors"]
    )
    assert any(
        "manifest signature mismatch" in error
        for error in validation["errors"]
    )

    bundle = load_platform_export_bundle(
        paths["export_dir"],
        require_signature=True,
        signing_key_file=signing_key_file,
    )
    assert not bundle["ok"]
    assert bundle["records"] == []
    assert any(
        "manifest signature mismatch" in error
        for error in bundle["errors"]
    )


def test_validate_platform_export_requires_signature(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv(PLATFORM_SIGNING_KEY_ENV, raising=False)
    ledger_dir = tmp_path / "ledger"
    packet_path = ledger_dir / "packets" / "run_a.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text('{"run_id":"run_a","content_digest":"content"}\n', encoding="utf-8")

    _, paths = write_platform_export(
        ledger_entries=[
            {
                "run_id": "run_a",
                "content_digest": "content",
                "packet_copy_path": str(packet_path),
            }
        ],
        outcome_entries=[],
        ledger_dir=ledger_dir,
        output_dir=tmp_path / "exports",
    )

    validation = validate_platform_export(
        paths["export_dir"],
        require_signature=True,
    )

    assert not validation["ok"]
    assert any("manifest signature missing" in error for error in validation["errors"])
