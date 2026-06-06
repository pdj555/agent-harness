from __future__ import annotations

import json
from pathlib import Path

from agent_harness.promotions import (
    build_promotion_record,
    read_promotion_attempts,
    write_promotion_record,
)


def _ready_report() -> dict:
    return {
        "promotion": {"ready": True, "blockers": [], "min_runs": 3},
        "run_count": 3,
        "latest_run_id": "run_ready",
        "trust": {
            "latest_policy_evaluation": {
                "ok": True,
                "blocking_change_count": 0,
                "allowed_change_count": 0,
            }
        },
        "outcomes": {
            "outcome_count": 1,
            "scorecard": {"ok_rate": 1.0},
        },
        "regimes": {
            "replay_count": 1,
            "latest_summary": {"ok": True, "fragile_count": 0},
        },
    }


def _entry() -> dict:
    return {
        "run_id": "run_ready",
        "content_digest": "abc",
        "top_loop": {"repo": "monte-carlo", "score": 0.3},
        "primary_pick": {"ticker": "AAPL", "weight": 0.6},
        "backtest": {"excess_return_vs_cash": 0.02},
        "stress": {"ok": True, "worst_margin": 0.03},
        "sentiment": {"ticker": "AAPL", "score": 0.3, "signal": "buy"},
    }


def test_build_promotion_record_promotes_ready_report() -> None:
    record = build_promotion_record(report=_ready_report(), latest_entry=_entry())

    assert record["status"] == "promoted"
    assert record["canonical_decision"]["primary_pick"]["ticker"] == "AAPL"
    assert record["canonical_decision"]["stress"]["ok"]
    assert record["canonical_decision"]["sentiment"]["signal"] == "buy"
    assert record["canonical_decision"]["trust"]["ok"]
    assert record["canonical_decision"]["outcomes"]["outcome_count"] == 1
    assert record["canonical_decision"]["regimes"]["latest_summary"]["ok"]
    assert record["blockers"] == []


def test_write_promotion_record_writes_canonical_only_when_promoted(tmp_path: Path) -> None:
    promoted = build_promotion_record(report=_ready_report(), latest_entry=_entry())
    paths = write_promotion_record(promoted, promotions_dir=tmp_path)

    assert paths["attempt_path"].exists()
    assert paths["canonical_path"].exists()
    assert (tmp_path / "latest.json").exists()

    blocked_report = {
        "promotion": {"ready": False, "blockers": ["latest run has dirty repos"]},
        "run_count": 1,
        "latest_run_id": "run_blocked",
    }
    blocked = build_promotion_record(report=blocked_report, latest_entry=_entry())
    paths = write_promotion_record(blocked, promotions_dir=tmp_path / "blocked")

    assert paths["attempt_path"].exists()
    assert paths["canonical_path"] is None
    assert not (tmp_path / "blocked" / "canonical.json").exists()


def test_read_promotion_attempts_dedupes_latest_and_sorts(tmp_path: Path) -> None:
    attempts_dir = tmp_path / "attempts"
    attempts_dir.mkdir()
    first = {
        "promotion_id": "promotion_first",
        "created_at": "2026-06-05T00:00:00+00:00",
        "run_id": "run_a",
        "status": "blocked",
    }
    latest = {
        "promotion_id": "promotion_latest",
        "created_at": "2026-06-05T00:01:00+00:00",
        "run_id": "run_b",
        "status": "promoted",
    }
    for record in (latest, first):
        (attempts_dir / f"{record['promotion_id']}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (tmp_path / "latest.json").write_text(
        json.dumps(latest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    attempts = read_promotion_attempts(tmp_path)

    assert [row["promotion_id"] for row in attempts] == [
        "promotion_first",
        "promotion_latest",
    ]
