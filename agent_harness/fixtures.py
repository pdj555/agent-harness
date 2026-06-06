"""Offline price-fixture universe auditing."""

from __future__ import annotations

import hashlib
import json
import math
import os
import ssl
import urllib.parse
import urllib.request
from csv import DictReader
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from agent_harness.outcomes import PricePoint, load_price_series


FIXTURE_UNIVERSE_SCHEMA_VERSION = "agent-harness.fixture-universe.v1"
FIXTURE_REFRESH_SCHEMA_VERSION = "agent-harness.fixture-refresh.v1"
DEFAULT_REFRESH_SOURCE = "stooq"
DEFAULT_REFRESH_TICKERS = ["AAPL", "MSFT", "GOOGL", "JPM", "XOM"]
STOOQ_DAILY_URL = "https://stooq.com/q/d/l/"

DEFAULT_SECTOR_MAP = {
    "AAPL": "Information Technology",
    "AMZN": "Consumer Discretionary",
    "GOOG": "Communication Services",
    "GOOGL": "Communication Services",
    "JPM": "Financials",
    "META": "Communication Services",
    "MSFT": "Information Technology",
    "NVDA": "Information Technology",
    "SPY": "Broad Market ETF",
    "XLE": "Energy ETF",
    "XLV": "Health Care ETF",
    "XOM": "Energy",
}


def default_fixture_report_dir(cwd: Path | None = None) -> Path:
    """Return the default local fixture-audit artifact directory."""

    return (cwd or Path.cwd()) / ".agent-harness" / "fixtures"


def default_price_fixture_dir(namespace_root: Path) -> Path:
    """Return the namespace's default Monte Carlo fixture directory."""

    return namespace_root.expanduser().resolve() / "monte-carlo" / "sample_data"


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


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(payload, encoding="utf-8")
    os.replace(temp_path, path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_digest(payload: dict[str, Any]) -> str:
    scoped = dict(payload)
    scoped.pop("generated_at", None)
    scoped.pop("fixture_universe_digest", None)
    encoded = json.dumps(scoped, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _date_token(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped.replace("-", "")


def _stooq_symbol(ticker: str) -> str:
    raw = ticker.strip().lower()
    return raw if "." in raw else f"{raw}.us"


def _stooq_url(
    ticker: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, str]:
    symbol = _stooq_symbol(ticker)
    query: dict[str, str] = {"s": symbol, "i": "d"}
    start_token = _date_token(start_date)
    end_token = _date_token(end_date)
    if start_token is not None:
        query["d1"] = start_token
    if end_token is not None:
        query["d2"] = end_token
    return f"{STOOQ_DAILY_URL}?{urllib.parse.urlencode(query)}", symbol


def _download_text(url: str, *, timeout: float, verify_tls: bool = True) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "agent-harness/0.1 fixture-refresh"},
    )
    context = None if verify_tls else ssl._create_unverified_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read().decode("utf-8-sig")


def _parse_external_price_csv(payload: str, ticker: str) -> list[PricePoint]:
    if payload.lstrip().startswith("<"):
        raise ValueError(f"external source returned HTML instead of CSV for {ticker}")
    reader = DictReader(StringIO(payload))
    rows: list[PricePoint] = []
    seen_dates: set[str] = set()
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
        raise ValueError(f"external source returned fewer than two price rows for {ticker}")
    return sorted(rows, key=lambda point: point.date)


def _series_csv(series: list[PricePoint]) -> str:
    lines = ["Date,Close"]
    lines.extend(f"{point.date},{point.close:g}" for point in series)
    return "\n".join(lines) + "\n"


def _load_sector_map(path: Path | None) -> dict[str, str]:
    sectors = dict(DEFAULT_SECTOR_MAP)
    if path is None:
        return sectors
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("sector map must be a JSON object")
    for ticker, row in payload.items():
        sector = row.get("sector") if isinstance(row, dict) else row
        if isinstance(sector, str) and sector.strip():
            sectors[str(ticker).upper()] = sector.strip()
    return sectors


def _fixture_tickers(price_dir: Path) -> list[str]:
    root = price_dir.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"fixture directory not found: {root}")
    return sorted(path.stem.upper() for path in root.glob("*.csv") if path.is_file())


