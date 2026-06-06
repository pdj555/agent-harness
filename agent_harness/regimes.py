"""Deterministic regime replay for saved capital decision packets."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_harness.outcomes import evaluate_outcome
from agent_harness.packets import packet_digest


REGIME_REPLAY_SCHEMA_VERSION = "agent-harness.regime-replay.v1"
DEFAULT_REGIME_START_DATE = "2024-01-02"
DEFAULT_MAX_REGIME_DRAWDOWN = 0.08


@dataclass(frozen=True)
class RegimeDefinition:
    name: str
    description: str


REGIME_DEFINITIONS: tuple[RegimeDefinition, ...] = (
    RegimeDefinition(
        name="primary_trend",
        description="Primary pick trends hard while other names drift up.",
    ),
    RegimeDefinition(
        name="primary_reversal",
        description="Primary pick reverses lower while alternatives rise.",
    ),
    RegimeDefinition(
        name="shock_recovery",
        description="A sharp mid-window drawdown partially recovers by the horizon.",
    ),
    RegimeDefinition(
        name="cash_drag_rally",
        description="Every risky asset rallies, exposing idle cash drag.",
    ),
)


def default_regime_dir(cwd: Path | None = None) -> Path:
    """Return the default local deterministic-regime artifact directory."""

    return (cwd or Path.cwd()) / ".agent-harness" / "regimes"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _reject_unsafe_ticker(ticker: str) -> None:
    if "/" in ticker or "\\" in ticker or ticker in {"", ".", ".."}:
        raise ValueError(f"ticker cannot contain path separators: {ticker!r}")


def _action_plan(packet: dict[str, Any]) -> dict[str, Any]:
    run = packet.get("engine_runs", {}).get("monte_carlo")
    payload = run.get("payload", {}) if isinstance(run, dict) else {}
    action_plan = payload.get("action_plan", {}) if isinstance(payload, dict) else {}
    return action_plan if isinstance(action_plan, dict) else {}


def _allocations(packet: dict[str, Any]) -> dict[str, float]:
    run = packet.get("engine_runs", {}).get("monte_carlo")
    payload = run.get("payload", {}) if isinstance(run, dict) else {}
    raw_allocations = payload.get("allocations", {}) if isinstance(payload, dict) else {}
    allocations: dict[str, float] = {}
    if isinstance(raw_allocations, dict):
        for ticker, row in raw_allocations.items():
            if isinstance(row, dict) and row.get("weight") is not None:
                allocations[str(ticker)] = float(row["weight"])
    if allocations:
        return allocations
    primary = _action_plan(packet).get("primary_pick", {})
    if isinstance(primary, dict) and primary.get("ticker") and primary.get("weight") is not None:
        return {str(primary["ticker"]): float(primary["weight"])}
    return {}


def _input_tickers(packet: dict[str, Any], allocations: dict[str, float]) -> list[str]:
    raw = packet.get("inputs", {}).get("tickers")
    tickers = [str(ticker) for ticker in raw] if isinstance(raw, list) else []
    for ticker in allocations:
        if ticker not in tickers:
            tickers.append(ticker)
    return tickers


def _primary_ticker(
    packet: dict[str, Any],
    *,
    tickers: list[str],
    allocations: dict[str, float],
) -> str:
    primary = _action_plan(packet).get("primary_pick", {})
    if isinstance(primary, dict) and primary.get("ticker"):
        return str(primary["ticker"])
    if allocations:
        return max(allocations.items(), key=lambda item: item[1])[0]
    if tickers:
        return tickers[0]
    raise ValueError("packet has no tickers to replay")


def _dates(*, rows: int, start_date: str) -> list[str]:
    if rows < 2:
        raise ValueError("regime replay requires at least two price rows")
    start = date.fromisoformat(start_date)
    return [(start + timedelta(days=index)).isoformat() for index in range(rows)]


def _interpolate_anchors(anchors: list[float], rows: int) -> list[float]:
    if len(anchors) < 2:
        raise ValueError("at least two anchors are required")
    if rows < 2:
        raise ValueError("at least two rows are required")
    if rows == len(anchors):
        return anchors
    last_anchor_index = len(anchors) - 1
    values: list[float] = []
    for row_index in range(rows):
        position = row_index * last_anchor_index / (rows - 1)
        lower_index = int(math.floor(position))
        upper_index = int(math.ceil(position))
        if lower_index == upper_index:
            values.append(anchors[lower_index])
            continue
        span = position - lower_index
        lower = anchors[lower_index]
        upper = anchors[upper_index]
        values.append(lower + (upper - lower) * span)
    return values


def _ticker_factors(regime_name: str, *, ticker: str, primary_ticker: str, rows: int) -> list[float]:
    is_primary = ticker == primary_ticker
    if regime_name == "primary_trend":
        return _interpolate_anchors([1.0, 1.25 if is_primary else 1.04], rows)
    if regime_name == "primary_reversal":
        return _interpolate_anchors([1.0, 0.90 if is_primary else 1.05], rows)
    if regime_name == "shock_recovery":
        anchors = (
            [1.0, 0.94, 0.84, 0.98, 1.08]
            if is_primary
            else [1.0, 0.97, 0.94, 0.99, 1.01]
        )
        return _interpolate_anchors(anchors, rows)
    if regime_name == "cash_drag_rally":
        return _interpolate_anchors([1.0, 1.12], rows)
    raise ValueError(f"unknown regime: {regime_name}")


def write_regime_price_csvs(
    packet: dict[str, Any],
    *,
    regime_name: str,
    output_dir: Path,
    rows: int = 5,
    start_date: str = DEFAULT_REGIME_START_DATE,
) -> Path:
    """Write deterministic ``Date,Close`` CSVs for one replay regime."""

    allocations = _allocations(packet)
    if not allocations:
        raise ValueError("packet has no Monte Carlo allocation or primary pick")
    tickers = _input_tickers(packet, allocations)
    if not tickers:
        raise ValueError("packet has no tickers to replay")
    for ticker in tickers:
        _reject_unsafe_ticker(ticker)
    primary = _primary_ticker(packet, tickers=tickers, allocations=allocations)
    row_dates = _dates(rows=rows, start_date=start_date)
    price_dir = output_dir.expanduser().resolve()
    price_dir.mkdir(parents=True, exist_ok=True)

    for ticker in tickers:
        factors = _ticker_factors(regime_name, ticker=ticker, primary_ticker=primary, rows=rows)
        temp_path = price_dir / f".{ticker}.csv.tmp"
        final_path = price_dir / f"{ticker}.csv"
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["Date", "Close"])
            writer.writeheader()
            for row_date, factor in zip(row_dates, factors):
                writer.writerow({"Date": row_date, "Close": f"{100.0 * factor:.6f}"})
        os.replace(temp_path, final_path)

    return price_dir


def _fragility_reasons(
    outcome: dict[str, Any],
    *,
    regime_name: str,
    max_drawdown: float,
) -> list[str]:
    scorecard = outcome.get("scorecard", {})
    returns = outcome.get("returns", {})
    risk = outcome.get("risk", {})
    primary = outcome.get("primary_pick", {})
    reasons: list[str] = []
    allocation_return = returns.get("allocation")
    excess_cash = returns.get("excess_vs_cash")
    excess_equal = returns.get("excess_vs_equal_weight")
    primary_return = primary.get("realized_return")
    realized_drawdown = risk.get("realized_max_drawdown")

    if regime_name == "primary_trend":
        if not scorecard.get("beat_cash"):
            reasons.append("failed to capture positive primary trend")
        if not scorecard.get("primary_hit"):
            reasons.append("primary pick did not trend up")
    elif regime_name == "primary_reversal":
        if isinstance(allocation_return, (int, float)) and allocation_return < -0.02:
            reasons.append("portfolio lost more than reversal budget")
        elif not isinstance(allocation_return, (int, float)):
            reasons.append("portfolio reversal return missing")
        if isinstance(excess_equal, (int, float)) and excess_equal < -1e-9:
            reasons.append("underperformed equal weight during primary reversal")
        elif not isinstance(excess_equal, (int, float)):
            reasons.append("primary reversal excess return missing")
    elif regime_name == "shock_recovery":
        if isinstance(allocation_return, (int, float)) and allocation_return < 0.0:
            reasons.append("portfolio failed to recover after shock")
        elif not isinstance(allocation_return, (int, float)):
            reasons.append("shock recovery return missing")
    elif regime_name == "cash_drag_rally":
        if isinstance(excess_equal, (int, float)) and excess_equal < -1e-9:
            reasons.append("failed to keep pace with broad risky-asset rally")
        elif not isinstance(excess_equal, (int, float)):
            reasons.append("cash-drag excess return missing")
    else:
        if not scorecard.get("beat_cash"):
            reasons.append("failed to beat cash")
        if not scorecard.get("beat_equal_weight"):
            reasons.append("failed to beat equal weight")
        if not scorecard.get("primary_hit"):
            reasons.append("primary pick lost capital")

    if (
        regime_name != "primary_reversal"
        and isinstance(primary_return, (int, float))
        and primary_return < 0.0
    ):
        reasons.append("primary pick lost capital")
    if isinstance(realized_drawdown, (int, float)) and realized_drawdown > max_drawdown:
        reasons.append(
            f"realized drawdown {realized_drawdown:.6f} above max {max_drawdown:.6f}"
        )
    return reasons


def _regime_result(
    *,
    definition: RegimeDefinition,
    price_dir: Path,
    outcome: dict[str, Any],
    max_drawdown: float,
) -> dict[str, Any]:
    fragility_reasons = _fragility_reasons(
        outcome,
        regime_name=definition.name,
        max_drawdown=max_drawdown,
    )
    attribution = outcome.get("attribution", {})
    return {
        "name": definition.name,
        "description": definition.description,
        "price_dir": str(price_dir.expanduser().resolve()),
        "outcome_digest": outcome.get("outcome_digest"),
        "sources": outcome.get("sources", {}),
        "window": outcome.get("window", {}),
        "primary_pick": outcome.get("primary_pick", {}),
        "returns": outcome.get("returns", {}),
        "risk": outcome.get("risk", {}),
        "scorecard": outcome.get("scorecard", {}),
        "attribution": {
            "cash": attribution.get("cash", {}),
            "active_excess": attribution.get("active_excess", {}),
            "drivers": attribution.get("drivers", {}),
        },
        "sentiment": outcome.get("sentiment", {}),
        "fragility": {
            "ok": not fragility_reasons,
            "reasons": fragility_reasons,
        },
    }


def _float_values(regimes: list[dict[str, Any]], path: tuple[str, ...]) -> list[float]:
    values: list[float] = []
    for regime in regimes:
        value: Any = regime
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
    return values


def _regime_summary(regimes: list[dict[str, Any]], *, max_drawdown: float) -> dict[str, Any]:
    fragile = [regime for regime in regimes if not regime["fragility"]["ok"]]
    reversal = next((regime for regime in regimes if regime["name"] == "primary_reversal"), None)
    cash_rally = next((regime for regime in regimes if regime["name"] == "cash_drag_rally"), None)
    excess_cash = _float_values(regimes, ("returns", "excess_vs_cash"))
    excess_equal = _float_values(regimes, ("returns", "excess_vs_equal_weight"))
    drawdowns = _float_values(regimes, ("risk", "realized_max_drawdown"))
    primary_reversal_loss = None
    if isinstance(reversal, dict):
        primary_reversal_loss = reversal.get("primary_pick", {}).get("realized_return")
    cash_drag_rally_excess_equal = None
    if isinstance(cash_rally, dict):
        cash_drag_rally_excess_equal = cash_rally.get("returns", {}).get("excess_vs_equal_weight")
    return {
        "ok": not fragile,
        "regime_count": len(regimes),
        "scorecard_pass_count": len(
            [regime for regime in regimes if regime.get("scorecard", {}).get("ok")]
        ),
        "fragile_count": len(fragile),
        "fragile_regimes": [regime["name"] for regime in fragile],
        "worst_excess_vs_cash": min(excess_cash) if excess_cash else None,
        "worst_excess_vs_equal_weight": min(excess_equal) if excess_equal else None,
        "worst_drawdown": max(drawdowns) if drawdowns else None,
        "max_drawdown": max_drawdown,
        "primary_reversal_loss": primary_reversal_loss,
        "cash_drag_rally_excess_vs_equal_weight": cash_drag_rally_excess_equal,
    }


def stable_regime_replay_digest(report: dict[str, Any]) -> str:
    """Return a stable digest for a replay report, excluding volatile timestamp."""

    scoped = dict(report)
    scoped.pop("generated_at", None)
    scoped.pop("report_digest", None)
    encoded = json.dumps(scoped, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evaluate_packet_regimes(
    packet: dict[str, Any],
    *,
    output_dir: Path | None = None,
    rows: int = 5,
    start_date: str = DEFAULT_REGIME_START_DATE,
    cash_return: float = 0.0,
    max_drawdown: float = DEFAULT_MAX_REGIME_DRAWDOWN,
) -> dict[str, Any]:
    """Replay a packet across deterministic synthetic market regimes."""

    root = (output_dir or default_regime_dir()).expanduser().resolve()
    run_id = _safe_name(str(packet.get("run_id") or "run"))
    content_digest = str(packet.get("content_digest") or packet_digest(packet))
    allocations = _allocations(packet)
    tickers = _input_tickers(packet, allocations)
    primary = _primary_ticker(packet, tickers=tickers, allocations=allocations)
    regimes: list[dict[str, Any]] = []

    for definition in REGIME_DEFINITIONS:
        price_dir = write_regime_price_csvs(
            packet,
            regime_name=definition.name,
            output_dir=root / run_id / "prices" / definition.name,
            rows=rows,
            start_date=start_date,
        )
        outcome = evaluate_outcome(
            packet,
            price_dir=price_dir,
            start_date=start_date,
            horizon_rows=rows - 1,
            cash_return=cash_return,
        )
        regimes.append(
            _regime_result(
                definition=definition,
                price_dir=price_dir,
                outcome=outcome,
                max_drawdown=max_drawdown,
            )
        )

    report = {
        "schema_version": REGIME_REPLAY_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "run_id": packet.get("run_id"),
        "content_digest": content_digest,
        "primary_ticker": primary,
        "parameters": {
            "rows": rows,
            "start_date": start_date,
            "cash_return": cash_return,
            "max_drawdown": max_drawdown,
            "regime_names": [definition.name for definition in REGIME_DEFINITIONS],
        },
        "summary": _regime_summary(regimes, max_drawdown=max_drawdown),
        "regimes": regimes,
    }
    report["report_digest"] = stable_regime_replay_digest(report)
    return report


def regime_replay_path(output_dir: Path, report: dict[str, Any]) -> Path:
    """Return the canonical path for a regime replay artifact."""

    run_id = _safe_name(str(report.get("run_id") or "run"))
    digest = _safe_name(str(report.get("report_digest") or stable_regime_replay_digest(report)))[:12]
    return output_dir.expanduser().resolve() / f"{run_id}_regime_replay_{digest}.json"


def write_regime_replay(report: dict[str, Any], output_dir: Path | None = None) -> Path:
    """Write a regime replay artifact and update ``latest.json`` atomically."""

    root = (output_dir or default_regime_dir()).expanduser().resolve()
    report = dict(report)
    report["report_digest"] = stable_regime_replay_digest(report)
    path = regime_replay_path(root, report)
    _atomic_write_json(path, report)
    _atomic_write_json(root / "latest.json", report)
    return path
