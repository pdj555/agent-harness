# agent-harness

Production harness for the `pdj555` decision-system repos. The first target is a
capital research loop: discover sibling engines, run the real Monte Carlo risk
engine offline, run a walk-forward validation smoke, rank implementation loops,
run deterministic stress tests, save a durable run packet, ingest it into an
append-only provenance ledger, and make that packet replayable/evaluable without
re-running engines.

This repo does not place trades. It is an evidence, simulation, provenance, and
agent-eval layer for financial decision research.

## Install

```bash
python3 -m pip install -e .
```

## Use

```bash
# Discover sibling repos and adapter readiness
python3 -m agent_harness scan

# Run the default capital thesis against local offline fixtures
python3 -m agent_harness thesis AAPL MSFT --days 30 --scenarios 100 --seed 42

# Disable walk-forward validation only when you are intentionally doing a fast smoke
python3 -m agent_harness thesis AAPL MSFT --no-backtest

# Machine-readable output for downstream agents, saved by default
python3 -m agent_harness thesis AAPL MSFT --json

# Replay the last saved packet without executing engines
python3 -m agent_harness replay .agent-harness/runs/latest.json

# Production checks over a saved packet
python3 -m agent_harness eval .agent-harness/runs/latest.json

# Realized outcome scoring over Date,Close price CSVs
python3 -m agent_harness outcome .agent-harness/runs/latest.json

# Query the append-only provenance ledger
python3 -m agent_harness ledger list
python3 -m agent_harness ledger show <run_id>
python3 -m agent_harness ledger report
python3 -m agent_harness ledger outcomes
python3 -m agent_harness ledger trust
python3 -m agent_harness ledger promote

# Apply an explicit trust policy when auditing or promoting
python3 -m agent_harness ledger trust --trust-policy docs/trust-policy.example.json
python3 -m agent_harness ledger report --trust-policy docs/trust-policy.example.json
```

By default, the harness assumes sibling repos live beside this repo under
`/Users/p/code/github/pdj555`. Override that with:

```bash
export AGENT_HARNESS_NAMESPACE_ROOT=/path/to/pdj555
```

## Current Integrations

| repo | status | harness role |
| --- | --- | --- |
| `monte-carlo` | executable | Simulation, VaR, drawdown, allocation, cash-buffer gate, repo fingerprint. |
| `stock-sentiment-analysis` | configured | Short-half-life catalyst overlay when `OPENAI_API_KEY` is set. |
| `energy-market-visualization` | discovered | Scarcity, congestion, and volatility playground for non-equity market physics. |
| `research-run-platform` | remote/missing locally | Provenance ledger for hypotheses, runs, and rejection reasons. |

## Run Packets

Every `thesis` run saves a packet to `.agent-harness/runs/<run_id>.json`,
updates `.agent-harness/runs/latest.json`, and ingests the packet into
`.agent-harness/ledger` by default.

The packet contains:

- schema version and content digest
- run id and UTC timestamp
- invocation and input parameters
- risk controls
- adapter readiness
- sibling repo SHA, branch, dirty status, and bounded `git status --porcelain` lines
- simulation/backtest command, duration, diagnostics, summary, and normalized payload
- deterministic stress-test margins
- ranked implementation loops

This turns the harness from a CLI demo into a replayable decision artifact.

## Realized Outcomes

Simulation is only useful if it is closed by observed returns. `outcome` scores a
saved packet over a price window and writes
`.agent-harness/outcomes/<run>_<start>_<end>_<digest>.json` plus
`.agent-harness/outcomes/latest.json`. Saved outcomes are also ingested into the
ledger by default as compact rows in `.agent-harness/ledger/outcomes.jsonl`.

```bash
python3 -m agent_harness outcome .agent-harness/runs/latest.json
python3 -m agent_harness outcome .agent-harness/runs/latest.json \
  --price-dir /path/to/csvs \
  --start-date 2024-01-02 \
  --end-date 2024-01-15
```

The outcome artifact records primary-pick hit rate, allocation return, equal-weight
return, cash return, excess return, realized max drawdown, forecast error, and a
PASS/FAIL scorecard. `ledger outcomes` aggregates realized results across the
ledger: hit rate, beat-cash rate, beat-equal-weight rate, excess-return stats,
forecast-error calibration, realized drawdown, and attribution averages. Outcome
thresholds can block promotion on low ok rate, weak excess return, high average
absolute forecast error, or excessive realized drawdown. Each outcome also
records price-source hashes, rejects duplicate price dates or unbalanced
portfolio weights, and attributes performance by per-position contribution,
active contribution versus equal weight, cash return contribution, cash drag,
top positive active contributor, weakest active contributor, and largest active
drag.

## Provenance Ledger

The ledger is append-only and idempotent by `run_id` plus content digest. It
stores:

- `runs.jsonl`: compact queryable event log
- `index.json`: run-id index
- `latest.json`: latest compact ledger entry
- `packets/<run_id>.json`: immutable packet copy
- `outcomes.jsonl`: compact realized-outcome event log
- `latest_outcome.json`: latest compact outcome entry
- `outcomes/<run_id>_<start>_<end>_<digest>.json`: immutable realized-outcome copy

Commands:

