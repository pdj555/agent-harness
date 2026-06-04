from __future__ import annotations

from pathlib import Path

from agent_harness.promotions import build_promotion_record, write_promotion_record


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
    }


def _entry() -> dict:
    return {
        "run_id": "run_ready",
        "content_digest": "abc",
        "top_loop": {"repo": "monte-carlo", "score": 0.3},
        "primary_pick": {"ticker": "AAPL", "weight": 0.6},
        "backtest": {"excess_return_vs_cash": 0.02},
        "stress": {"ok": True, "worst_margin": 0.03},
    }


def test_build_promotion_record_promotes_ready_report() -> None:
    record = build_promotion_record(report=_ready_report(), latest_entry=_entry())

    assert record["status"] == "promoted"
    assert record["canonical_decision"]["primary_pick"]["ticker"] == "AAPL"
    assert record["canonical_decision"]["stress"]["ok"]
    assert record["canonical_decision"]["trust"]["ok"]
    assert record["canonical_decision"]["outcomes"]["outcome_count"] == 1
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
