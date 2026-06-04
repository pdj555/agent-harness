"""Repository discovery for sibling decision engines."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


REPO_MARKERS = (
    ".git",
    "README.md",
    "README",
    "pyproject.toml",
    "package.json",
    "pom.xml",
    "Dockerfile",
)


@dataclass(frozen=True)
class RepoSpec:
    """Known repository with a useful role in the harness."""

    name: str
    path: Path
    purpose: str
    stack: str
    capabilities: tuple[str, ...]
    required: bool = False

    @property
    def exists(self) -> bool:
        return self.path.exists() and self.path.is_dir()


def default_namespace_root(cwd: Path | None = None) -> Path:
    """Return the local namespace root containing sibling repositories."""

    override = os.environ.get("AGENT_HARNESS_NAMESPACE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    base = (cwd or Path.cwd()).resolve()
    if base.name == "agent-harness":
        return base.parent
    return base


def looks_like_repo(path: Path) -> bool:
    """Return whether a directory has common repository markers."""

    if not path.is_dir():
        return False
    return any((path / marker).exists() for marker in REPO_MARKERS)


def discover_repositories(namespace_root: Path) -> dict[str, Path]:
    """Discover immediate child repositories under a namespace root."""

    root = namespace_root.expanduser().resolve()
    if not root.exists():
        return {}

    repos: dict[str, Path] = {}
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.name in {"node_modules", ".venv", "venv", "dist", "build", ".git"}:
            continue
        if looks_like_repo(child):
            repos[child.name] = child
    return repos


def known_repo_specs(namespace_root: Path) -> list[RepoSpec]:
    """Return the high-signal repo map this harness knows how to use."""

    root = namespace_root.expanduser().resolve()
    return [
        RepoSpec(
            name="agent-harness",
            path=root / "agent-harness",
            purpose="Capital research orchestration, run packets, replay, eval, and provenance ledger.",
            stack="Python",
            capabilities=("orchestration", "run_packets", "provenance", "eval"),
            required=True,
        ),
        RepoSpec(
            name="monte-carlo",
            path=root / "monte-carlo",
            purpose="Forward simulation, ranking, guardrails, allocation, and walk-forward validation.",
            stack="Python + Next.js",
            capabilities=("simulation", "backtest", "risk_guardrails", "allocation"),
            required=True,
        ),
        RepoSpec(
            name="stock-sentiment-analysis",
            path=root / "stock-sentiment-analysis",
            purpose="News ingestion, classification, sentiment scoring, and catalyst summaries.",
            stack="Python + Next.js",
            capabilities=("news", "sentiment", "catalyst_overlay"),
        ),
        RepoSpec(
            name="energy-market-visualization",
            path=root / "energy-market-visualization",
            purpose="Synthetic wholesale power market telemetry and reactive dashboard surfaces.",
            stack="Java + Next.js",
            capabilities=("power_prices", "market_microstructure", "dashboard"),
        ),
        RepoSpec(
            name="research-run-platform",
            path=root / "research-run-platform",
            purpose="Parquet/DuckDB/FastAPI research-run provenance and run exploration.",
            stack="Python",
            capabilities=("provenance", "run_ledger", "duckdb", "audit"),
        ),
    ]
