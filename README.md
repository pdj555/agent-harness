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
python3 -m agent_harness thesis --days 30 --scenarios 100 --seed 42

# Disable walk-forward validation only when you are intentionally doing a fast smoke
python3 -m agent_harness thesis --no-backtest

# Keep raw Monte Carlo allocation without deterministic regime repair
python3 -m agent_harness thesis --no-allocation-repair

# Add a bounded catalyst overlay from stock-sentiment-analysis for the primary pick
python3 -m agent_harness thesis --sentiment

# Machine-readable output for downstream agents, saved by default
python3 -m agent_harness thesis --json

# Replay the last saved packet without executing engines
python3 -m agent_harness replay .agent-harness/runs/latest.json

# Production checks over a saved packet
python3 -m agent_harness eval .agent-harness/runs/latest.json

# Realized outcome scoring over Date,Close price CSVs
python3 -m agent_harness outcome .agent-harness/runs/latest.json

# Audit offline fixture breadth, sectors, hashes, and correlations
python3 -m agent_harness fixtures audit

# Refresh offline fixtures from an external historical source, then audit them
python3 -m agent_harness fixtures refresh --start-date 2024-01-02 --end-date 2024-01-15

# Deterministic replay across synthetic market regimes
python3 -m agent_harness regime-replay .agent-harness/runs/latest.json

# Query the append-only provenance ledger
python3 -m agent_harness ledger list
python3 -m agent_harness ledger show <run_id>
python3 -m agent_harness ledger report
python3 -m agent_harness ledger outcomes
python3 -m agent_harness ledger regimes
python3 -m agent_harness ledger backfill-outcomes --rolling
python3 -m agent_harness ledger calibrate-outcomes
python3 -m agent_harness ledger trust
python3 -m agent_harness ledger sync research-run-platform
python3 -m agent_harness ledger import research-run-platform .agent-harness/platform_exports/<export_id>
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

The default offline thesis universe is `AAPL`, `MSFT`, `GOOGL`, `JPM`, and
`XOM`; that gives allocation repair enough non-primary alternatives and sector
breadth to satisfy portfolio-wide position caps in the live smoke path.

## Current Integrations

| repo | status | harness role |
| --- | --- | --- |
| `monte-carlo` | executable | Simulation, VaR, drawdown, allocation, cash-buffer gate, repo fingerprint. |
| `stock-sentiment-analysis` | executable when keyed | Short-half-life catalyst overlay when `OPENAI_API_KEY` or `OLLAMA_API_KEY` is set. |
| `energy-market-visualization` | discovered | Scarcity, congestion, and volatility playground for non-equity market physics. |
| `research-run-platform` | importable receiver | SQLite-backed provenance ledger and evidence API for hypotheses, runs, outcomes, regime replays, promotions, and rejection reasons. |

## Fixture Universe

`fixtures refresh` rebuilds the local `Date,Close` CSV directory from a
historical data source, then `fixtures audit` turns that directory into a
measurable offline market universe. The refresh path supports Stooq's daily CSV
download endpoint plus `csv-dir` for trusted vendor drops or previously
downloaded files. If Stooq serves a browser-verification HTML challenge instead
of CSV, refresh fails closed and records that blocker. The audit records
per-ticker hashes, row counts, aligned dates, sector tags, return stats, and a
pairwise return-correlation matrix, then fails closed when the universe is too
narrow, too short, sector-poor, or too highly correlated.

```bash
python3 -m agent_harness fixtures refresh \
  --source stooq \
  --start-date 2024-01-02 \
  --end-date 2024-01-15
python3 -m agent_harness fixtures refresh \
  --source csv-dir \
  --source-dir /path/to/vendor-csvs
python3 -m agent_harness fixtures refresh --no-verify-tls  # local CA fallback only
python3 -m agent_harness fixtures audit
python3 -m agent_harness fixtures audit \
  --price-dir /path/to/csvs \
  --min-tickers 5 \
  --min-sectors 3 \
  --max-pairwise-correlation 0.98
```

Saved reports live under `.agent-harness/fixtures` and include stable
`fixture_refresh_digest` and `fixture_universe_digest` values for downstream
importers and promotion evidence.

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
- default-on regime-repaired allocation metadata with the raw engine stance preserved
- optional sentiment command, duration, diagnostics, summary, and normalized payload
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
forecast-error calibration, realized drawdown, attribution averages, and
sentiment-overlay alignment. Outcome thresholds can block promotion on low ok
rate, weak excess return, high average absolute forecast error, or excessive
realized drawdown. Each outcome also records price-source hashes, rejects
duplicate price dates or unbalanced portfolio weights, attributes performance by
per-position contribution, active contribution versus equal weight, cash return
contribution, cash drag, top positive active contributor, weakest active
contributor, and largest active drag, and scores whether any captured sentiment
signal aligned with the realized primary-pick return.

