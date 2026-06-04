"""Capital thesis scoring for execution loops."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from agent_harness.adapters import AdapterStatus, EngineRun


@dataclass(frozen=True)
class CapitalLoop:
    """A ranked implementation loop that can increase decision quality."""

    name: str
    repo: str
    thesis: str
    expected_edge: float
    confidence: float
    max_loss: float
    implementation_effort: float
    half_life_days: float
    evidence: tuple[str, ...]

    @property
    def score(self) -> float:
        """Risk-adjusted, effort-adjusted score for implementation priority."""

        edge = self.expected_edge * self.confidence
        tail_penalty = self.max_loss * 0.35
        decay_penalty = 1.0 / max(self.half_life_days, 1.0)
        effort = math.sqrt(max(self.implementation_effort, 1.0))
        return (edge - tail_penalty - decay_penalty) / effort


def rank_loops(loops: Iterable[CapitalLoop]) -> list[CapitalLoop]:
    """Return loops sorted by risk-adjusted implementation priority."""

    return sorted(loops, key=lambda loop: loop.score, reverse=True)


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _monte_carlo_signal(run: EngineRun | None) -> tuple[float, float, tuple[str, ...]]:
    """Derive edge/confidence from actual Monte Carlo risk output."""

    if run is None or not run.ok:
        return 0.34, 0.72, ()

    action_plan = run.payload.get("action_plan", {})
    rankings = run.payload.get("rankings", {})
    errors = run.payload.get("errors", [])
    primary_pick = action_plan.get("primary_pick") if isinstance(action_plan, dict) else None
    if not isinstance(primary_pick, dict):
        return 0.28, 0.62, ("monte-carlo ran without a primary pick",)

    ticker = str(primary_pick.get("ticker", "unknown"))
    expected_return = float(primary_pick.get("expected_return", 0.0) or 0.0)
    prob_above = float(primary_pick.get("prob_above_current", 0.5) or 0.5)
    value_at_risk = float(primary_pick.get("value_at_risk_95_pct", 0.0) or 0.0)
    cash_weight = float(action_plan.get("cash_weight", 0.0) or 0.0)

    drawdown = 0.0
    if isinstance(rankings, dict):
        ticker_row = rankings.get(ticker, {})
        if isinstance(ticker_row, dict):
            drawdown = float(ticker_row.get("max_drawdown_q95", 0.0) or 0.0)

    risk_adjusted_signal = (
        expected_return
        + (prob_above - 0.5) * 0.12
        - value_at_risk * 0.55
        - drawdown * 0.35
        + min(cash_weight, 0.5) * 0.04
    )
    edge = _bounded(0.30 + risk_adjusted_signal, 0.12, 0.55)

    confidence = 0.78
    if value_at_risk <= 0.05:
        confidence += 0.06
    if prob_above >= 0.60:
        confidence += 0.04
    if cash_weight >= 0.20:
        confidence += 0.03
    if errors:
        confidence -= 0.12
    if run.repo_dirty:
        confidence -= 0.03
    confidence = _bounded(confidence, 0.25, 0.92)

    evidence = (
        f"{ticker}: expected_return={expected_return:.1%}, "
        f"prob_above_current={prob_above:.0%}, VaR95={value_at_risk:.1%}, "
        f"drawdown_q95={drawdown:.1%}, cash={cash_weight:.1%}"
    )
    return edge, confidence, (evidence,)


def _backtest_signal(run: EngineRun | None) -> tuple[float, tuple[str, ...]]:
    """Return confidence adjustment and evidence from walk-forward validation."""

    if run is None:
        return 0.0, ()
    if not run.ok:
        return -0.10, (f"walk-forward backtest failed: {run.summary}",)

    summary = run.payload.get("summary", {})
    if not isinstance(summary, dict):
        return -0.04, ("walk-forward backtest summary missing",)

    strategy_return = float(summary.get("strategy_total_return", 0.0) or 0.0)
    excess_equal = float(summary.get("excess_return_vs_equal_weight", 0.0) or 0.0)
    excess_cash = float(summary.get("excess_return_vs_cash", 0.0) or 0.0)
    max_drawdown = float(summary.get("strategy_max_drawdown", 0.0) or 0.0)
    win_rate = float(summary.get("strategy_win_rate", 0.0) or 0.0)
    periods = float(summary.get("periods", 0.0) or 0.0)

    adjustment = 0.0
    if periods >= 3:
        adjustment += 0.02
    if strategy_return > 0:
        adjustment += 0.03
    if excess_equal > 0:
        adjustment += 0.03
    if excess_cash > 0:
        adjustment += 0.02
    if max_drawdown > 0.10:
        adjustment -= 0.05
    if win_rate < 0.50:
        adjustment -= 0.04

    evidence = (
        f"walk_forward: return={strategy_return:.1%}, excess_equal={excess_equal:.1%}, "
        f"excess_cash={excess_cash:.1%}, max_drawdown={max_drawdown:.1%}, "
        f"win_rate={win_rate:.0%}, periods={periods:.0f}"
    )
    return _bounded(adjustment, -0.12, 0.10), (evidence,)


def build_capital_loops(
    statuses: dict[str, AdapterStatus],
    *,
    monte_carlo_run: EngineRun | None = None,
    monte_carlo_backtest: EngineRun | None = None,
) -> list[CapitalLoop]:
    """Build ranked capital loops from discovered sibling engines."""

    monte_status = statuses.get("monte-carlo")
    sentiment_status = statuses.get("stock-sentiment-analysis")

    monte_edge, monte_confidence, monte_signal_evidence = _monte_carlo_signal(monte_carlo_run)
    backtest_confidence_adjustment, backtest_evidence = _backtest_signal(monte_carlo_backtest)
    monte_confidence = _bounded(monte_confidence + backtest_confidence_adjustment, 0.25, 0.94)
    if not (monte_status and monte_status.available):
        monte_confidence = 0.25
    monte_evidence = ["monte-carlo exposes simulation, ranking, allocation, and guardrails"]
    if monte_carlo_run and monte_carlo_run.ok:
        action_plan = monte_carlo_run.payload.get("action_plan", {})
        if isinstance(action_plan, dict) and action_plan.get("headline"):
            monte_evidence.append(str(action_plan["headline"]))
        monte_evidence.extend(monte_signal_evidence)
    monte_evidence.extend(backtest_evidence)

    sentiment_confidence = 0.62 if sentiment_status and sentiment_status.available else 0.34
    sentiment_evidence = ["sentiment repo has JSON CLI for ticker-level catalyst scoring"]
    if sentiment_status and not sentiment_status.available:
        sentiment_evidence.append(sentiment_status.reason)

    return rank_loops(
        [
            CapitalLoop(
                name="risk-first allocation loop",
                repo="monte-carlo",
                thesis=(
                    "Use path simulation, VaR, drawdown, and cash buffers as the first "
                    "gate before any catalyst or narrative can reach capital."
                ),
                expected_edge=monte_edge,
                confidence=monte_confidence,
                max_loss=0.11,
                implementation_effort=2.0,
                half_life_days=30.0,
                evidence=tuple(monte_evidence),
            ),
            CapitalLoop(
                name="sentiment catalyst overlay",
                repo="stock-sentiment-analysis",
                thesis=(
                    "Treat news sentiment as a short-half-life catalyst overlay on top "
                    "of simulated base rates, not as a standalone buy signal."
                ),
                expected_edge=0.22,
                confidence=sentiment_confidence,
                max_loss=0.08,
                implementation_effort=2.5,
                half_life_days=3.0,
                evidence=tuple(sentiment_evidence),
            ),
            CapitalLoop(
                name="research-run provenance ledger",
                repo="research-run-platform",
                thesis=(
                    "Persist every hypothesis, run input, output, and rejection reason so "
                    "the system compounds judgment instead of repeating stale analysis."
                ),
                expected_edge=0.29,
                confidence=0.58,
                max_loss=0.03,
                implementation_effort=3.0,
                half_life_days=90.0,
                evidence=("remote repo describes Parquet, DuckDB, FastAPI, provenance, and run explorer",),
            ),
            CapitalLoop(
                name="energy-market basis scanner",
                repo="energy-market-visualization",
                thesis=(
                    "Use power-market telemetry as the non-equity proving ground for "
                    "scarcity, congestion, volatility clustering, and mean reversion."
                ),
                expected_edge=0.18,
                confidence=0.46,
                max_loss=0.04,
                implementation_effort=4.0,
                half_life_days=14.0,
                evidence=("energy market repo exposes market snapshots, forecasts, and insights models",),
            ),
            CapitalLoop(
                name="agent eval and trace harness",
                repo="agent-harness",
                thesis=(
                    "Score agent runs by forecast quality, evidence coverage, latency, "
                    "and rollback cost so orchestration improves with measured feedback."
                ),
                expected_edge=0.24,
                confidence=0.64,
                max_loss=0.02,
                implementation_effort=3.0,
                half_life_days=60.0,
                evidence=("current repo is blank, so the harness can define the audit surface cleanly",),
            ),
        ]
    )