def _returns(series: list[PricePoint], dates: list[str]) -> list[float]:
    prices = {point.date: point.close for point in series}
    values = [float(prices[date]) for date in dates]
    returns: list[float] = []
    for previous, current in zip(values, values[1:]):
        if previous <= 0:
            returns.append(0.0)
        else:
            returns.append(current / previous - 1.0)
    return returns


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(0.0, variance))


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left))
    right_denominator = math.sqrt(sum((b - right_mean) ** 2 for b in right))
    denominator = left_denominator * right_denominator
    if denominator == 0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def _common_dates(series_by_ticker: dict[str, list[PricePoint]]) -> list[str]:
    common: set[str] | None = None
    for series in series_by_ticker.values():
        dates = {point.date for point in series}
        common = dates if common is None else common & dates
    return sorted(common or set())


def audit_fixture_universe(
    *,
    price_dir: Path,
    tickers: list[str] | None = None,
    sector_map: Path | None = None,
    min_tickers: int = 5,
    min_rows: int = 10,
    min_common_dates: int = 5,
    min_sectors: int = 3,
    max_pairwise_abs_correlation: float = 0.98,
) -> dict[str, Any]:
    """Audit offline ``Date,Close`` fixture breadth and diversification."""

    root = price_dir.expanduser().resolve()
    sectors = _load_sector_map(sector_map)
    scoped_tickers = [ticker.upper() for ticker in tickers] if tickers else _fixture_tickers(root)
    scoped_tickers = list(dict.fromkeys(scoped_tickers))
    errors: list[str] = []
    blockers: list[str] = []
    series_by_ticker: dict[str, list[PricePoint]] = {}
    rows: list[dict[str, Any]] = []

    for ticker in scoped_tickers:
        path = root / f"{ticker}.csv"
        try:
            series = load_price_series(root, ticker)
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")
            rows.append(
                {
                    "ticker": ticker,
                    "ok": False,
                    "path": str(path),
                    "error": str(exc),
                    "sector": sectors.get(ticker, "Unknown"),
                }
            )
            continue
        series_by_ticker[ticker] = series
        returns = _returns(series, [point.date for point in series])
        total_return = series[-1].close / series[0].close - 1.0
        rows.append(
            {
                "ticker": ticker,
                "ok": True,
                "path": str(path.resolve()),
                "sha256": _file_sha256(path),
                "rows": len(series),
                "first_date": series[0].date,
                "last_date": series[-1].date,
                "sector": sectors.get(ticker, "Unknown"),
                "return_count": len(returns),
                "total_return": total_return,
                "mean_period_return": _mean(returns),
                "period_volatility": _stdev(returns),
            }
        )

    common_dates = _common_dates(series_by_ticker)
    returns_by_ticker = {
        ticker: _returns(series, common_dates)
        for ticker, series in sorted(series_by_ticker.items())
        if len(common_dates) >= 2
    }
    correlation_matrix: dict[str, dict[str, float | None]] = {}
    pairwise_correlations: list[dict[str, Any]] = []
    max_abs_correlation: float | None = None
    max_abs_correlation_pair: list[str] | None = None
    for left in sorted(returns_by_ticker):
        correlation_matrix[left] = {}
        for right in sorted(returns_by_ticker):
            corr = 1.0 if left == right else _pearson(returns_by_ticker[left], returns_by_ticker[right])
            correlation_matrix[left][right] = corr
            if left >= right or corr is None:
                continue
            abs_corr = abs(corr)
            pairwise_correlations.append(
                {
                    "left": left,
                    "right": right,
                    "correlation": corr,
                    "abs_correlation": abs_corr,
                }
            )
            if max_abs_correlation is None or abs_corr > max_abs_correlation:
                max_abs_correlation = abs_corr
                max_abs_correlation_pair = [left, right]

    valid_rows = [row for row in rows if row.get("ok")]
    sector_counts: dict[str, int] = {}
    for row in valid_rows:
        sector = str(row.get("sector") or "Unknown")
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    if errors:
        blockers.extend(errors)
    if len(valid_rows) < min_tickers:
        blockers.append(f"needs at least {min_tickers} valid fixture tickers")
    short_rows = {
        str(row["ticker"]): row.get("rows")
        for row in valid_rows
        if int(row.get("rows") or 0) < min_rows
    }
    if short_rows:
        blockers.append(f"fixture rows below {min_rows}: {short_rows}")
    if len(common_dates) < min_common_dates:
        blockers.append(f"needs at least {min_common_dates} common price dates")
    known_sector_count = len([sector for sector in sector_counts if sector != "Unknown"])
    if known_sector_count < min_sectors:
        blockers.append(f"needs at least {min_sectors} known sectors")
    if (
        max_abs_correlation is not None
        and max_abs_correlation > max_pairwise_abs_correlation
    ):
        blockers.append(
            "max pairwise absolute correlation "
            f"{max_abs_correlation:.4f} exceeds {max_pairwise_abs_correlation:.4f}"
        )

    report = {
        "schema_version": FIXTURE_UNIVERSE_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "price_dir": str(root),
        "sector_map": str(sector_map.expanduser().resolve()) if sector_map else "built_in",
        "parameters": {
            "tickers": scoped_tickers,
            "min_tickers": min_tickers,
            "min_rows": min_rows,
            "min_common_dates": min_common_dates,
            "min_sectors": min_sectors,
            "max_pairwise_abs_correlation": max_pairwise_abs_correlation,
        },
        "summary": {
            "ok": not blockers,
            "ticker_count": len(valid_rows),
            "requested_ticker_count": len(scoped_tickers),
            "sector_count": len(sector_counts),
            "known_sector_count": known_sector_count,
            "sector_counts": dict(sorted(sector_counts.items())),
            "common_date_count": len(common_dates),
            "first_common_date": common_dates[0] if common_dates else None,
            "last_common_date": common_dates[-1] if common_dates else None,
            "max_abs_correlation": max_abs_correlation,
            "max_abs_correlation_pair": max_abs_correlation_pair,
            "blockers": blockers,
        },
        "fixtures": rows,
        "correlations": {
            "return_observation_count": max(0, len(common_dates) - 1),
            "matrix": correlation_matrix,
            "pairs": pairwise_correlations,
        },
    }
    report["fixture_universe_digest"] = _stable_digest(report)
    return report