Use `ledger backfill-outcomes` to turn saved ledger packet copies into realized
evidence in bulk. With `--rolling`, the harness sweeps every available common
price window for each packet, writes only new outcome artifacts, and skips
existing outcome digests idempotently.

```bash
python3 -m agent_harness ledger backfill-outcomes \
  --price-dir /path/to/csvs \
  --horizon-rows 5 \
  --rolling
python3 -m agent_harness ledger backfill-outcomes --rolling --dry-run
```

Use `ledger calibrate-outcomes` after backfill to convert observed outcomes into
copyable promotion thresholds. It reports realized distributions and recommends
minimum outcome count, ok rate, excess-return gates, maximum average forecast
error, maximum drawdown, and sentiment gates when enough directional sentiment
windows exist. Add `--write-gates` once calibration is ready to atomically write
a validated gates file.

```bash
python3 -m agent_harness ledger calibrate-outcomes \
  --min-sample 20 \
  --write-gates \
  --gates-output agent-harness.gates.json
```

`agent-harness.gates.json` is the tracked promotion-gates file. `ledger report`,
`ledger outcomes`, and `ledger promote` load it automatically when present and
use it as production defaults. Explicit CLI threshold flags override the file,
and `ledger --no-gates ...` disables configured defaults for inspection/debugging.

## Regime Replay

`regime-replay` turns one saved packet into deterministic synthetic price
fixtures, then runs the same outcome scorer used for observed prices. It writes
CSV fixtures plus a replay report under `.agent-harness/regimes`, ingests a
compact replay row into the ledger by default, and exits non-zero when a packet
is fragile.

```bash
python3 -m agent_harness regime-replay .agent-harness/runs/latest.json
python3 -m agent_harness regime-replay .agent-harness/runs/latest.json \
  --output-dir .agent-harness/regimes \
  --max-drawdown 0.08
python3 -m agent_harness ledger regimes \
  --min-regime-replays 1 \
  --require-latest-regime-replay \
  --require-regime-ok
```

The built-in regimes cover primary-pick trend, primary-pick reversal, shock
recovery, and cash-drag rally. The report records allocation return, excess
return versus cash and equal weight, realized drawdown, attribution drivers,
sentiment alignment fields, fragile regimes, worst drawdown, worst excess
return, and the primary-reversal loss. This catches concentrated bets, cash drag,
and drawdown fragility before a run can earn trust from observed outcomes.
`ledger report` and `ledger promote` can require replay evidence through
`agent-harness.gates.json` or explicit regime flags.

`thesis` applies deterministic allocation repair by default. It searches
candidate allocations against the same regime replay criteria, keeps the raw
Monte Carlo stance in `action_plan.raw_headline`, and updates the packet
allocation only when replay fragility improves. Disable this with
`--no-allocation-repair` when you need a raw sibling-engine artifact. Repair does
not silently waive concentration controls: when there are at least two
non-primary alternatives, candidate allocations must respect the configured
max-position weight across every allocation row. With a narrower universe, the
packet records `allocation_repair.max_position_policy.reason =
insufficient_non_primary_alternatives`, and packet eval blocks production use if
any repaired row still exceeds the position cap.

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
- `regimes.jsonl`: compact deterministic-regime replay event log
- `latest_regime.json`: latest compact regime replay entry
- `regimes/<run_id>_regime_replay_<digest>.json`: immutable regime replay copy
- `platform_exports/<export_id>/manifest.json`: research-run-platform sync manifest
- `platform_exports/<export_id>/runs.jsonl`: importable compact run rows
- `platform_exports/<export_id>/outcomes.jsonl`: importable compact outcome rows
- `platform_exports/<export_id>/regimes.jsonl`: importable compact regime replay rows
- `platform_exports/<export_id>/promotions.jsonl`: promotion attempt rows for exported runs
- `platform_exports/<export_id>/artifacts/...`: bundle-local packet, outcome, and regime copies
- `platform_exports/<export_id>/duckdb_import.sql`: local staging import helper

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
  --max-drawdown 0.05 \
  --min-sentiment-outcomes 3 \
  --min-sentiment-hit-rate 0.55 \
  --min-sentiment-alignment 0.0
