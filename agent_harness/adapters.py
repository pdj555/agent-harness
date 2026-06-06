"""Executable adapters for sibling decision engines."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


MONTE_CARLO_MODULES = (
    "analysis",
    "ai",
    "backtest",
    "cli_shared",
    "data",
    "decision",
    "legacy_cli",
    "public_cli",
    "simulate_cli",
    "simulation",
    "viz",
)

STOCK_SENTIMENT_MODULES = (
    "stock_sentiment",
    "stock_sentiment.ai_credentials",
    "stock_sentiment.cache",
    "stock_sentiment.cli",
    "stock_sentiment.env",
    "stock_sentiment.errors",
    "stock_sentiment.google_rss",
    "stock_sentiment.newsapi",
    "stock_sentiment.openai_client",
    "stock_sentiment.runtime",
    "stock_sentiment.sentiment",
    "stock_sentiment.types",
)


@dataclass(frozen=True)
class AdapterStatus:
    """Readiness report for a repository adapter."""

    name: str
    available: bool
    repo_path: Path
    reason: str
    command: tuple[str, ...] = ()
    required_env: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    contract_version: str = "1"
    repo_sha: str | None = None
    repo_branch: str | None = None
    repo_dirty: bool | None = None
    repo_status: tuple[str, ...] = ()
    repo_status_count: int = 0
    repo_status_truncated: bool = False


@dataclass(frozen=True)
class EngineRun:
    """Result of running a sibling engine."""

    name: str
    ok: bool
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    duration_ms: int = 0
    repo_sha: str | None = None
    repo_dirty: bool | None = None


def _to_builtin(value: Any) -> Any:
    """Convert common numeric/container objects into JSON-safe primitives."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _to_builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_builtin(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _to_builtin(value.item())
        except Exception:
            pass
    return str(value)


