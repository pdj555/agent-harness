"""Regime-resilient allocation repair for Monte Carlo engine output."""

from __future__ import annotations

import copy
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_harness.adapters import EngineRun
from agent_harness.regimes import DEFAULT_MAX_REGIME_DRAWDOWN, evaluate_packet_regimes
from agent_harness.stress import stress_packet


ALLOCATION_REPAIR_SCHEMA_VERSION = "agent-harness.allocation-repair.v1"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _action_plan(payload: dict[str, Any]) -> dict[str, Any]:
    action_plan = payload.get("action_plan", {})
    return action_plan if isinstance(action_plan, dict) else {}


def _primary_ticker(payload: dict[str, Any], tickers: list[str]) -> str | None:
    primary = _action_plan(payload).get("primary_pick", {})
    if isinstance(primary, dict) and primary.get("ticker"):
        return str(primary["ticker"])
    return tickers[0] if tickers else None


def _payload_allocations(payload: dict[str, Any]) -> dict[str, float]:
    raw = payload.get("allocations", {})
    allocations: dict[str, float] = {}
    if isinstance(raw, dict):
        for ticker, row in raw.items():
            if isinstance(row, dict) and row.get("weight") is not None:
                allocations[str(ticker)] = _number(row.get("weight"))
    primary = _action_plan(payload).get("primary_pick", {})
    if not allocations and isinstance(primary, dict) and primary.get("ticker"):
        allocations[str(primary["ticker"])] = _number(primary.get("weight"))
    return {ticker: weight for ticker, weight in allocations.items() if weight > 0.0}


def _cash_weight(payload: dict[str, Any], allocations: dict[str, float]) -> float:
    action_plan = _action_plan(payload)
    if action_plan.get("cash_weight") is not None:
        return _number(action_plan.get("cash_weight"))
    return max(0.0, 1.0 - sum(allocations.values()))


def _candidate_key(allocations: dict[str, float], cash_weight: float) -> tuple[tuple[str, float], float]:
    return (
        tuple(sorted((ticker, round(weight, 6)) for ticker, weight in allocations.items())),
        round(cash_weight, 6),
    )


def _candidate_allocations(
    *,
    tickers: list[str],
    primary_ticker: str,
    original_allocations: dict[str, float],
    original_cash: float,
    max_position_weight: float,
    enforce_max_position: bool,
) -> list[tuple[dict[str, float], float]]:
    candidates: list[tuple[dict[str, float], float]] = []
    seen: set[tuple[tuple[str, float], float]] = set()

    def add(allocations: dict[str, float], cash_weight: float, *, allow_cap_violation: bool = False) -> None:
        cleaned = {
            ticker: round(max(0.0, float(weight)), 6)
            for ticker, weight in allocations.items()
            if weight > 0.0
        }
        total = sum(cleaned.values()) + max(0.0, float(cash_weight))
        if abs(total - 1.0) > 1e-6:
            return
        if (
            enforce_max_position
            and not allow_cap_violation
            and any(weight > max_position_weight + 1e-9 for weight in cleaned.values())
        ):
            return
        key = _candidate_key(cleaned, cash_weight)
        if key in seen:
            return
        seen.add(key)
        candidates.append((cleaned, round(cash_weight, 6)))

    scoped_tickers = [ticker for ticker in tickers if ticker]
    if primary_ticker not in scoped_tickers:
        scoped_tickers.insert(0, primary_ticker)
    non_primary = [ticker for ticker in scoped_tickers if ticker != primary_ticker]

    add(original_allocations, original_cash, allow_cap_violation=True)
    risky_sum = sum(original_allocations.values())
    if risky_sum > 0:
        add(
            {
                ticker: weight / risky_sum
                for ticker, weight in original_allocations.items()
            },
            0.0,
        )

    if not non_primary:
        return candidates

    equal_weight = 1.0 / len(scoped_tickers)
    add({ticker: equal_weight for ticker in scoped_tickers}, 0.0)

    cash_options = sorted({0.0, 0.1, 0.2, round(original_cash, 6)})
    max_primary = max(0.05, min(max_position_weight, 0.80))
    primary_steps = [round(step / 100, 2) for step in range(5, int(max_primary * 100) + 1, 5)]
    for cash_weight in cash_options:
        risky_budget = 1.0 - cash_weight
        if risky_budget <= 0:
            continue
        for primary_weight in primary_steps:
            if primary_weight > risky_budget:
                continue
            remainder = risky_budget - primary_weight
            other_weight = remainder / len(non_primary)
            add(
                {
                    primary_ticker: primary_weight,
                    **{ticker: other_weight for ticker in non_primary},
                },
                cash_weight,
            )

    return candidates


