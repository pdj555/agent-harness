"""Verify the sibling research-run-platform receiver against a promotion-bearing export."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECEIVER_ROOT = REPO_ROOT.parent / "research-run-platform"


def _add_import_root(path: Path) -> None:
    scoped = str(path.expanduser().resolve())
    if scoped not in sys.path:
        sys.path.insert(0, scoped)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_receiver(
    *,
    receiver_root: Path,
    work_dir: Path | None = None,
    require_evidence_promotion_attempts: bool = False,
) -> dict[str, Any]:
    receiver_root = receiver_root.expanduser().resolve()
    _assert(
        (receiver_root / "research_run_platform" / "importer.py").exists(),
        f"research-run-platform receiver missing: {receiver_root}",
    )
    _add_import_root(REPO_ROOT)
    _add_import_root(receiver_root)

    from agent_harness.ledger import (  # noqa: PLC0415
        read_ledger_entries,
        read_outcome_entries,
        read_regime_entries,
    )
    from agent_harness.platform_sync import write_platform_export  # noqa: PLC0415
    from research_run_platform.importer import (  # noqa: PLC0415
        get_run,
        get_run_evidence,
        ingest_bundle,
        stats,
    )
    from tools.build_ci_production_fixture import build_fixture  # noqa: PLC0415

    root = (
        work_dir.expanduser().resolve()
        if work_dir is not None
        else Path(tempfile.mkdtemp(prefix="agent-harness-receiver."))
    )
    paths = build_fixture(root / "fixture")
    ledger_dir = Path(paths["ledger_dir"])
    _, export_paths = write_platform_export(
        ledger_entries=read_ledger_entries(ledger_dir),
        outcome_entries=read_outcome_entries(ledger_dir),
        regime_entries=read_regime_entries(ledger_dir),
        ledger_dir=ledger_dir,
        output_dir=root / "exports",
        discover_promotions=True,
    )
    db_path = root / "runs.sqlite"
    ingest = ingest_bundle(export_paths["export_dir"], db_path=db_path)
    summary = stats(db_path=db_path)
    record = get_run("run_ci_3", db_path=db_path)
    evidence = get_run_evidence("run_ci_3", db_path=db_path)

    promotion_attempts = record.get("promotion_attempts")
    _assert(isinstance(promotion_attempts, dict), "run record missing promotion_attempts")
    categories = promotion_attempts.get("categories")
    _assert(isinstance(categories, dict), "promotion_attempts missing categories")
    top_categories = categories.get("top")
    _assert(isinstance(top_categories, list), "promotion categories top must be a list")
    _assert(top_categories, "promotion categories top is empty")
    _assert(
        top_categories[0].get("category") == "backtest",
        f"expected backtest category, got {top_categories[0]}",
    )
    _assert(
        promotion_attempts.get("attempt_count") == 2,
        f"expected 2 promotion attempts, got {promotion_attempts.get('attempt_count')}",
    )
    promotion = evidence.get("promotion") if isinstance(evidence.get("promotion"), dict) else {}
    evidence_attempts = (
        promotion.get("attempts")
        if isinstance(promotion.get("attempts"), dict)
        else None
    )
    _assert(promotion.get("count") == 2, f"expected promotion count 2, got {promotion}")
    _assert(
        promotion.get("latest_promotion_id") == "promotion_ci_promoted",
        f"unexpected latest promotion: {promotion}",
    )
    _assert(evidence.get("ready") is True, f"expected ready evidence, got {evidence}")
    evidence_exposes_attempts = (
        isinstance(evidence_attempts, dict)
        and evidence_attempts.get("attempt_count") == 2
    )
    if require_evidence_promotion_attempts:
        _assert(
            evidence_exposes_attempts,
            "receiver evidence payload does not expose promotion attempts",
        )

    return {
        "ok": True,
        "work_dir": str(root),
        "db_path": str(db_path),
        "export_dir": str(export_paths["export_dir"]),
        "ingest": ingest,
        "receiver_counts": summary["counts"],
        "run_id": "run_ci_3",
        "promotion_attempt_count": promotion_attempts["attempt_count"],
        "top_promotion_blocker_category": top_categories[0]["category"],
        "latest_promotion_id": promotion["latest_promotion_id"],
        "evidence_exposes_promotion_attempts": evidence_exposes_attempts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--receiver-root",
        type=Path,
        default=DEFAULT_RECEIVER_ROOT,
        help="Path to the sibling research-run-platform checkout.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Directory for generated fixture/export/database files.",
    )
    parser.add_argument(
        "--require-evidence-promotion-attempts",
        action="store_true",
        help="Fail unless receiver evidence JSON exposes promotion.attempts.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    result = verify_receiver(
        receiver_root=args.receiver_root,
        work_dir=args.work_dir,
        require_evidence_promotion_attempts=args.require_evidence_promotion_attempts,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("research-run-platform receiver verification: ok")
        print(f"DB: {result['db_path']}")
        print(f"Export: {result['export_dir']}")
        print(f"Run: {result['run_id']}")
        print(
            "Promotion attempts: "
            f"count={result['promotion_attempt_count']} "
            f"top_category={result['top_promotion_blocker_category']} "
            f"latest={result['latest_promotion_id']}"
        )
        print(
            "Evidence API promotion attempts: "
            f"{result['evidence_exposes_promotion_attempts']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
