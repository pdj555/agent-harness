"""Executable adapters for sibling decision engines."""

from __future__ import annotations

import contextlib
import importlib
import io
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
    repo_dirty: bool | None = None


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


def _repo_dirty(repo_path: Path) -> bool | None:
    output = _git_output(repo_path, ("status", "--porcelain"))
    if output is None:
        return None
    return bool(output)


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
        repo_sha = _repo_sha(self.repo_path)
        repo_dirty = _repo_dirty(self.repo_path)
        if not self.repo_path.exists():
            return AdapterStatus(
                name="monte-carlo",
                available=False,
                repo_path=self.repo_path,
                reason="repository not found",
                command=self.default_command(("AAPL", "MSFT")),
                capabilities=("simulation", "backtest", "risk_guardrails", "allocation"),
                repo_sha=repo_sha,
                repo_dirty=repo_dirty,
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
                command=self.default_command(("AAPL", "MSFT")),
                capabilities=("simulation", "backtest", "risk_guardrails", "allocation"),
                repo_sha=repo_sha,
                repo_dirty=repo_dirty,
            )
        return AdapterStatus(
            name="monte-carlo",
            available=True,
            repo_path=self.repo_path,
            reason="public_cli execution functions found",
            command=self.default_command(("AAPL", "MSFT")),
            capabilities=("simulation", "backtest", "risk_guardrails", "allocation"),
            repo_sha=repo_sha,
            repo_dirty=repo_dirty,
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

    def status(self) -> AdapterStatus:
        repo_sha = _repo_sha(self.repo_path)
        repo_dirty = _repo_dirty(self.repo_path)
        command = (
            "python3",
            "-m",
            "stock_sentiment",
            "analyze",
            "AAPL",
            "--format",
            "json",
            "--include-reasons",
        )
        if not self.repo_path.exists():
            return AdapterStatus(
                name="stock-sentiment-analysis",
                available=False,
                repo_path=self.repo_path,
                reason="repository not found",
                command=command,
                required_env=("OPENAI_API_KEY",),
                capabilities=("news", "sentiment", "catalyst_overlay"),
                repo_sha=repo_sha,
                repo_dirty=repo_dirty,
            )
        if not (self.repo_path / "stock_sentiment" / "cli.py").exists():
            return AdapterStatus(
                name="stock-sentiment-analysis",
                available=False,
                repo_path=self.repo_path,
                reason="stock_sentiment CLI package not found",
                command=command,
                required_env=("OPENAI_API_KEY",),
                capabilities=("news", "sentiment", "catalyst_overlay"),
                repo_sha=repo_sha,
                repo_dirty=repo_dirty,
            )
        if not os.environ.get("OPENAI_API_KEY"):
            return AdapterStatus(
                name="stock-sentiment-analysis",
                available=False,
                repo_path=self.repo_path,
                reason="OPENAI_API_KEY not set; sentiment is configured but not live",
                command=command,
                required_env=("OPENAI_API_KEY",),
                capabilities=("news", "sentiment", "catalyst_overlay"),
                repo_sha=repo_sha,
                repo_dirty=repo_dirty,
            )
        return AdapterStatus(
            name="stock-sentiment-analysis",
            available=True,
            repo_path=self.repo_path,
            reason="CLI package found and OPENAI_API_KEY is set",
            command=command,
            required_env=("OPENAI_API_KEY",),
            capabilities=("news", "sentiment", "catalyst_overlay"),
            repo_sha=repo_sha,
            repo_dirty=repo_dirty,
        )


class LocalLedgerAdapter:
    """Adapter status for the harness-owned provenance ledger."""

    def __init__(self, repo_path: Path, ledger_dir: Path) -> None:
        self.repo_path = repo_path.expanduser().resolve()
        self.ledger_dir = ledger_dir.expanduser()

    def status(self) -> AdapterStatus:
        repo_sha = _repo_sha(self.repo_path)
        repo_dirty = _repo_dirty(self.repo_path)
        return AdapterStatus(
            name="agent-harness-ledger",
            available=True,
            repo_path=self.repo_path,
            reason=f"local append-only ledger configured at {self.ledger_dir}",
            command=("agent-harness", "ledger", "list"),
            capabilities=("provenance", "run_ledger", "replay", "eval"),
            contract_version="1",
            repo_sha=repo_sha,
            repo_dirty=repo_dirty,
        )


class ResearchRunPlatformAdapter:
    """Readiness manifest for the sibling ``research-run-platform`` repo."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.expanduser().resolve()

    def status(self) -> AdapterStatus:
        repo_sha = _repo_sha(self.repo_path)
        repo_dirty = _repo_dirty(self.repo_path)
        command = ("agent-harness", "ledger", "sync", "research-run-platform")
        if not self.repo_path.exists():
            return AdapterStatus(
                name="research-run-platform",
                available=False,
                repo_path=self.repo_path,
                reason="repository not found locally; local ledger remains active",
                command=command,
                capabilities=("provenance", "run_explorer", "duckdb", "audit"),
                repo_sha=repo_sha,
                repo_dirty=repo_dirty,
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
                capabilities=("provenance", "run_explorer", "duckdb", "audit"),
                repo_sha=repo_sha,
                repo_dirty=repo_dirty,
            )
        return AdapterStatus(
            name="research-run-platform",
            available=True,
            repo_path=self.repo_path,
            reason="repository found; ready for future ledger sync adapter",
            command=command,
            capabilities=("provenance", "run_explorer", "duckdb", "audit"),
            repo_sha=repo_sha,
            repo_dirty=repo_dirty,
        )