def _candidate_payload(
    payload: dict[str, Any],
    *,
    primary_ticker: str,
    allocations: dict[str, float],
    cash_weight: float,
) -> dict[str, Any]:
    candidate = copy.deepcopy(payload)
    action_plan = _action_plan(candidate)
    action_plan = copy.deepcopy(action_plan)
    raw_headline = action_plan.get("headline")
    if raw_headline and not action_plan.get("raw_headline"):
        action_plan["raw_headline"] = raw_headline
    primary = action_plan.get("primary_pick", {})
    primary = copy.deepcopy(primary) if isinstance(primary, dict) else {}
    primary["ticker"] = primary.get("ticker") or primary_ticker
    primary["weight"] = allocations.get(primary_ticker, 0.0)
    action_plan["primary_pick"] = primary
    action_plan["cash_weight"] = cash_weight
    action_plan["headline"] = _allocation_headline(
        allocations=allocations,
        cash_weight=cash_weight,
    )
    candidate["action_plan"] = action_plan

    existing_rows = candidate.get("allocations", {})
    existing_rows = existing_rows if isinstance(existing_rows, dict) else {}
    repaired_rows: dict[str, dict[str, Any]] = {}
    for ticker, weight in sorted(allocations.items()):
        row = copy.deepcopy(existing_rows.get(ticker, {}))
        row = row if isinstance(row, dict) else {}
        row["weight"] = weight
        if ticker not in existing_rows:
            row["source"] = "agent_harness_regime_repair"
        repaired_rows[ticker] = row
    candidate["allocations"] = repaired_rows
    return candidate


def _allocation_headline(*, allocations: dict[str, float], cash_weight: float) -> str:
    weights = ", ".join(
        f"{ticker} {weight:.1%}" for ticker, weight in sorted(allocations.items())
    )
    return f"Regime-repaired allocation: {weights}; cash {cash_weight:.1%}"


def _position_cap_violations(
    allocations: dict[str, float],
    *,
    max_position_weight: float,
) -> dict[str, float]:
    return {
        ticker: weight
        for ticker, weight in sorted(allocations.items())
        if weight > max_position_weight + 1e-9
    }


def _max_position_policy(
    *,
    allocations: dict[str, float],
    primary_ticker: str,
    tickers: list[str],
    max_position_weight: float,
    enforced: bool,
) -> dict[str, Any]:
    non_primary = [ticker for ticker in tickers if ticker and ticker != primary_ticker]
    violations = _position_cap_violations(
        allocations,
        max_position_weight=max_position_weight,
    )
    return {
        "max_position_weight": max_position_weight,
        "enforced": enforced,
        "reason": None if enforced else "insufficient_non_primary_alternatives",
        "non_primary_alternative_count": len(non_primary),
        "minimum_non_primary_alternatives": 2,
        "max_selected_weight": max(allocations.values()) if allocations else 0.0,
        "violations": violations,
        "ok": not violations,
    }


def _repair_packet(
    *,
    payload: dict[str, Any],
    tickers: list[str],
    run_id: str,
    backtest_run: EngineRun | None = None,
) -> dict[str, Any]:
    engine_runs: dict[str, Any] = {
        "monte_carlo": {
            "ok": True,
            "payload": payload,
        }
    }
    if backtest_run is not None:
        engine_runs["monte_carlo_backtest"] = {
            "ok": bool(backtest_run.ok),
            "payload": backtest_run.payload,
        }
    return {
        "run_id": run_id,
        "inputs": {"tickers": tickers},
        "engine_runs": engine_runs,
    }


def _summary_score(
    report: dict[str, Any],
    stress: dict[str, Any] | None = None,
) -> tuple[float, ...]:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    regimes = report.get("regimes", []) if isinstance(report.get("regimes"), list) else []
    trend = next(
        (row for row in regimes if isinstance(row, dict) and row.get("name") == "primary_trend"),
        {},
    )
    trend_return = (
        trend.get("returns", {}).get("allocation")
        if isinstance(trend.get("returns"), dict)
        else None
    )
    score: tuple[float, ...] = (
        _number(summary.get("fragile_count"), 999.0),
    )
    if stress is not None:
        score = (
            *score,
            0.0 if bool(stress.get("ok")) else 1.0,
            -_number(stress.get("worst_margin"), -999.0),
        )
    return (
        *score,
        -_number(summary.get("worst_excess_vs_cash"), -999.0),
        -_number(summary.get("worst_excess_vs_equal_weight"), -999.0),
        _number(summary.get("worst_drawdown"), 999.0),
        -_number(trend_return, -999.0),
    )


