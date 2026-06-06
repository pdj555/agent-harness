"""Realized outcome evaluation for saved capital decision packets."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_harness.packets import packet_digest


OUTCOME_SCHEMA_VERSION = "agent-harness.outcome.v1"


@dataclass(frozen=True)
class PricePoint:
    date: str
    close: float


def default_outcome_dir(cwd: Path | None = None) -> Path:
    """Return the default local realized-outcome artifact directory."""

    return (cwd or Path.cwd()) / ".agent-harness" / "outcomes"


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


def stable_outcome_digest(outcome: dict[str, Any]) -> str:
    """Return a stable digest for an outcome, excluding volatile timestamps."""

    scoped = dict(outcome)
    scoped.pop("outcome_digest", None)
    scoped.pop("evaluated_at", None)
    encoded = json.dumps(scoped, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _price_csv_path(price_dir: Path, ticker: str) -> Path:
    return price_dir.expanduser() / f"{ticker}.csv"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_price_series(price_dir: Path, ticker: str) -> list[PricePoint]:
    """Load ``Date,Close`` CSV fixture data for a ticker."""

    path = _price_csv_path(price_dir, ticker)
    if not path.exists():
        raise FileNotFoundError(f"missing price CSV for {ticker}: {path}")
    rows: list[PricePoint] = []
    seen_dates: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            date = str(row.get("Date") or row.get("date") or "").strip()
            close_raw = row.get("Close") or row.get("close")
            if not date or close_raw is None:
                continue
            if date in seen_dates:
                raise ValueError(f"duplicate price date for {ticker}: {date}")
            seen_dates.add(date)
            rows.append(PricePoint(date=date, close=float(close_raw)))
    if len(rows) < 2:
        raise ValueError(f"price CSV for {ticker} must contain at least two rows")
    return sorted(rows, key=lambda item: item.date)


def _action_plan(packet: dict[str, Any]) -> dict[str, Any]:
    run = packet.get("engine_runs", {}).get("monte_carlo")
    if not isinstance(run, dict):
        return {}
    payload = run.get("payload", {})
    if not isinstance(payload, dict):
        return {}
    action_plan = payload.get("action_plan", {})
    return action_plan if isinstance(action_plan, dict) else {}


def _monte_carlo_payload(packet: dict[str, Any]) -> dict[str, Any]:
    run = packet.get("engine_runs", {}).get("monte_carlo")
    if not isinstance(run, dict):
        return {}
    payload = run.get("payload", {})
    return payload if isinstance(payload, dict) else {}


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


def _cash_weight(packet: dict[str, Any], allocations: dict[str, float]) -> float:
    action_plan = _action_plan(packet)
    if action_plan.get("cash_weight") is not None:
        return float(action_plan["cash_weight"])
    return max(0.0, 1.0 - sum(allocations.values()))


def _validate_portfolio_weights(
    allocations: dict[str, float],
    cash_weight: float,
    *,
    tolerance: float = 1e-9,
) -> None:
    invalid_allocations = {
        ticker: weight
        for ticker, weight in allocations.items()
        if not math.isfinite(weight) or weight < -tolerance
    }
    if invalid_allocations:
        rendered = ", ".join(
            f"{ticker}={weight}" for ticker, weight in sorted(invalid_allocations.items())
        )
        raise ValueError(f"allocation weights must be finite and non-negative: {rendered}")
    if not math.isfinite(cash_weight) or cash_weight < -tolerance:
        raise ValueError(f"cash_weight must be finite and non-negative: {cash_weight}")
    total_weight = sum(allocations.values()) + cash_weight
    if abs(total_weight - 1.0) > tolerance:
        raise ValueError(
            "allocation weights plus cash_weight must equal 1.0 "
            f"for an unlevered outcome: {total_weight}"
        )


def _price_sources(
    *,
    price_dir: Path,
    series_by_ticker: dict[str, list[PricePoint]],
) -> dict[str, Any]:
    prices: dict[str, Any] = {}
    for ticker, series in sorted(series_by_ticker.items()):
        path = _price_csv_path(price_dir, ticker).expanduser().resolve()
        prices[ticker] = {
            "path": str(path),
            "sha256": _file_sha256(path),
            "rows": len(series),
            "first_date": series[0].date,
            "last_date": series[-1].date,
        }
    digest_input = {
        ticker: {
            "sha256": row["sha256"],
            "rows": row["rows"],
            "first_date": row["first_date"],
            "last_date": row["last_date"],
        }
        for ticker, row in prices.items()
    }
    encoded = json.dumps(digest_input, sort_keys=True, separators=(",", ":"))
    return {
        "price_dir": str(price_dir.expanduser().resolve()),
        "price_source_digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "prices": prices,
    }


def _default_horizon_rows(packet: dict[str, Any]) -> int | None:
    backtest = packet.get("inputs", {}).get("backtest", {})
    if isinstance(backtest, dict) and backtest.get("hold") is not None:
        try:
            return max(1, int(backtest["hold"]))
        except (TypeError, ValueError):
            return None
    return None


def _series_map(series: list[PricePoint]) -> dict[str, float]:
    return {point.date: point.close for point in series}


def _common_dates(series_by_ticker: dict[str, list[PricePoint]]) -> list[str]:
    common: set[str] | None = None
    for series in series_by_ticker.values():
        dates = {point.date for point in series}
        common = dates if common is None else common & dates
    return sorted(common or set())


def _resolve_window(
    dates: list[str],
    *,
    start_date: str | None,
    end_date: str | None,
    horizon_rows: int | None,
) -> tuple[str, str, int, int]:
    if len(dates) < 2:
        raise ValueError("at least two common price dates are required")
    if start_date is None:
        start_index = 0
    else:
        if start_date not in dates:
            raise ValueError(f"start date not present in common price dates: {start_date}")
        start_index = dates.index(start_date)

    if end_date is not None:
        if end_date not in dates:
            raise ValueError(f"end date not present in common price dates: {end_date}")
        end_index = dates.index(end_date)
    else:
        scoped_horizon = horizon_rows if horizon_rows is not None else len(dates) - start_index - 1
        end_index = min(len(dates) - 1, start_index + max(1, int(scoped_horizon)))

    if end_index <= start_index:
        raise ValueError("end date must be after start date")
    return dates[start_index], dates[end_index], start_index, end_index


def _max_drawdown(values: list[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1.0)
    return abs(worst)


def _attribution(
    *,
    tickers: list[str],
    ticker_returns: dict[str, float],
    allocations: dict[str, float],
    cash_weight: float,
    cash_return: float,
) -> dict[str, Any]:
    equal_weight = 1.0 / len(tickers)
    positions: list[dict[str, Any]] = []
    for ticker in tickers:
        allocation_weight = float(allocations.get(ticker, 0.0))
        realized_return = ticker_returns[ticker]
        allocation_contribution = allocation_weight * realized_return
        equal_weight_contribution = equal_weight * realized_return
        active_contribution = allocation_contribution - equal_weight_contribution
        positions.append(
            {
                "ticker": ticker,
                "allocation_weight": allocation_weight,
                "equal_weight": equal_weight,
                "active_weight": allocation_weight - equal_weight,
                "realized_return": realized_return,
                "allocation_contribution": allocation_contribution,
                "equal_weight_contribution": equal_weight_contribution,
                "active_contribution": active_contribution,
            }
        )

    cash_contribution = cash_weight * cash_return
    equal_weight_return = sum(ticker_returns.values()) / len(ticker_returns)
    active_position_contribution = sum(row["active_contribution"] for row in positions)
    active_excess = active_position_contribution + cash_contribution
    top_allocation = max(
        positions,
        key=lambda row: row["allocation_contribution"],
    )
    top_active = max(
        positions,
        key=lambda row: row["active_contribution"],
    )
    top_active_contributor = (
        {
            "ticker": top_active["ticker"],
            "contribution": top_active["active_contribution"],
        }
        if top_active["active_contribution"] > 0
        else None
    )
    bottom_active = min(
        positions,
        key=lambda row: row["active_contribution"],
    )
    largest_active_drag = (
        {
            "ticker": bottom_active["ticker"],
            "contribution": bottom_active["active_contribution"],
        }
        if bottom_active["active_contribution"] < 0
        else None
    )
    return {
        "positions": positions,
        "cash": {
            "weight": cash_weight,
            "return": cash_return,
            "contribution": cash_contribution,
            "drag_vs_equal_weight": cash_weight * (cash_return - equal_weight_return),
        },
        "active_excess": {
            "vs_equal_weight": active_excess,
            "from_positions": active_position_contribution,
            "from_cash": cash_contribution,
        },
        "drivers": {
            "top_allocation_contributor": {
                "ticker": top_allocation["ticker"],
                "contribution": top_allocation["allocation_contribution"],
            },
            "top_active_contributor": {
                "ticker": top_active["ticker"],
                "contribution": top_active["active_contribution"],
            }
            if top_active_contributor is not None
            else None,
            "weakest_active_contributor": {
                "ticker": bottom_active["ticker"],
                "contribution": bottom_active["active_contribution"],
            },
            "largest_active_drag": largest_active_drag,
        },
    }


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sentiment_outcome(
    packet: dict[str, Any],
    *,
    primary_ticker: str | None,
    primary_return: float | None,
) -> dict[str, Any]:
    run = packet.get("engine_runs", {}).get("stock_sentiment")
    if not isinstance(run, dict):
        return {
            "present": False,
            "ok": False,
            "reason": "stock sentiment was not run",
        }
    payload = run.get("payload", {}) if isinstance(run.get("payload"), dict) else {}
    ticker = str(payload.get("ticker") or "") or None
    signal = str(payload.get("signal") or "hold").lower()
    score = _float_or_none(payload.get("score"))
    confidence = _float_or_none(payload.get("confidence"))
    direction = {"buy": 1, "sell": -1, "hold": 0}.get(signal, 0)
    ticker_matches_primary = bool(ticker and primary_ticker and ticker == primary_ticker)
    realized_direction = (
        1
        if primary_return is not None and primary_return > 0
        else -1
        if primary_return is not None and primary_return < 0
        else 0
        if primary_return is not None
        else None
    )
    directional_hit = None
    signed_realized_return = None
    if bool(run.get("ok")) and ticker_matches_primary and primary_return is not None and direction:
        signed_realized_return = direction * primary_return
        directional_hit = signed_realized_return >= 0

    score_return_alignment = (
        score * primary_return
        if score is not None and primary_return is not None and ticker_matches_primary
        else None
    )
    confidence_weighted_alignment = (
        score_return_alignment * confidence
        if score_return_alignment is not None and confidence is not None
        else None
    )
    return {
        "present": True,
        "ok": bool(run.get("ok")),
        "ticker": ticker,
        "ticker_matches_primary": ticker_matches_primary,
        "score": score,
        "label": payload.get("label"),
        "confidence": confidence,
        "signal": signal,
        "signal_direction": direction,
        "articles_analyzed": payload.get("articles_analyzed"),
        "source": payload.get("source"),
        "source_label": payload.get("source_label"),
        "classification_degraded": bool(payload.get("classification_degraded")),
        "classification_warnings": payload.get("classification_warnings", []),
        "primary_ticker": primary_ticker,
        "primary_realized_return": primary_return,
        "realized_direction": realized_direction,
        "directional_hit": directional_hit,
        "signed_realized_return": signed_realized_return,
        "score_return_alignment": score_return_alignment,
        "confidence_weighted_alignment": confidence_weighted_alignment,
    }


def evaluate_outcome(
    packet: dict[str, Any],
    *,
    price_dir: Path,
    start_date: str | None = None,
    end_date: str | None = None,
    horizon_rows: int | None = None,
    cash_return: float = 0.0,
) -> dict[str, Any]:
    """Evaluate realized return for a saved packet over a price window."""

    allocations = _allocations(packet)
    if not allocations:
        raise ValueError("packet has no Monte Carlo allocation or primary pick")
    tickers = _input_tickers(packet, allocations)
    if not tickers:
        raise ValueError("packet has no tickers to evaluate")

    series_by_ticker = {
        ticker: load_price_series(price_dir, ticker)
        for ticker in tickers
    }
    common_dates = _common_dates(series_by_ticker)
    resolved_horizon = horizon_rows if horizon_rows is not None else _default_horizon_rows(packet)
    start, end, start_index, end_index = _resolve_window(
        common_dates,
        start_date=start_date,
        end_date=end_date,
        horizon_rows=resolved_horizon,
    )
    scoped_dates = common_dates[start_index : end_index + 1]
    prices = {ticker: _series_map(series) for ticker, series in series_by_ticker.items()}
    ticker_returns = {
        ticker: prices[ticker][end] / prices[ticker][start] - 1.0
        for ticker in tickers
    }
    cash_weight = _cash_weight(packet, allocations)
    _validate_portfolio_weights(allocations, cash_weight)
    allocation_return = sum(
        float(weight) * ticker_returns[ticker]
        for ticker, weight in allocations.items()
        if ticker in ticker_returns
    ) + cash_weight * cash_return
    equal_weight_return = sum(ticker_returns.values()) / len(ticker_returns)
    attribution = _attribution(
        tickers=tickers,
        ticker_returns=ticker_returns,
        allocations=allocations,
        cash_weight=cash_weight,
        cash_return=cash_return,
    )
    monte_payload = _monte_carlo_payload(packet)
    primary = _action_plan(packet).get("primary_pick", {})
    primary_ticker = str(primary.get("ticker")) if isinstance(primary, dict) and primary.get("ticker") else None
    primary_return = ticker_returns.get(primary_ticker) if primary_ticker else None
    primary_weight = (
        allocations.get(primary_ticker)
        if primary_ticker is not None and primary_ticker in allocations
        else float(primary.get("weight"))
        if isinstance(primary, dict) and primary.get("weight") is not None
        else None
    )
    expected_return = (
        float(primary["expected_return"])
        if isinstance(primary, dict) and primary.get("expected_return") is not None
        else None
    )
    allocation_repair = (
        monte_payload.get("allocation_repair")
        if isinstance(monte_payload.get("allocation_repair"), dict)
        else {}
    )

    curve: list[dict[str, Any]] = []
    for date in scoped_dates:
        risky_value = sum(
            float(weight) * (prices[ticker][date] / prices[ticker][start])
            for ticker, weight in allocations.items()
            if ticker in prices
        )
        cash_value = cash_weight * (1.0 + cash_return)
        curve.append({"date": date, "value": risky_value + cash_value})

    realized_max_drawdown = _max_drawdown([row["value"] for row in curve])
    hit = primary_return is not None and primary_return >= 0.0
    beat_cash = allocation_return >= cash_return
    beat_equal_weight = allocation_return >= equal_weight_return
    primary_hit_required = not (
        bool(allocation_repair.get("applied"))
        and primary_weight is not None
        and float(primary_weight) <= 0.10
    )
    forecast_error = (
        primary_return - expected_return
        if primary_return is not None and expected_return is not None
        else None
    )

    outcome = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "evaluated_at": _utc_now(),
        "run_id": packet.get("run_id"),
        "content_digest": packet.get("content_digest") or packet_digest(packet),
        "price_dir": str(price_dir.expanduser().resolve()),
        "sources": _price_sources(
            price_dir=price_dir,
            series_by_ticker=series_by_ticker,
        ),
        "window": {
            "start_date": start,
            "end_date": end,
            "rows": len(scoped_dates),
            "horizon_rows": end_index - start_index,
        },
        "allocation": {
            "weights": allocations,
            "cash_weight": cash_weight,
            "cash_return": cash_return,
        },
        "primary_pick": {
            "ticker": primary_ticker,
            "weight": primary_weight,
            "expected_return": expected_return,
            "realized_return": primary_return,
            "forecast_error": forecast_error,
            "hit": hit,
        },
        "returns": {
            "allocation": allocation_return,
            "equal_weight": equal_weight_return,
            "cash": cash_return,
            "excess_vs_equal_weight": allocation_return - equal_weight_return,
            "excess_vs_cash": allocation_return - cash_return,
            "by_ticker": ticker_returns,
        },
        "risk": {
            "realized_max_drawdown": realized_max_drawdown,
        },
        "attribution": attribution,
        "sentiment": _sentiment_outcome(
            packet,
            primary_ticker=primary_ticker,
            primary_return=primary_return,
        ),
        "scorecard": {
            "beat_cash": beat_cash,
            "beat_equal_weight": beat_equal_weight,
            "primary_hit": hit,
            "primary_hit_required": primary_hit_required,
            "primary_hit_waived_reason": (
                "allocation_repair_de_minimis_primary_weight"
                if not primary_hit_required
                else None
            ),
            "ok": beat_cash and beat_equal_weight and (hit or not primary_hit_required),
        },
        "curve": curve,
    }
    outcome["outcome_digest"] = stable_outcome_digest(outcome)
    return outcome


def outcome_path(output_dir: Path, outcome: dict[str, Any]) -> Path:
    """Return the canonical path for an outcome artifact."""

    run_id = _safe_name(str(outcome.get("run_id") or "run"))
    window = outcome.get("window", {})
    start = _safe_name(str(window.get("start_date") or "start")) if isinstance(window, dict) else "start"
    end = _safe_name(str(window.get("end_date") or "end")) if isinstance(window, dict) else "end"
    digest = _safe_name(str(outcome.get("outcome_digest") or stable_outcome_digest(outcome)))[:12]
    return output_dir.expanduser().resolve() / f"{run_id}_{start}_{end}_{digest}.json"


def write_outcome(outcome: dict[str, Any], output_dir: Path | None = None) -> Path:
    """Write an outcome artifact and update ``latest.json`` atomically."""

    root = (output_dir or default_outcome_dir()).expanduser().resolve()
    path = outcome_path(root, outcome)
    _atomic_write_json(path, outcome)
    _atomic_write_json(root / "latest.json", outcome)
    return path


def _candidate_windows(
    packet: dict[str, Any],
    *,
    price_dir: Path,
    start_date: str | None,
    end_date: str | None,
    horizon_rows: int | None,
    rolling: bool,
    stride_rows: int,
    max_windows: int | None,
) -> list[dict[str, Any]]:
    if not rolling:
        return [
            {
                "start_date": start_date,
                "end_date": end_date,
                "horizon_rows": horizon_rows,
            }
        ]
    if end_date is not None:
        raise ValueError("rolling outcome backfill cannot use --end-date")
    if stride_rows < 1:
        raise ValueError("rolling outcome backfill stride must be at least 1")
    if max_windows is not None and max_windows < 1:
        raise ValueError("rolling outcome backfill max_windows must be at least 1")

    allocations = _allocations(packet)
    tickers = _input_tickers(packet, allocations)
    if not tickers:
        raise ValueError("packet has no tickers to evaluate")
    series_by_ticker = {
        ticker: load_price_series(price_dir, ticker)
        for ticker in tickers
    }
    dates = _common_dates(series_by_ticker)
    resolved_horizon = horizon_rows if horizon_rows is not None else _default_horizon_rows(packet)
    scoped_horizon = max(1, int(resolved_horizon or 1))
    if len(dates) <= scoped_horizon:
        raise ValueError("not enough common price dates for rolling outcome backfill")
    start_index = dates.index(start_date) if start_date is not None else 0
    windows: list[dict[str, Any]] = []
    for index in range(start_index, len(dates) - scoped_horizon, stride_rows):
        windows.append(
            {
                "start_date": dates[index],
                "end_date": dates[index + scoped_horizon],
                "horizon_rows": scoped_horizon,
            }
        )
        if max_windows is not None and len(windows) >= max_windows:
            break
    return windows


def backfill_ledger_outcomes(
    *,
    ledger_dir: Path,
    price_dir: Path,
    output_dir: Path | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    horizon_rows: int | None = None,
    cash_return: float = 0.0,
    run_ids: set[str] | None = None,
    limit: int | None = None,
    rolling: bool = False,
    stride_rows: int = 1,
    max_windows: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Evaluate and ingest realized outcomes for ledger packet copies."""

    from agent_harness.ledger import ingest_outcome, read_ledger_entries, read_outcome_entries

    entries = read_ledger_entries(ledger_dir)
    if run_ids:
        entries = [entry for entry in entries if str(entry.get("run_id")) in run_ids]
    if limit is not None and limit >= 0:
        entries = entries[-limit:] if limit else []

    existing_digests = {
        str(entry.get("outcome_digest"))
        for entry in read_outcome_entries(ledger_dir)
        if entry.get("outcome_digest")
    }
    root_output_dir = output_dir or default_outcome_dir()
    rows: list[dict[str, Any]] = []
    created = 0
    skipped_existing = 0
    would_create = 0
    failed = 0
    evaluated = 0

    for entry in entries:
        run_id = str(entry.get("run_id") or "")
        packet_path_raw = entry.get("packet_copy_path")
        if not isinstance(packet_path_raw, str) or not packet_path_raw:
            failed += 1
            rows.append(
                {
                    "run_id": run_id,
                    "status": "failed",
                    "error": "ledger entry has no packet_copy_path",
                }
            )
            continue
        try:
            packet = json.loads(Path(packet_path_raw).read_text(encoding="utf-8"))
            if not isinstance(packet, dict):
                raise ValueError("packet copy must be a JSON object")
            windows = _candidate_windows(
                packet,
                price_dir=price_dir,
                start_date=start_date,
                end_date=end_date,
                horizon_rows=horizon_rows,
                rolling=rolling,
                stride_rows=stride_rows,
                max_windows=max_windows,
            )
        except Exception as exc:
            failed += 1
            rows.append(
                {
                    "run_id": run_id,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            continue

        for window in windows:
            try:
                outcome = evaluate_outcome(
                    packet,
                    price_dir=price_dir,
                    start_date=window["start_date"],
                    end_date=window["end_date"],
                    horizon_rows=window["horizon_rows"],
                    cash_return=cash_return,
                )
                evaluated += 1
                digest = str(outcome["outcome_digest"])
                row = {
                    "run_id": run_id,
                    "window": outcome["window"],
                    "outcome_digest": digest,
                    "scorecard_ok": outcome["scorecard"]["ok"],
                    "excess_vs_cash": outcome["returns"]["excess_vs_cash"],
                    "excess_vs_equal_weight": outcome["returns"]["excess_vs_equal_weight"],
                    "realized_max_drawdown": outcome["risk"]["realized_max_drawdown"],
                }
                if digest in existing_digests:
                    skipped_existing += 1
                    rows.append({**row, "status": "skipped_existing"})
                    continue
                if dry_run:
                    would_create += 1
                    rows.append({**row, "status": "would_create"})
                    continue
                path = write_outcome(outcome, root_output_dir)
                ledger_entry = ingest_outcome(
                    outcome,
                    outcome_path=path,
                    ledger_dir=ledger_dir,
                )
                existing_digests.add(digest)
                created += 1
                rows.append(
                    {
                        **row,
                        "status": "created",
                        "outcome_path": str(path),
                        "ledger_outcome_digest": ledger_entry.get("outcome_digest"),
                    }
                )
            except Exception as exc:
                failed += 1
                rows.append(
                    {
                        "run_id": run_id,
                        "window": window,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

    return {
        "ledger_dir": str(ledger_dir.expanduser().resolve()),
        "price_dir": str(price_dir.expanduser().resolve()),
        "output_dir": str(root_output_dir.expanduser().resolve()),
        "dry_run": dry_run,
        "rolling": rolling,
        "run_count": len(entries),
        "evaluated": evaluated,
        "created": created,
        "skipped_existing": skipped_existing,
        "would_create": would_create,
        "failed": failed,
        "rows": rows,
    }
