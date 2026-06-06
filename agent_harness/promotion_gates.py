"""Versioned promotion gate defaults for ledger readiness commands."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


PROMOTION_GATES_SCHEMA_VERSION = "agent-harness.promotion-gates.v1"
DEFAULT_PROMOTION_GATES_FILE = "agent-harness.gates.json"

_OUTCOME_KEYS = {
    "min_outcomes": int,
    "min_ok_rate": float,
    "min_avg_excess_cash": float,
    "min_avg_excess_equal_weight": float,
    "max_avg_abs_forecast_error": float,
    "max_realized_drawdown": float,
    "min_sentiment_directional_count": int,
    "min_sentiment_hit_rate": float,
    "min_avg_sentiment_alignment": float,
}

_REGIME_KEYS = {
    "min_regime_replays": int,
    "require_latest_run": bool,
    "require_ok": bool,
    "max_fragile_count": int,
    "max_worst_drawdown": float,
    "min_worst_excess_cash": float,
    "min_worst_excess_equal_weight": float,
}

DEFAULT_REGIME_PROMOTION_GATES = {
    "min_regime_replays": 1,
    "require_latest_run": True,
    "require_ok": True,
    "max_fragile_count": 0,
    "max_worst_drawdown": 0.08,
    "min_worst_excess_cash": 0.0,
}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def empty_promotion_gates(*, source_path: Path | None = None) -> dict[str, Any]:
    """Return empty gate defaults."""

    return {
        "schema_version": PROMOTION_GATES_SCHEMA_VERSION,
        "source_path": str(source_path) if source_path else None,
        "loaded": False,
        "min_runs": None,
        "outcomes": {},
        "regimes": {},
    }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_rate(value: Any, path: str, problems: list[str]) -> None:
    number = _number(value)
    if number is None or number < 0.0 or number > 1.0:
        problems.append(f"{path} must be between 0 and 1")


def _validate_non_negative(value: Any, path: str, problems: list[str]) -> None:
    number = _number(value)
    if number is None or number < 0.0:
        problems.append(f"{path} must be non-negative")


def _validate_int(value: Any, path: str, problems: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        problems.append(f"{path} must be a non-negative integer")


def _validate_bool(value: Any, path: str, problems: list[str]) -> None:
    if not isinstance(value, bool):
        problems.append(f"{path} must be a boolean")


def _validate_number(value: Any, path: str, problems: list[str]) -> None:
    if _number(value) is None:
        problems.append(f"{path} must be a number")


def validate_promotion_gates(gates: dict[str, Any]) -> list[str]:
    """Return schema problems for promotion gate defaults."""

    problems: list[str] = []
    if gates.get("schema_version") != PROMOTION_GATES_SCHEMA_VERSION:
        problems.append("unsupported promotion gates schema_version")
    if "min_runs" in gates and gates.get("min_runs") is not None:
        _validate_int(gates.get("min_runs"), "min_runs", problems)
    outcomes = gates.get("outcomes", {})
    if not isinstance(outcomes, dict):
        problems.append("outcomes must be a JSON object")
        outcomes = {}
    for key in outcomes:
        if key not in _OUTCOME_KEYS:
            problems.append(f"outcomes.{key} is not supported")
    for key, expected_type in _OUTCOME_KEYS.items():
        if key not in outcomes or outcomes[key] is None:
            continue
        path = f"outcomes.{key}"
        if expected_type is int:
            _validate_int(outcomes[key], path, problems)
        elif key in {"min_ok_rate", "min_sentiment_hit_rate"}:
            _validate_rate(outcomes[key], path, problems)
        elif key.startswith("max_") or key in {
            "min_avg_excess_cash",
            "min_avg_excess_equal_weight",
            "min_avg_sentiment_alignment",
        }:
            _validate_non_negative(outcomes[key], path, problems)
    regimes = gates.get("regimes", {})
    if not isinstance(regimes, dict):
        problems.append("regimes must be a JSON object")
        regimes = {}
    for key in regimes:
        if key not in _REGIME_KEYS:
            problems.append(f"regimes.{key} is not supported")
    for key, expected_type in _REGIME_KEYS.items():
        if key not in regimes or regimes[key] is None:
            continue
        path = f"regimes.{key}"
        if expected_type is int:
            _validate_int(regimes[key], path, problems)
        elif expected_type is bool:
            _validate_bool(regimes[key], path, problems)
        elif key == "max_worst_drawdown":
            _validate_non_negative(regimes[key], path, problems)
        else:
            _validate_number(regimes[key], path, problems)
    return problems


def load_promotion_gates(
    path: Path | None = None,
    *,
    cwd: Path | None = None,
    disabled: bool = False,
) -> dict[str, Any]:
    """Load promotion gate defaults from JSON.

    Missing default files mean no configured defaults. Missing explicit files are
    operator errors.
    """

    root = cwd or Path.cwd()
    source = path.expanduser() if path is not None else root / DEFAULT_PROMOTION_GATES_FILE
    if disabled:
        return empty_promotion_gates(source_path=source)
    if not source.exists():
        if path is not None:
            raise FileNotFoundError(str(source))
        return empty_promotion_gates(source_path=source)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("promotion gates must be a JSON object")
    problems = validate_promotion_gates(payload)
    if problems:
        raise ValueError("; ".join(problems))
    loaded = dict(payload)
    loaded["source_path"] = str(source.expanduser().resolve())
    loaded["loaded"] = True
    loaded.setdefault("min_runs", None)
    loaded.setdefault("outcomes", {})
    loaded.setdefault("regimes", {})
    return loaded


def promotion_gates_digest(gates: dict[str, Any]) -> str:
    """Return a stable digest for gate defaults."""

    scoped = {
        key: value
        for key, value in gates.items()
        if key not in {"source_path", "loaded"}
    }
    encoded = json.dumps(scoped, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def promotion_gates_summary(gates: dict[str, Any]) -> dict[str, Any]:
    """Return a compact operator-facing gate policy summary."""

    outcomes = gates.get("outcomes", {}) if isinstance(gates.get("outcomes"), dict) else {}
    regimes = gates.get("regimes", {}) if isinstance(gates.get("regimes"), dict) else {}
    return {
        "schema_version": gates.get("schema_version", PROMOTION_GATES_SCHEMA_VERSION),
        "source_path": gates.get("source_path"),
        "loaded": bool(gates.get("loaded")),
        "digest": promotion_gates_digest(gates),
        "min_runs": gates.get("min_runs"),
        "min_outcomes": outcomes.get("min_outcomes"),
        "outcome_gate_count": len([key for key, value in outcomes.items() if value is not None]),
        "min_regime_replays": regimes.get("min_regime_replays"),
        "regime_gate_count": len([key for key, value in regimes.items() if value is not None]),
    }


def build_gates_from_calibration(
    calibration: dict[str, Any],
    *,
    min_runs: int = 3,
) -> dict[str, Any]:
    """Build a gates payload from a ready outcome calibration report."""

    if not calibration.get("ready"):
        blockers = calibration.get("blockers", [])
        rendered = "; ".join(str(blocker) for blocker in blockers) or "calibration is not ready"
        raise ValueError(f"cannot write promotion gates: {rendered}")
    recommended = calibration.get("recommended_thresholds")
    if not isinstance(recommended, dict):
        raise ValueError("calibration missing recommended_thresholds")
    outcomes = {
        key: recommended.get(key)
        for key in _OUTCOME_KEYS
        if key in recommended
    }
    gates = {
        "schema_version": PROMOTION_GATES_SCHEMA_VERSION,
        "min_runs": int(min_runs),
        "outcomes": outcomes,
        "regimes": dict(DEFAULT_REGIME_PROMOTION_GATES),
        "source": {
            "method": "agent-harness ledger calibrate-outcomes",
            "calibrated_ready": bool(calibration.get("ready")),
            "sample_count": calibration.get("outcome_count"),
            "min_sample": calibration.get("min_sample"),
            "sentiment_min_sample": calibration.get("sentiment_min_sample"),
            "rationale": calibration.get("rationale", {}),
        },
    }
    problems = validate_promotion_gates(gates)
    if problems:
        raise ValueError("; ".join(problems))
    return gates


def write_promotion_gates(path: Path, gates: dict[str, Any]) -> Path:
    """Validate and atomically write a promotion-gates JSON file."""

    problems = validate_promotion_gates(gates)
    if problems:
        raise ValueError("; ".join(problems))
    output_path = path.expanduser().resolve()
    _atomic_write_json(output_path, gates)
    return output_path