def repair_monte_carlo_allocation(
    run: EngineRun | None,
    *,
    tickers: list[str],
    backtest_run: EngineRun | None = None,
    max_position_weight: float = 0.60,
    max_drawdown: float = DEFAULT_MAX_REGIME_DRAWDOWN,
) -> EngineRun | None:
    """Return a Monte Carlo run with a regime-resilient allocation when useful."""

    if run is None or not run.ok or not isinstance(run.payload, dict):
        return run
    payload = copy.deepcopy(run.payload)
    primary_ticker = _primary_ticker(payload, tickers)
    if not primary_ticker:
        return run
    original_allocations = _payload_allocations(payload)
    if not original_allocations:
        return run
    scoped_tickers = list(dict.fromkeys([*tickers, *original_allocations.keys(), primary_ticker]))
    non_primary = [ticker for ticker in scoped_tickers if ticker != primary_ticker]
    enforce_max_position = len(non_primary) >= 2
    original_cash = _cash_weight(payload, original_allocations)

    candidates = _candidate_allocations(
        tickers=scoped_tickers,
        primary_ticker=primary_ticker,
        original_allocations=original_allocations,
        original_cash=original_cash,
        max_position_weight=max_position_weight,
        enforce_max_position=enforce_max_position,
    )
    if not candidates:
        return run

    evaluated: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agent-harness-allocation-repair-") as tmp_dir:
        root = Path(tmp_dir)
        for index, (allocations, cash_weight) in enumerate(candidates):
            candidate_payload = _candidate_payload(
                payload,
                primary_ticker=primary_ticker,
                allocations=allocations,
                cash_weight=cash_weight,
            )
            report = evaluate_packet_regimes(
                _repair_packet(
                    payload=candidate_payload,
                    tickers=scoped_tickers,
                    run_id=f"allocation_repair_candidate_{index}",
                    backtest_run=backtest_run,
                ),
                output_dir=root / f"candidate_{index}",
                max_drawdown=max_drawdown,
            )
            repair_packet = _repair_packet(
                payload=candidate_payload,
                tickers=scoped_tickers,
                run_id=f"allocation_repair_candidate_{index}",
                backtest_run=backtest_run,
            )
            stress_report = stress_packet(repair_packet) if backtest_run is not None else None
            evaluated.append(
                {
                    "allocations": allocations,
                    "cash_weight": cash_weight,
                    "report": report,
                    "stress": stress_report,
                    "score": _summary_score(report, stress_report),
                }
            )

    original_key = _candidate_key(original_allocations, original_cash)
    original = next(
        (
            row for row in evaluated
            if _candidate_key(row["allocations"], row["cash_weight"]) == original_key
        ),
        None,
    )
    if original is None:
        return run
    best = min(evaluated, key=lambda row: row["score"])
    if best["score"] >= original["score"]:
        cap_policy = _max_position_policy(
            allocations=original_allocations,
            primary_ticker=primary_ticker,
            tickers=scoped_tickers,
            max_position_weight=max_position_weight,
            enforced=enforce_max_position,
        )
        payload["allocation_repair"] = {
            "schema_version": ALLOCATION_REPAIR_SCHEMA_VERSION,
            "applied": False,
            "method": "deterministic_regime_grid_search",
            "candidate_count": len(evaluated),
            "reason": "no candidate improved deterministic regime replay score",
            "max_position_policy": cap_policy,
            "original_summary": original["report"]["summary"],
            "best_summary": best["report"]["summary"],
        }
        if original["stress"] is not None:
            payload["allocation_repair"]["original_stress"] = original["stress"]
            payload["allocation_repair"]["best_stress"] = best["stress"]
        return replace(run, payload=payload)

    repaired_payload = _candidate_payload(
        payload,
        primary_ticker=primary_ticker,
        allocations=best["allocations"],
        cash_weight=best["cash_weight"],
    )
    repaired_payload["allocation_repair"] = {
        "schema_version": ALLOCATION_REPAIR_SCHEMA_VERSION,
        "applied": True,
        "method": "deterministic_regime_grid_search",
        "candidate_count": len(evaluated),
        "primary_ticker": primary_ticker,
        "max_position_policy": _max_position_policy(
            allocations=best["allocations"],
            primary_ticker=primary_ticker,
            tickers=scoped_tickers,
            max_position_weight=max_position_weight,
            enforced=enforce_max_position,
        ),
        "original": {
            "allocations": original_allocations,
            "cash_weight": original_cash,
            "summary": original["report"]["summary"],
            "stress": original["stress"],
        },
        "selected": {
            "allocations": best["allocations"],
            "cash_weight": best["cash_weight"],
            "summary": best["report"]["summary"],
            "stress": best["stress"],
        },
    }
    original_fragile = original["report"]["summary"].get("fragile_count")
    selected_fragile = best["report"]["summary"].get("fragile_count")
    repair_line = (
        f"Regime repair: fragile={original_fragile}->{selected_fragile}; "
        f"primary={best['allocations'].get(primary_ticker, 0.0):.1%}; "
        f"cash={best['cash_weight']:.1%}; "
        f"max_position_policy={'enforced' if enforce_max_position else 'deferred'}"
    )
    if best["stress"] is not None:
        repair_line += (
            f"; stress_margin={best['stress'].get('worst_margin')}; "
            f"stress_ok={best['stress'].get('ok')}"
        )
    summary = (
        f"{_allocation_headline(allocations=best['allocations'], cash_weight=best['cash_weight'])}\n"
        f"{repair_line}\n"
        f"Raw Monte Carlo output:\n{run.summary}"
    )
    return replace(
        run,
        payload=repaired_payload,
        summary=summary,
        diagnostics=(
            *run.diagnostics,
            "agent-harness allocation repair applied deterministic regime grid search",
        ),
    )
