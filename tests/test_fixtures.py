from __future__ import annotations

import json
from pathlib import Path

from agent_harness.cli import main
from agent_harness.fixtures import (
    FIXTURE_UNIVERSE_SCHEMA_VERSION,
    refresh_fixture_universe,
    audit_fixture_universe,
    write_fixture_refresh_report,
    write_fixture_universe_report,
)


def _write_price(price_dir: Path, ticker: str, prices: list[int]) -> None:
    rows = ["Date,Close"]
    for index, close in enumerate(prices, start=2):
        rows.append(f"2024-01-{index:02d},{close}")
    (price_dir / f"{ticker}.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_diverse_prices(price_dir: Path) -> None:
    price_dir.mkdir(parents=True, exist_ok=True)
    _write_price(price_dir, "AAPL", [100, 102, 101, 103, 104, 102])
    _write_price(price_dir, "MSFT", [100, 99, 101, 100, 102, 101])
    _write_price(price_dir, "GOOGL", [100, 101, 103, 102, 105, 106])
    _write_price(price_dir, "JPM", [100, 100, 99, 101, 100, 102])
    _write_price(price_dir, "XOM", [100, 98, 99, 97, 100, 99])


def test_audit_fixture_universe_records_hashes_sectors_and_correlations(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_diverse_prices(price_dir)

    report = audit_fixture_universe(
        price_dir=price_dir,
        min_tickers=5,
        min_rows=6,
        min_common_dates=6,
        min_sectors=3,
        max_pairwise_abs_correlation=0.98,
    )

    assert report["schema_version"] == FIXTURE_UNIVERSE_SCHEMA_VERSION
    assert report["summary"]["ok"]
    assert report["summary"]["ticker_count"] == 5
    assert report["summary"]["known_sector_count"] >= 3
    assert len(report["fixture_universe_digest"]) == 64
    assert report["summary"]["max_abs_correlation"] < 0.98
    assert report["correlations"]["return_observation_count"] == 5
    assert report["correlations"]["matrix"]["AAPL"]["AAPL"] == 1.0
    aapl = next(row for row in report["fixtures"] if row["ticker"] == "AAPL")
    assert aapl["sector"] == "Information Technology"
    assert len(aapl["sha256"]) == 64


def test_audit_fixture_universe_blocks_highly_correlated_universe(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    price_dir.mkdir()
    for ticker in ("AAPL", "MSFT", "GOOGL", "JPM", "XOM"):
        _write_price(price_dir, ticker, [100, 101, 102, 103, 104, 105])

    report = audit_fixture_universe(
        price_dir=price_dir,
        min_tickers=5,
        min_rows=6,
        min_common_dates=6,
        min_sectors=3,
        max_pairwise_abs_correlation=0.98,
    )

    assert not report["summary"]["ok"]
    assert any("max pairwise absolute correlation" in blocker for blocker in report["summary"]["blockers"])


def test_write_fixture_universe_report_updates_latest(tmp_path: Path) -> None:
    price_dir = tmp_path / "prices"
    _write_diverse_prices(price_dir)
    report = audit_fixture_universe(price_dir=price_dir, min_tickers=5, min_rows=6)

    path = write_fixture_universe_report(report, tmp_path / "fixtures")

    assert path.exists()
    latest = json.loads((tmp_path / "fixtures" / "latest.json").read_text(encoding="utf-8"))
    assert latest["fixture_universe_digest"] == report["fixture_universe_digest"]


def test_refresh_fixture_universe_writes_stooq_csvs_and_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payloads = {
        "aapl.us": "Date,Open,High,Low,Close,Volume\n2024-01-02,1,1,1,100,10\n2024-01-03,1,1,1,101,10\n",
        "msft.us": "Date,Open,High,Low,Close,Volume\n2024-01-02,1,1,1,200,10\n2024-01-03,1,1,1,199,10\n",
    }

    def fake_download(url: str, *, timeout: float, verify_tls: bool = True) -> str:
        assert timeout == 3.0
        assert verify_tls is True
        symbol = url.split("s=")[1].split("&")[0]
        assert "d1=20240102" in url
        assert "d2=20240103" in url
        return payloads[symbol]

    monkeypatch.setattr("agent_harness.fixtures._download_text", fake_download)

    report = refresh_fixture_universe(
        price_dir=tmp_path / "prices",
        tickers=["AAPL", "MSFT"],
        start_date="2024-01-02",
        end_date="2024-01-03",
        timeout=3.0,
        run_audit=True,
        min_tickers=2,
        min_rows=2,
        min_common_dates=2,
        min_sectors=1,
    )

    assert report["summary"]["ok"]
    assert report["summary"]["refreshed_count"] == 2
    assert len(report["fixture_refresh_digest"]) == 64
    assert (tmp_path / "prices" / "AAPL.csv").read_text(encoding="utf-8") == (
        "Date,Close\n2024-01-02,100\n2024-01-03,101\n"
    )
    assert report["audit"]["summary"]["ticker_count"] == 2


def test_refresh_fixture_universe_rejects_stooq_html_challenge(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_harness.fixtures._download_text",
        lambda url, *, timeout, verify_tls=True: "<html>challenge</html>",
    )

    report = refresh_fixture_universe(
        price_dir=tmp_path / "prices",
        tickers=["AAPL"],
        run_audit=True,
    )

    assert not report["summary"]["ok"]
    assert report["audit"] is None
    assert report["summary"]["blockers"] == [
        "AAPL: external source returned HTML instead of CSV for AAPL"
    ]


def test_refresh_fixture_universe_from_csv_dir(tmp_path: Path) -> None:
    source_dir = tmp_path / "vendor"
    source_dir.mkdir()
    (source_dir / "AAPL.csv").write_text(
        "Date,Open,High,Low,Close,Volume\n2024-01-02,1,1,1,100,10\n2024-01-03,1,1,1,101,10\n",
        encoding="utf-8",
    )

    report = refresh_fixture_universe(
        price_dir=tmp_path / "prices",
        tickers=["AAPL"],
        source="csv-dir",
        source_dir=source_dir,
        run_audit=False,
    )

    assert report["summary"]["ok"]
    assert report["fixtures"][0]["source_path"] == str(source_dir / "AAPL.csv")
    assert len(report["fixtures"][0]["source_sha256"]) == 64
    assert (tmp_path / "prices" / "AAPL.csv").read_text(encoding="utf-8") == (
        "Date,Close\n2024-01-02,100\n2024-01-03,101\n"
    )


def test_write_fixture_refresh_report_updates_latest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_harness.fixtures._download_text",
        lambda url, *, timeout, verify_tls=True: (
            "Date,Open,High,Low,Close,Volume\n"
            "2024-01-02,1,1,1,100,10\n"
            "2024-01-03,1,1,1,101,10\n"
        ),
    )
    report = refresh_fixture_universe(
        price_dir=tmp_path / "prices",
        tickers=["AAPL"],
        run_audit=False,
    )

    path = write_fixture_refresh_report(report, tmp_path / "fixtures")

    assert path.exists()
    latest = json.loads((tmp_path / "fixtures" / "latest-refresh.json").read_text(encoding="utf-8"))
    assert latest["fixture_refresh_digest"] == report["fixture_refresh_digest"]


def test_refresh_fixture_universe_skips_audit_after_download_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "agent_harness.fixtures._download_text",
        lambda url, *, timeout, verify_tls=True: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    report = refresh_fixture_universe(
        price_dir=tmp_path / "prices",
        tickers=["AAPL"],
        run_audit=True,
    )

    assert not report["summary"]["ok"]
    assert report["summary"]["failed_count"] == 1
    assert report["summary"]["audit_ok"] is None
    assert report["audit"] is None
    assert report["summary"]["blockers"] == ["AAPL: offline"]


def test_fixtures_audit_cli_saves_ready_report(tmp_path: Path, capsys) -> None:
    price_dir = tmp_path / "prices"
    output_dir = tmp_path / "fixture-reports"
    _write_diverse_prices(price_dir)

    exit_code = main(
        [
            "--namespace-root",
            str(tmp_path),
            "fixtures",
            "audit",
            "--price-dir",
            str(price_dir),
            "--min-tickers",
            "5",
            "--min-rows",
            "6",
            "--min-common-dates",
            "6",
            "--min-sectors",
            "3",
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Fixture universe: READY" in captured.out
    assert (output_dir / "latest.json").exists()


def test_fixtures_refresh_cli_saves_ready_report(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "agent_harness.fixtures._download_text",
        lambda url, *, timeout, verify_tls=True: (
            "Date,Open,High,Low,Close,Volume\n"
            "2024-01-02,1,1,1,100,10\n"
            "2024-01-03,1,1,1,101,10\n"
        ),
    )
    output_dir = tmp_path / "fixture-reports"

    exit_code = main(
        [
            "--namespace-root",
            str(tmp_path),
            "fixtures",
            "refresh",
            "AAPL",
            "--price-dir",
            str(tmp_path / "prices"),
            "--no-audit",
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Fixture refresh: READY" in captured.out
    assert (output_dir / "latest-refresh.json").exists()


def test_fixtures_refresh_cli_supports_csv_dir_source(tmp_path: Path, capsys) -> None:
    source_dir = tmp_path / "vendor"
    source_dir.mkdir()
    (source_dir / "AAPL.csv").write_text(
        "Date,Open,High,Low,Close,Volume\n2024-01-02,1,1,1,100,10\n2024-01-03,1,1,1,101,10\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--namespace-root",
            str(tmp_path),
            "fixtures",
            "refresh",
            "AAPL",
            "--source",
            "csv-dir",
            "--source-dir",
            str(source_dir),
            "--price-dir",
            str(tmp_path / "prices"),
            "--no-audit",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Source: csv-dir" in captured.out
    assert (tmp_path / "prices" / "AAPL.csv").exists()
