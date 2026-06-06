from __future__ import annotations

from pathlib import Path

from agent_harness.promotion_gates import (
    DEFAULT_PROMOTION_GATES_FILE,
    PROMOTION_GATES_SCHEMA_VERSION,
    build_gates_from_calibration,
    load_promotion_gates,
    promotion_gates_summary,
    validate_promotion_gates,
    write_promotion_gates,
)


def test_load_promotion_gates_uses_default_file(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_PROMOTION_GATES_FILE
    path.write_text(
        """{
  "schema_version": "agent-harness.promotion-gates.v1",
  "min_runs": 5,
  "outcomes": {
    "min_outcomes": 20,
    "min_ok_rate": 0.9
  },
  "regimes": {
    "min_regime_replays": 1,
    "require_latest_run": true,
    "require_ok": true,
    "max_fragile_count": 0
  }
}
""",
        encoding="utf-8",
    )

    gates = load_promotion_gates(cwd=tmp_path)
    summary = promotion_gates_summary(gates)

    assert gates["loaded"]
    assert gates["source_path"] == str(path.resolve())
    assert gates["min_runs"] == 5
    assert gates["outcomes"]["min_outcomes"] == 20
    assert gates["regimes"]["require_latest_run"]
    assert summary["min_outcomes"] == 20
    assert summary["min_regime_replays"] == 1
    assert summary["regime_gate_count"] == 4
    assert len(summary["digest"]) == 64


def test_missing_default_promotion_gates_is_empty(tmp_path: Path) -> None:
    gates = load_promotion_gates(cwd=tmp_path)

    assert not gates["loaded"]
    assert gates["source_path"] == str((tmp_path / DEFAULT_PROMOTION_GATES_FILE))


def test_validate_promotion_gates_rejects_bad_values() -> None:
    problems = validate_promotion_gates(
        {
            "schema_version": PROMOTION_GATES_SCHEMA_VERSION,
            "min_runs": -1,
            "outcomes": {
                "min_outcomes": 3.5,
                "min_ok_rate": 1.2,
                "unknown": 1,
            },
            "regimes": {
                "min_regime_replays": -1,
                "require_latest_run": "yes",
                "max_worst_drawdown": -0.1,
                "unknown": 1,
            },
        }
    )

    assert "min_runs must be a non-negative integer" in problems
    assert "outcomes.min_outcomes must be a non-negative integer" in problems
    assert "outcomes.min_ok_rate must be between 0 and 1" in problems
    assert "outcomes.unknown is not supported" in problems
    assert "regimes.min_regime_replays must be a non-negative integer" in problems
    assert "regimes.require_latest_run must be a boolean" in problems
    assert "regimes.max_worst_drawdown must be non-negative" in problems
    assert "regimes.unknown is not supported" in problems


def test_tracked_promotion_gates_load() -> None:
    root = Path(__file__).resolve().parents[1]
    gates = load_promotion_gates(root / DEFAULT_PROMOTION_GATES_FILE)

    assert gates["loaded"]
    assert gates["outcomes"]["min_outcomes"] == 20
    assert gates["outcomes"]["min_ok_rate"] == 0.561111
    assert gates["outcomes"]["max_realized_drawdown"] == 0.0115
    assert gates["regimes"]["min_regime_replays"] == 1
    assert gates["regimes"]["require_latest_run"]
    assert gates["regimes"]["require_ok"]
    assert gates["regimes"]["max_fragile_count"] == 0
    assert gates["regimes"]["max_worst_drawdown"] == 0.08
    assert gates["regimes"]["min_worst_excess_cash"] == 0.0


def test_build_and_write_gates_from_ready_calibration(tmp_path: Path) -> None:
    calibration = {
        "ready": True,
        "outcome_count": 25,
        "min_sample": 20,
        "sentiment_min_sample": 10,
        "recommended_thresholds": {
            "min_outcomes": 20,
            "min_ok_rate": 0.9,
            "min_avg_excess_cash": 0.01,
            "max_avg_abs_forecast_error": 0.1,
            "max_realized_drawdown": 0.02,
            "min_sentiment_directional_count": 0,
        },
        "rationale": {"max_realized_drawdown": "p95"},
    }

    gates = build_gates_from_calibration(calibration, min_runs=4)
    path = write_promotion_gates(tmp_path / "gates.json", gates)
    loaded = load_promotion_gates(path)

    assert loaded["min_runs"] == 4
    assert loaded["outcomes"]["min_outcomes"] == 20
    assert loaded["regimes"]["min_regime_replays"] == 1
    assert loaded["regimes"]["require_latest_run"]
    assert loaded["regimes"]["require_ok"]
    assert loaded["regimes"]["max_fragile_count"] == 0
    assert loaded["source"]["sample_count"] == 25


def test_build_gates_from_calibration_rejects_insufficient_sample() -> None:
    try:
        build_gates_from_calibration(
            {
                "ready": False,
                "blockers": ["needs at least 20 realized outcomes for calibration"],
                "recommended_thresholds": {"min_outcomes": 20},
            }
        )
    except ValueError as exc:
        assert "needs at least 20 realized outcomes" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected insufficient calibration to fail")