python3 -m agent_harness ledger regimes --min-regime-replays 1 \
  --require-latest-regime-replay \
  --require-regime-ok \
  --max-regime-fragile-count 0 \
  --max-regime-drawdown 0.08 \
  --min-regime-excess-cash 0.0
python3 -m agent_harness ledger trust
python3 -m agent_harness ledger trust <run_id>
python3 -m agent_harness ledger trust --trust-policy docs/trust-policy.example.json
python3 -m agent_harness ledger report --trust-policy docs/trust-policy.example.json
python3 -m agent_harness ledger report --min-outcomes 1
python3 -m agent_harness ledger report --min-regime-replays 1 --require-latest-regime-replay
python3 -m agent_harness ledger report --min-outcomes 1 --max-outcome-forecast-error 0.10
python3 -m agent_harness ledger backfill-outcomes --rolling --horizon-rows 5
python3 -m agent_harness ledger calibrate-outcomes --min-sample 20
python3 -m agent_harness fixtures refresh --start-date 2024-01-02 --end-date 2024-01-15
python3 -m agent_harness fixtures audit
python3 -m agent_harness ledger sync research-run-platform
python3 -m agent_harness ledger import research-run-platform .agent-harness/platform_exports/<export_id>
python3 -m agent_harness ledger promote --min-runs 3
python3 -m agent_harness ledger promote --min-outcomes 1
python3 -m agent_harness ledger promote --min-regime-replays 1 --require-latest-regime-replay
python3 -m agent_harness ledger promote --min-outcomes 1 --max-outcome-drawdown 0.05
python3 -m agent_harness ledger promote --min-outcomes 1 \
  --min-outcome-sentiment-outcomes 3 \
  --min-outcome-sentiment-hit-rate 0.55
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
`ledger report --promotions-dir .agent-harness/promotions` ranks persisted
promotion blockers by category, all-time frequency, recent-window frequency,
latest affected run, and share of attempts. It also surfaces the recent
promotion rate, so operators can tell whether remediation is improving the
gate or just moving failures between subsystems.
`agent-harness.trust.json` is the tracked production trust policy and is loaded
automatically by `ledger trust`, `ledger report`, and `ledger promote` when
present in the current working directory. It permits only narrow
operator-facing docs/example edits and explicitly blocks dirty harness,
Monte Carlo, sentiment, run-platform, test, dependency, CI, and trust-policy
changes from promotion. Allow rules must be named, repo-scoped, path-scoped,
status-scoped, justified, and time-limited.
Sentiment outcome thresholds are optional until enough keyed sentiment runs
exist. Once enabled, they block promotion when the overlay lacks enough realized
directional windows, misses too often, or has negative confidence-weighted
return alignment.
`agent-harness.gates.json` is also tracked and loaded automatically by
promotion-readiness commands. The current file is intentionally fail-closed:
it requires 20 realized outcomes before promotion readiness can pass, while
carrying provisional calibration-derived thresholds for forecast error, drawdown,
ok rate, excess return, and deterministic replay evidence for the latest run.
`ledger sync research-run-platform` writes an immutable import bundle under
`.agent-harness/platform_exports`. The bundle contains a manifest, JSONL tables,
artifact references, bundle-local packet/outcome/regime copies, all promotion
attempts for exported runs, copy hashes, and a DuckDB staging import helper.
Programmatic exports can pass `discover_promotions=True` to discover a standard
sibling `promotions` directory beside the supplied ledger directory, so receiver
smoke tests can carry promotion history without hard-coding another path.
Each promotion row includes its own `promotion_attempt_report` with blocker and
category rollups, so receivers can expose remediation evidence directly from
`promotions.jsonl`.
Sync validates the manifest digests, JSONL row counts, per-row platform digests,
duplicate ids, and artifact byte/hash matches before returning success.
`ledger import research-run-platform <export-dir>` is the receiving-side
contract: it reruns validation, loads artifact payloads, checks
run/outcome/regime/promotion joins, rejects stale promotions, and stages compact
per-run records for the external platform importer. Those staged records include
promotion-attempt counts, latest attempt metadata, blocker categories, blocker
frequency, and recent promotion rate so the platform can route remediation
without reparsing raw promotion artifacts.
The sibling receiver persists that validated bundle:

```bash
research-run-platform ingest .agent-harness/platform_exports/<export_id>
research-run-platform stats
research-run-platform show <run_id>
research-run-platform evidence <run_id>
research-run-platform serve --port 8765
```

The evidence endpoint includes latest packet/outcome/regime summaries plus
aggregate realized excess-return, forecast-error, drawdown, and regime fragility
statistics for that run.