def _git_output(repo_path: Path, args: tuple[str, ...]) -> str | None:
    """Return stripped git output for a repo, or ``None`` when unavailable."""

    try:
        completed = subprocess.run(
            ("git", "-C", str(repo_path), *args),
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _repo_sha(repo_path: Path) -> str | None:
    return _git_output(repo_path, ("rev-parse", "HEAD"))


def _repo_branch(repo_path: Path) -> str | None:
    return _git_output(repo_path, ("rev-parse", "--abbrev-ref", "HEAD"))


def _repo_status_lines(repo_path: Path) -> tuple[str, ...] | None:
    output = _git_output(repo_path, ("status", "--porcelain=v1"))
    if output is None:
        return None
    return tuple(line for line in output.splitlines() if line.strip())


def _repo_fingerprint(repo_path: Path, *, status_limit: int = 50) -> dict[str, Any]:
    """Return compact git trust metadata for a repository path."""

    status_lines = _repo_status_lines(repo_path)
    if status_lines is None:
        return {
            "repo_sha": _repo_sha(repo_path),
            "repo_branch": _repo_branch(repo_path),
            "repo_dirty": None,
            "repo_status": (),
            "repo_status_count": 0,
            "repo_status_truncated": False,
        }
    scoped = status_lines[:status_limit]
    return {
        "repo_sha": _repo_sha(repo_path),
        "repo_branch": _repo_branch(repo_path),
        "repo_dirty": bool(status_lines),
        "repo_status": scoped,
        "repo_status_count": len(status_lines),
        "repo_status_truncated": len(status_lines) > len(scoped),
    }


@contextlib.contextmanager
def _module_context(repo_path: Path, *, env: dict[str, str] | None = None) -> Iterator[None]:
    """Temporarily import top-level modules from a sibling repository."""

    old_cwd = Path.cwd()
    old_path = list(sys.path)
    old_env: dict[str, str | None] = {}
    try:
        if env:
            for key, value in env.items():
                old_env[key] = os.environ.get(key)
                os.environ[key] = value
        os.chdir(repo_path)
        sys.path.insert(0, str(repo_path))
        yield
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def _isolated_modules(module_names: tuple[str, ...]) -> Iterator[None]:
    """Temporarily evict top-level modules that sibling repos import by name."""

    previous = {name: sys.modules.get(name) for name in module_names}
    try:
        for name in module_names:
            sys.modules.pop(name, None)
        yield
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class MonteCarloAdapter:
    """Direct adapter for the sibling ``monte-carlo`` decision engine."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.expanduser().resolve()

    def status(self) -> AdapterStatus:
        fingerprint = _repo_fingerprint(self.repo_path)
        if not self.repo_path.exists():
            return AdapterStatus(
                name="monte-carlo",
                available=False,
                repo_path=self.repo_path,
                reason="repository not found",
                command=self.default_command(("AAPL", "MSFT", "GOOGL", "JPM", "XOM")),
                capabilities=("simulation", "backtest", "risk_guardrails", "allocation"),
                **fingerprint,
            )
        missing = [
            filename
            for filename in ("public_cli.py", "simulate_cli.py", "decision.py")
            if not (self.repo_path / filename).exists()
        ]
        if missing:
            return AdapterStatus(
                name="monte-carlo",
                available=False,
                repo_path=self.repo_path,
                reason=f"missing expected files: {', '.join(missing)}",
                command=self.default_command(("AAPL", "MSFT", "GOOGL", "JPM", "XOM")),
                capabilities=("simulation", "backtest", "risk_guardrails", "allocation"),
                **fingerprint,
            )
        return AdapterStatus(
            name="monte-carlo",
            available=True,
            repo_path=self.repo_path,
            reason="public_cli execution functions found",
            command=self.default_command(("AAPL", "MSFT", "GOOGL", "JPM", "XOM")),
            capabilities=("simulation", "backtest", "risk_guardrails", "allocation"),
            **fingerprint,
        )

    def default_command(self, tickers: tuple[str, ...]) -> tuple[str, ...]:
        return (
            "python3",
            "-c",
            "import public_cli; raise SystemExit(public_cli.main())",
            "simulate",
            *tickers,
            "--source",
            "offline",
            "--data-path",
            "sample_data",
            "--days",
            "252",
            "--scenarios",
            "1000",
            "--seed",
            "42",
        )

    def default_backtest_command(self, tickers: tuple[str, ...]) -> tuple[str, ...]:
        return (
            "python3",
            "-c",
            "import public_cli; raise SystemExit(public_cli.main())",
            "backtest",
            *tickers,
            "--source",
            "offline",
            "--data-path",
            "sample_data",
            "--lookback",
            "3",
            "--hold",
            "2",
            "--rebalance",
            "2",
            "--scenarios",
            "20",
            "--seed",
            "42",
        )

    def run_offline_simulation(
        self,
        tickers: tuple[str, ...],
        *,
        days: int = 252,
        scenarios: int = 1000,
        seed: int = 42,
        details: bool = False,
    ) -> EngineRun:
        """Run the real offline Monte Carlo engine and normalize the result."""

        status = self.status()
        if not status.available:
            return EngineRun(
                name="monte-carlo",
                ok=False,
                summary=status.reason,
                diagnostics=(status.reason,),
            )

        argv = [
            "simulate",
            *tickers,
            "--source",
            "offline",
            "--data-path",
            "sample_data",
            "--days",
            str(days),
            "--scenarios",
            str(scenarios),
            "--seed",
            str(seed),
        ]
        if details:
            argv.append("--details")

        command = ("monte-carlo", *argv)
        started = time.perf_counter()
        try:
            with tempfile.TemporaryDirectory(prefix="agent-harness-mpl-") as mpl_dir:
                cache_dir = Path(mpl_dir) / "cache"
                mpl_config_dir = Path(mpl_dir) / "matplotlib"
                cache_dir.mkdir(parents=True, exist_ok=True)
                mpl_config_dir.mkdir(parents=True, exist_ok=True)
                stderr_buffer = io.StringIO()
                with contextlib.redirect_stderr(stderr_buffer):
                    with _isolated_modules(MONTE_CARLO_MODULES):
                        with _module_context(
                            self.repo_path,
                            env={
                                "MPLBACKEND": "Agg",
                                "MPLCONFIGDIR": str(mpl_config_dir),
                                "XDG_CACHE_HOME": str(cache_dir),
                            },
                        ):
                            public_cli = importlib.import_module("public_cli")
                            args = public_cli.parse_public_args(argv)
                            result = public_cli.execute_public_simulate(args)
                            summary = public_cli.format_public_simulation_output(
                                result,
                                details=details,
                                output=None,
                            )
            report = result.get("report", {}) if isinstance(result, dict) else {}
            diagnostics = tuple(
                line.strip()
                for line in stderr_buffer.getvalue().splitlines()
                if line.strip()
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            return EngineRun(
                name="monte-carlo",
                ok=True,
                summary=summary,
                payload={
                    "action_plan": _to_builtin(report.get("action_plan", {})),
                    "rankings": _to_builtin(report.get("rankings", {})),
                    "allocations": _to_builtin(report.get("allocations", {})),
                    "errors": _to_builtin(report.get("errors", [])),
                },
                diagnostics=diagnostics,
                command=command,
                duration_ms=duration_ms,
                repo_sha=status.repo_sha,
                repo_dirty=status.repo_dirty,
            )
        except Exception as exc:  # pragma: no cover - exercised in integration smoke
            duration_ms = int((time.perf_counter() - started) * 1000)
            return EngineRun(
                name="monte-carlo",
                ok=False,
                summary=f"monte-carlo execution failed: {exc}",
                diagnostics=(type(exc).__name__, str(exc)),
                command=command,
                duration_ms=duration_ms,
                repo_sha=status.repo_sha,
                repo_dirty=status.repo_dirty,
            )

    def run_offline_backtest(
        self,
        tickers: tuple[str, ...],
        *,
        lookback: int = 3,
        hold: int = 2,
        rebalance: int = 2,
        scenarios: int = 20,
        seed: int = 42,
        top: int = 1,
        details: bool = False,
    ) -> EngineRun:
        """Run the real offline Monte Carlo walk-forward backtest."""

        status = self.status()
        if not status.available:
            return EngineRun(
                name="monte-carlo-backtest",
                ok=False,
                summary=status.reason,
                diagnostics=(status.reason,),
            )

        argv = [
            "backtest",
            *tickers,
            "--source",
            "offline",
            "--data-path",
            "sample_data",
            "--lookback",
            str(lookback),
            "--hold",
            str(hold),
            "--rebalance",
            str(rebalance),
            "--top",
            str(top),
            "--scenarios",
            str(scenarios),
            "--seed",
            str(seed),
        ]
        if details:
            argv.append("--details")

        command = ("monte-carlo", *argv)
        started = time.perf_counter()
        try:
            with tempfile.TemporaryDirectory(prefix="agent-harness-mpl-") as mpl_dir:
                cache_dir = Path(mpl_dir) / "cache"
                mpl_config_dir = Path(mpl_dir) / "matplotlib"
                cache_dir.mkdir(parents=True, exist_ok=True)
                mpl_config_dir.mkdir(parents=True, exist_ok=True)
                stderr_buffer = io.StringIO()
                with contextlib.redirect_stderr(stderr_buffer):
                    with _isolated_modules(MONTE_CARLO_MODULES):
                        with _module_context(
                            self.repo_path,
                            env={
                                "MPLBACKEND": "Agg",
                                "MPLCONFIGDIR": str(mpl_config_dir),
                                "XDG_CACHE_HOME": str(cache_dir),
                            },
                        ):
                            public_cli = importlib.import_module("public_cli")
                            args = public_cli.parse_public_args(argv)
                            result = public_cli.execute_public_backtest(args)
                            summary = public_cli.format_public_backtest_output(
                                result,
                                details=details,
                                output=None,
                            )
            summary_series = result.get("summary") if isinstance(result, dict) else None
            summary_payload = (
                _to_builtin(summary_series.to_dict())
                if hasattr(summary_series, "to_dict")
                else _to_builtin(summary_series)
            )
            diagnostics = tuple(
                line.strip()
                for line in stderr_buffer.getvalue().splitlines()
                if line.strip()
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            return EngineRun(
                name="monte-carlo-backtest",
                ok=True,
                summary=summary,
                payload={
                    "summary": summary_payload,
                    "price_sources": _to_builtin(
                        result.get("price_sources", {}) if isinstance(result, dict) else {}
                    ),
                },
                diagnostics=diagnostics,
                command=command,
                duration_ms=duration_ms,
                repo_sha=status.repo_sha,
                repo_dirty=status.repo_dirty,
            )
        except Exception as exc:  # pragma: no cover - exercised in integration smoke
            duration_ms = int((time.perf_counter() - started) * 1000)
            return EngineRun(
                name="monte-carlo-backtest",
                ok=False,
                summary=f"monte-carlo backtest failed: {exc}",
                diagnostics=(type(exc).__name__, str(exc)),
                command=command,
                duration_ms=duration_ms,
                repo_sha=status.repo_sha,
                repo_dirty=status.repo_dirty,
            )


class StockSentimentAdapter:
    """Adapter manifest for the sibling sentiment engine."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.expanduser().resolve()

    def default_command(
        self,
        ticker: str,
        *,
        days: int = 3,
        max_articles: int = 10,
        source: str = "auto",
        half_life_hours: float = 24.0,
        include_reasons: bool = False,
    ) -> tuple[str, ...]:
        command = [
            "python3",
            "-m",
            "stock_sentiment",
            "analyze",
            ticker,
            "--format",
            "json",
            "--days",
            str(days),
            "--max-articles",
            str(max_articles),
            "--source",
            source,
            "--half-life-hours",
            str(half_life_hours),
        ]
        if include_reasons:
            command.append("--include-reasons")
        return tuple(command)

    def status(self) -> AdapterStatus:
        fingerprint = _repo_fingerprint(self.repo_path)
        command = self.default_command("AAPL", include_reasons=True)
        if not self.repo_path.exists():
            return AdapterStatus(
                name="stock-sentiment-analysis",
                available=False,
                repo_path=self.repo_path,
                reason="repository not found",
                command=command,
                required_env=("OPENAI_API_KEY", "OLLAMA_API_KEY"),
                capabilities=("news", "sentiment", "catalyst_overlay"),
                **fingerprint,
            )
        if not (self.repo_path / "stock_sentiment" / "cli.py").exists():
            return AdapterStatus(
                name="stock-sentiment-analysis",
                available=False,
                repo_path=self.repo_path,
                reason="stock_sentiment CLI package not found",
                command=command,
                required_env=("OPENAI_API_KEY", "OLLAMA_API_KEY"),
                capabilities=("news", "sentiment", "catalyst_overlay"),
                **fingerprint,
            )
        if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("OLLAMA_API_KEY")):
            return AdapterStatus(
                name="stock-sentiment-analysis",
                available=False,
                repo_path=self.repo_path,
                reason="OPENAI_API_KEY or OLLAMA_API_KEY not set; sentiment is configured but not live",
                command=command,
                required_env=("OPENAI_API_KEY", "OLLAMA_API_KEY"),
                capabilities=("news", "sentiment", "catalyst_overlay"),
                **fingerprint,
            )
        return AdapterStatus(
            name="stock-sentiment-analysis",
            available=True,
            repo_path=self.repo_path,
            reason="CLI package found and an OpenAI-compatible API key is set",
            command=command,
            required_env=("OPENAI_API_KEY", "OLLAMA_API_KEY"),
            capabilities=("news", "sentiment", "catalyst_overlay"),
            **fingerprint,
        )

    def run_analysis(
        self,
        ticker: str,
        *,
        days: int = 3,
        max_articles: int = 10,
        source: str = "auto",
        half_life_hours: float = 24.0,
        include_reasons: bool = False,
    ) -> EngineRun:
        """Run the sibling stock-sentiment JSON CLI and normalize the result."""

        status = self.status()
        command = self.default_command(
            ticker,
            days=days,
            max_articles=max_articles,
            source=source,
            half_life_hours=half_life_hours,
            include_reasons=include_reasons,
        )
        if not status.available:
            return EngineRun(
                name="stock-sentiment-analysis",
                ok=False,
                summary=status.reason,
                diagnostics=(status.reason,),
                command=command,
                repo_sha=status.repo_sha,
                repo_dirty=status.repo_dirty,
            )

        argv = list(command[3:])
        started = time.perf_counter()
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        try:
            with _isolated_modules(STOCK_SENTIMENT_MODULES):
                with _module_context(self.repo_path):
                    sentiment_cli = importlib.import_module("stock_sentiment.cli")
                    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(
                        stderr_buffer
                    ):
                        exit_code = int(sentiment_cli.main(argv) or 0)
            duration_ms = int((time.perf_counter() - started) * 1000)
            diagnostics = tuple(
                line.strip()
                for line in stderr_buffer.getvalue().splitlines()
                if line.strip()
            )
            if exit_code != 0:
                return EngineRun(
                    name="stock-sentiment-analysis",
                    ok=False,
                    summary=f"stock sentiment exited with code {exit_code}",
                    diagnostics=diagnostics,
                    command=command,
                    duration_ms=duration_ms,
                    repo_sha=status.repo_sha,
                    repo_dirty=status.repo_dirty,
                )
            payload = json.loads(stdout_buffer.getvalue())
            if not isinstance(payload, dict):
                raise ValueError("stock sentiment JSON output must be an object")
            summary = (
                f"{payload.get('ticker', ticker)} sentiment "
                f"score={payload.get('score')} label={payload.get('label')} "
                f"confidence={payload.get('confidence')} signal={payload.get('signal')} "
                f"articles={payload.get('articles_analyzed')}"
            )
            return EngineRun(
                name="stock-sentiment-analysis",
                ok=True,
                summary=summary,
                payload=_to_builtin(payload),
                diagnostics=diagnostics,
                command=command,
                duration_ms=duration_ms,
                repo_sha=status.repo_sha,
                repo_dirty=status.repo_dirty,
            )
        except Exception as exc:  # pragma: no cover - exercised by local smoke
            duration_ms = int((time.perf_counter() - started) * 1000)
            diagnostics = tuple(
                line.strip()
                for line in stderr_buffer.getvalue().splitlines()
                if line.strip()
            )
            return EngineRun(
                name="stock-sentiment-analysis",
                ok=False,
                summary=f"stock sentiment execution failed: {exc}",
                diagnostics=(type(exc).__name__, str(exc), *diagnostics),
                command=command,
                duration_ms=duration_ms,
                repo_sha=status.repo_sha,
                repo_dirty=status.repo_dirty,
            )