```bash
python3 -m agent_harness ledger ingest .agent-harness/runs/latest.json
python3 -m agent_harness ledger list --limit 5
python3 -m agent_harness ledger show <run_id>
python3 -m agent_harness ledger show <run_id> --packet
python3 -m agent_harness ledger report --min-runs 3
python3 -m agent_harness ledger outcomes --min-outcomes 1
python3 -m agent_harness ledger outcomes --min-outcomes 1 \
  --min-ok-rate 0.8 \
  --min-excess-cash 0.0 \
  --max-forecast-error 0.10 \
  --max-drawdown 0.05
python3 -m agent_harness ledger trust
python3 -m agent_harness ledger trust <run_id>
python3 -m agent_harness ledger trust --trust-policy docs/trust-policy.example.json
python3 -m agent_harness ledger report --trust-policy docs/trust-policy.example.json
python3 -m agent_harness ledger report --min-outcomes 1
python3 -m agent_harness ledger report --min-outcomes 1 --max-outcome-forecast-error 0.10
python3 -m agent_harness ledger promote --min-runs 3
python3 -m agent_harness ledger promote --min-outcomes 1
python3 -m agent_harness ledger promote --min-outcomes 1 --max-outcome-drawdown 0.05
```

The report aggregates eval pass rate, engine pass rate, primary-pick stability,
backtest excess return, stress margins, drawdown, dirty-repo frequency, and
promotion blockers. `ledger trust` shows the exact branch, SHA, dirty state, and
status lines that must be cleaned or intentionally acknowledged before
promotion. A trust policy can explicitly allow narrow dirty paths, such as
documentation-only runbook edits, while still blocking capital-engine code,
tests, dependency, and config changes. Without a policy, every dirty change is
promotion-blocking.
`ledger promote` writes every attempt to `.agent-harness/promotions/attempts`.
It only publishes `.agent-harness/promotions/canonical.json` when the report is
ready.

## Financial Physics Model

The harness ranks work by risk-adjusted implementation priority:

- Edge must be multiplied by confidence, not vibes.
- Tail loss gets a hard penalty before effort is considered.
- Short-half-life signals decay quickly unless refreshed.
- Capital allocation starts with simulation and guardrails; narrative is an
  overlay, never the base layer.
- Walk-forward validation adjusts confidence and becomes ledger evidence.
- Stress tests haircut expected return, backtest edge, VaR, drawdown, and
  liquidity before promotion can succeed.
- Every high-value agent action should leave an auditable run artifact.

## Ranked Implementation Recommendations

1. **Promote `monte-carlo` to the hard capital gate.** Keep its simulation,
   ranking, allocation, and guardrail outputs as the entry point for every opportunity workflow.
   Proof: current smoke produces a deterministic AAPL/MSFT stance from bundled
   fixtures plus walk-forward excess return versus cash and equal weight.
2. **Bridge the local ledger to `research-run-platform`.** Persist every thesis,
   input, engine version, output, rejection, and follow-up in the org-level run
   explorer.
   Proof: `agent-harness thesis` now creates and ingests replayable packet JSON by default.
3. **Use realized outcomes as the feedback loop.** Score packet recommendations
   against observed price windows before trusting repeated promotion.
   Proof: `agent-harness outcome .agent-harness/runs/latest.json` now records
   allocation return, excess return, drawdown, hit/miss, forecast error, and
   price-source hashes;
   `agent-harness ledger outcomes` aggregates that into promotion-grade metrics
   with explicit calibration, drawdown, and attribution evidence.
4. **Make ledger promotion the operating gate.** Use `agent-harness ledger report`
   to block canonical decisions when repos are dirty, backtests are missing, or
   stress tests fail.
   Proof: `agent-harness ledger trust` now shows the exact dirty branches and
   status lines, and `agent-harness ledger promote` refuses to publish
   `canonical.json` while those blockers remain.
5. **Activate sentiment as a catalyst overlay.** When credentials are present,
   run `stock-sentiment-analysis` after risk gating and discount it aggressively
   by half-life.
   Proof: require JSON sentiment to alter ranking only within explicit bounds.
6. **Use energy markets as the second domain.** Port the same loop from equities
   into power telemetry to test scarcity, congestion, and mean-reversion logic.
   Proof: one adapter can rank markets without touching the equity path.
7. **Add adapter contract tests for every sibling engine.** Keep each integration
   honest with fake-engine tests plus one opt-in local smoke.
   Proof: CI can run without sibling repos; local operator smoke can run with them.
8. **Emit run packages, not just text.** Store normalized engine payloads,
   diagnostics, commands, repo SHAs, and timestamps.
   Proof: `--json` payload round-trips into a saved run file.
9. **Separate research recommendations from execution authority.** This harness
   should never place orders; it should produce bounded, auditable decision
   packets.
   Proof: no broker dependency in the package.
10. **Add context compaction for agents.** Give downstream agents only the thesis,
   adapter readiness, engine payload, and ranked next actions.
   Proof: a compact JSON schema stays under a fixed token budget.

## Verification

```bash
python3 -m pytest -q
python3 -m agent_harness thesis AAPL MSFT --days 30 --scenarios 100 --seed 42
python3 -m agent_harness replay .agent-harness/runs/latest.json
python3 -m agent_harness eval .agent-harness/runs/latest.json
python3 -m agent_harness outcome .agent-harness/runs/latest.json
python3 -m agent_harness ledger report
python3 -m agent_harness ledger outcomes
python3 -m agent_harness ledger trust
python3 -m agent_harness ledger promote
```