Set `AGENT_HARNESS_PLATFORM_SIGNING_KEY` or pass `--signing-key-file` to
`ledger sync` to write `manifest.signature.json` beside `manifest.json`. Use
`ledger import --require-signature --signing-key-file <key>` or
`ledger verify-production --require-platform-signature --platform-signing-key-file <key>`
to reject unsigned or tampered bundles before the receiver trusts them.

## Financial Physics Model

The harness ranks work by risk-adjusted implementation priority:

- Edge must be multiplied by confidence, not vibes.
- Tail loss gets a hard penalty before effort is considered.
- Short-half-life signals decay quickly unless refreshed.
- Capital allocation starts with simulation and guardrails; narrative is an
  overlay, never the base layer.
- Allocation repair can override concentration and cash drag only through a
  replay-scored packet mutation that preserves the raw Monte Carlo stance.
- Portfolio-wide position caps apply to every allocation row, not just the
  primary pick; narrow universes must be expanded before promotion.
- Sentiment can only apply a bounded confidence adjustment; it cannot bypass
  simulation, backtest, stress, trust, or outcome gates.
- Walk-forward validation adjusts confidence and becomes ledger evidence.
- Stress tests haircut expected return, backtest edge, VaR, drawdown, and
  liquidity before promotion can succeed.
- Deterministic regime replay must expose primary reversal, shock drawdown, and
  cash-drag behavior before realized outcomes are trusted.
- Every high-value agent action should leave an auditable run artifact.

## Ranked Implementation Recommendations

1. **Promote `monte-carlo` to the hard capital gate.** Keep its simulation,
   ranking, allocation, and guardrail outputs as the entry point for every opportunity workflow.
   Proof: current smoke produces a deterministic AAPL/MSFT/GOOGL/JPM/XOM stance
   from bundled fixtures plus walk-forward excess return versus cash and equal
   weight, then applies deterministic allocation repair before packet
   persistence.
2. **Bridge the local ledger to `research-run-platform`.** Persist every thesis,
   input, engine version, output, rejection, and follow-up in the org-level run
   explorer.
   Proof: `agent-harness ledger sync research-run-platform` now emits importable
   run, outcome, regime, and promotion JSONL plus bundle-local artifact copies
   and a strict validation report. `agent-harness ledger import
   research-run-platform <export-dir>` validates the receiving-side semantic
   contract before an external service trusts the bundle. The sibling
   `research-run-platform ingest <export-dir>` command persists the validated
   bundle into SQLite and exposes list/show/stats plus decision-grade evidence
   through `research-run-platform evidence <run_id>` and
   `/runs/<run_id>/evidence`. The receiver repo now has CI that checks out
   `agent-harness`, builds the synthetic production fixture, ingests the export,
   and validates CLI plus HTTP evidence for `run_ci_3`.
3. **Refresh and audit the fixture universe before trusting offline smoke.** Treat local
   price CSVs as market data with provenance, sector breadth, and correlation
   risk, not anonymous test files.
   Proof: `agent-harness fixtures refresh` rebuilds `Date,Close` CSVs from
   Stooq with source metadata, and `agent-harness fixtures audit` records file
   hashes, date coverage, sector tags, return stats, and pairwise return
   correlations, then fails closed when the offline universe is narrow, short,
   sector-poor, or too duplicated.
4. **Use realized outcomes as the feedback loop.** Score packet recommendations
   against observed price windows before trusting repeated promotion.
   Proof: `agent-harness outcome .agent-harness/runs/latest.json` now records
   allocation return, excess return, drawdown, hit/miss, forecast error, and
   price-source hashes;
   `agent-harness ledger backfill-outcomes --rolling` can bulk-create
   idempotent realized measurements from saved ledger packet copies, and
   `agent-harness ledger calibrate-outcomes --write-gates` converts those
   measurements into a validated gates file. `agent-harness ledger outcomes`
   aggregates the same evidence into promotion-grade metrics with explicit
   calibration, drawdown, and attribution evidence.
5. **Use deterministic regime replay as a hard promotion gate.** Run every
   candidate packet through synthetic primary-trend, primary-reversal,
   shock-recovery, and cash-drag-rally fixtures before trusting the ledger
   evidence.
   Proof: `agent-harness regime-replay .agent-harness/runs/latest.json` writes
   generated `Date,Close` CSVs, scores them through the realized-outcome engine,
   emits a replay artifact, ingests a ledger row, exports it to
   `research-run-platform`, and `ledger report/promote` apply replay gates from
   `agent-harness.gates.json`.