def refresh_fixture_universe(
    *,
    price_dir: Path,
    tickers: list[str] | None = None,
    source: str = DEFAULT_REFRESH_SOURCE,
    source_dir: Path | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    timeout: float = 15.0,
    verify_tls: bool = True,
    run_audit: bool = True,
    sector_map: Path | None = None,
    min_tickers: int = 5,
    min_rows: int = 10,
    min_common_dates: int = 5,
    min_sectors: int = 3,
    max_pairwise_abs_correlation: float = 0.98,
) -> dict[str, Any]:
    """Refresh local ``Date,Close`` fixtures from an external historical source."""

    if source not in {"stooq", "csv-dir"}:
        raise ValueError(f"unsupported fixture refresh source: {source}")
    source_root = source_dir.expanduser().resolve() if source_dir is not None else None
    if source == "csv-dir" and source_root is None:
        raise ValueError("csv-dir fixture refresh requires source_dir")
    root = price_dir.expanduser().resolve()
    scoped_tickers = [ticker.upper() for ticker in (tickers or DEFAULT_REFRESH_TICKERS)]
    scoped_tickers = list(dict.fromkeys(scoped_tickers))
    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for ticker in scoped_tickers:
        url = None
        symbol = ticker
        source_path = None
        destination = root / f"{ticker}.csv"
        try:
            if source == "stooq":
                url, symbol = _stooq_url(ticker, start_date=start_date, end_date=end_date)
                payload = _download_text(url, timeout=float(timeout), verify_tls=verify_tls)
            else:
                assert source_root is not None
                source_path = source_root / f"{ticker}.csv"
                payload = source_path.read_text(encoding="utf-8")
            series = _parse_external_price_csv(payload, ticker)
            _atomic_write_text(destination, _series_csv(series))
            row = {
                "ticker": ticker,
                "ok": True,
                "source": source,
                "symbol": symbol,
                "url": url,
                "source_path": str(source_path) if source_path is not None else None,
                "source_sha256": _file_sha256(source_path) if source_path is not None else None,
                "path": str(destination),
                "sha256": _file_sha256(destination),
                "rows": len(series),
                "first_date": series[0].date,
                "last_date": series[-1].date,
            }
            rows.append(row)
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")
            rows.append(
                {
                    "ticker": ticker,
                    "ok": False,
                    "source": source,
                    "symbol": symbol,
                    "url": url,
                    "source_path": str(source_path) if source_path is not None else None,
                    "path": str(destination),
                    "error": str(exc),
                }
            )

    audit = (
        audit_fixture_universe(
            price_dir=root,
            tickers=scoped_tickers,
            sector_map=sector_map,
            min_tickers=min_tickers,
            min_rows=min_rows,
            min_common_dates=min_common_dates,
            min_sectors=min_sectors,
            max_pairwise_abs_correlation=max_pairwise_abs_correlation,
        )
        if run_audit and not errors
        else None
    )
    report = {
        "schema_version": FIXTURE_REFRESH_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "source": source,
        "price_dir": str(root),
        "parameters": {
            "tickers": scoped_tickers,
            "source_dir": str(source_root) if source_root is not None else None,
            "start_date": start_date,
            "end_date": end_date,
            "timeout": timeout,
            "verify_tls": verify_tls,
            "run_audit": run_audit,
        },
        "summary": {
            "ok": not errors and (audit is None or bool(audit["summary"]["ok"])),
            "ticker_count": len(scoped_tickers),
            "refreshed_count": len([row for row in rows if row.get("ok")]),
            "failed_count": len([row for row in rows if not row.get("ok")]),
            "audit_ok": audit["summary"]["ok"] if audit is not None else None,
            "blockers": [*errors, *(audit["summary"]["blockers"] if audit is not None else [])],
        },
        "fixtures": rows,
        "audit": audit,
    }
    report["fixture_refresh_digest"] = _stable_digest(report)
    return report


def write_fixture_refresh_report(
    report: dict[str, Any],
    output_dir: Path | None = None,
) -> Path:
    """Persist a fixture refresh report and update latest-refresh.json."""

    root = (output_dir or default_fixture_report_dir()).expanduser().resolve()
    digest = str(report.get("fixture_refresh_digest") or _stable_digest(report))
    path = root / f"fixture_refresh_{digest[:12]}.json"
    _atomic_write_json(path, report)
    _atomic_write_json(root / "latest-refresh.json", report)
    return path


def write_fixture_universe_report(
    report: dict[str, Any],
    output_dir: Path | None = None,
) -> Path:
    """Persist a fixture-universe audit report and update latest.json."""

    root = (output_dir or default_fixture_report_dir()).expanduser().resolve()
    digest = str(report.get("fixture_universe_digest") or _stable_digest(report))
    path = root / f"fixture_universe_{digest[:12]}.json"
    _atomic_write_json(path, report)
    _atomic_write_json(root / "latest.json", report)
    return path