class LocalLedgerAdapter:
    """Adapter status for the harness-owned provenance ledger."""

    def __init__(self, repo_path: Path, ledger_dir: Path) -> None:
        self.repo_path = repo_path.expanduser().resolve()
        self.ledger_dir = ledger_dir.expanduser()

    def status(self) -> AdapterStatus:
        fingerprint = _repo_fingerprint(self.repo_path)
        return AdapterStatus(
            name="agent-harness-ledger",
            available=True,
            repo_path=self.repo_path,
            reason=f"local append-only ledger configured at {self.ledger_dir}",
            command=("agent-harness", "ledger", "list"),
            capabilities=("provenance", "run_ledger", "replay", "eval"),
            contract_version="1",
            **fingerprint,
        )


class ResearchRunPlatformAdapter:
    """Readiness manifest for the sibling ``research-run-platform`` repo."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.expanduser().resolve()

    def status(self) -> AdapterStatus:
        fingerprint = _repo_fingerprint(self.repo_path)
        command = ("research-run-platform", "ingest", "<export-dir>")
        if not self.repo_path.exists():
            return AdapterStatus(
                name="research-run-platform",
                available=False,
                repo_path=self.repo_path,
                reason="repository not found locally; local ledger remains active",
                command=command,
                capabilities=(
                    "provenance",
                    "run_explorer",
                    "run_evidence_api",
                    "aggregate_evidence",
                    "sqlite",
                    "http_read_api",
                ),
                **fingerprint,
            )
        missing = [
            marker
            for marker in ("pyproject.toml", "README.md")
            if not (self.repo_path / marker).exists()
        ]
        if missing:
            return AdapterStatus(
                name="research-run-platform",
                available=False,
                repo_path=self.repo_path,
                reason=f"repository found but missing expected markers: {', '.join(missing)}",
                command=command,
                capabilities=(
                    "provenance",
                    "run_explorer",
                    "run_evidence_api",
                    "aggregate_evidence",
                    "sqlite",
                    "http_read_api",
                ),
                **fingerprint,
            )
        return AdapterStatus(
            name="research-run-platform",
            available=True,
            repo_path=self.repo_path,
            reason=(
                "repository found; ingest validated bundles and expose run evidence "
                "with research-run-platform evidence"
            ),
            command=command,
            capabilities=(
                "provenance",
                "run_explorer",
                "run_evidence_api",
                "aggregate_evidence",
                "sqlite",
                "http_read_api",
            ),
            **fingerprint,
        )