6. **Keep allocation repair in the thesis path.** Every new packet should prefer
   replay-survivable allocations over raw single-name concentration when the
   synthetic regimes prove the raw output is brittle.
   Proof: `agent-harness thesis` now records
   `allocation_repair.applied`, selected weights, original replay summary, and
   selected replay summary in the Monte Carlo payload. `agent-harness eval`
   checks every allocation row against max-position and cash-buffer controls.
7. **Make ledger promotion the operating gate.** Use `agent-harness ledger report`
   to block canonical decisions when repos are dirty, backtests are missing, or
   stress tests fail.
   Proof: `agent-harness ledger trust` now shows the exact dirty branches and
   status lines, the tracked `agent-harness.trust.json` policy classifies
   dirty changes, the tracked `agent-harness.gates.json` policy applies
   calibrated outcome defaults, and `agent-harness ledger promote` refuses to
   publish `canonical.json` while blocking changes remain.
8. **Activate sentiment as a catalyst overlay.** When credentials are present,
   run `stock-sentiment-analysis` after risk gating and discount it aggressively
   by half-life.
   Proof: `agent-harness thesis --sentiment` now runs the sibling JSON CLI for
   the Monte Carlo primary pick, stores the engine run, and caps the Monte Carlo
   confidence adjustment at a small bounded overlay. `agent-harness outcome`
   then scores directional hit rate and confidence-weighted return alignment for
   that overlay.
9. **Use energy markets as the second domain.** Port the same loop from equities
   into power telemetry to test scarcity, congestion, and mean-reversion logic.
   Proof: one adapter can rank markets without touching the equity path.
10. **Add adapter contract tests for every sibling engine.** Keep each integration
   honest with fake-engine tests plus one opt-in local smoke.
   Proof: CI can run without sibling repos; local operator smoke can run with them.
11. **Emit run packages, not just text.** Store normalized engine payloads,
   diagnostics, commands, repo SHAs, and timestamps.
   Proof: `--json` payload round-trips into a saved run file.
12. **Separate research recommendations from execution authority.** This harness
   should never place orders; it should produce bounded, auditable decision
   packets.
   Proof: no broker dependency in the package.

## Verification

```bash
python3 -m pytest -q
python3 -m agent_harness thesis --days 30 --scenarios 100 --seed 42
python3 -m agent_harness replay .agent-harness/runs/latest.json
python3 -m agent_harness eval .agent-harness/runs/latest.json
python3 -m agent_harness outcome .agent-harness/runs/latest.json
python3 -m agent_harness regime-replay .agent-harness/runs/latest.json
python3 -m agent_harness ledger report
python3 -m agent_harness ledger outcomes
python3 -m agent_harness ledger regimes
python3 -m agent_harness ledger trust
python3 -m agent_harness ledger verify-production
python3 -m agent_harness ledger sync research-run-platform
python3 -m agent_harness ledger import research-run-platform .agent-harness/platform_exports/<export_id>
python3 -m agent_harness ledger promote
```

CI also runs a synthetic verifier smoke:

```bash
python3 tools/build_ci_production_fixture.py --root .ci-production
AGENT_HARNESS_PLATFORM_SIGNING_KEY=ci-production-platform-signing-key \
  agent-harness --namespace-root .ci-production \
  ledger --ledger-dir .ci-production/ledger \
  --gates .ci-production/gates.json \
  verify-production \
  --price-dir .ci-production/prices \
  --platform-output-dir .ci-production/platform_exports \
  --promotions-dir .ci-production/promotions \
  --require-platform-signature
```

That fixture is only a clean-checkout contract test for the verifier, ledger,
gates, signed platform import/export machinery, and receiving-side validation.
It includes a blocked-then-promoted synthetic promotion history for the latest
run so CI proves blocker categories and recent promotion state traverse the
signed platform bundle. Live promotion still depends on the real
`.agent-harness` ledger, real fixture audit, observed outcomes, regime replay,
and trust-policy gates.

To verify the sibling receiver consumes promotion-bearing exports end to end:

```bash
python3 tools/verify_research_run_platform_receiver.py --json
```

The smoke builds the synthetic fixture, exports with `discover_promotions=True`,
ingests through `research-run-platform`, then asserts the persisted run record
contains promotion-attempt analytics and the evidence payload exposes the latest
promoted attempt.
Use `--require-evidence-promotion-attempts` after applying
`tools/patches/research-run-platform-promotion-evidence.patch` to the sibling
receiver; that stricter mode fails until `evidence <run_id>` exposes
`promotion.attempts` directly.
