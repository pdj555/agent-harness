# Production Operating Model

`agent-harness` is the audit and orchestration layer for capital research
engines in the `pdj555` namespace. It does not place orders. It produces
bounded decision packets that can be replayed, evaluated, ingested into an
append-only ledger, and passed to other agents.

## Non-Negotiable Contracts

1. Risk gate first. `monte-carlo` must remain the first capital gate for any
   thesis that touches allocation.
2. Walk-forward validation. A capital thesis should include backtest evidence
   unless the operator explicitly disables it.
3. Stress survival. A capital thesis should survive deterministic stress
   haircuts before promotion.
4. Durable packets. A thesis run is not complete until it has a saved packet.
5. Repo fingerprints. Every adapter must expose repo SHA, branch, dirty status,
   bounded status lines, count, and truncation state.
6. Replayability. Operators must be able to inspect a packet without re-running
   engines.
7. Catalyst overlays. Sentiment is opt-in and can only make a bounded adjustment
   to Monte Carlo confidence.
8. Ledger ingest. Packets must enter the append-only ledger unless explicitly
   disabled by an operator.
9. Ledger report. Promotion readiness must be checked across the ledger, not just
   one packet.
10. Promotion gate. Canonical artifacts must be written only by `ledger promote`.
11. Evaluation. Packets must pass schema, risk-order, simulation, backtest,
    stress, position-cap, cash-buffer, and fingerprint checks before they are
    treated as production-ready.

## Packet Lifecycle

1. `agent-harness thesis ...` executes simulation plus walk-forward validation
   and writes a packet.
2. `agent-harness thesis` applies deterministic allocation repair by default
   when regime replay proves the raw Monte Carlo allocation is brittle.
3. `.agent-harness/runs/latest.json` points to the most recent packet copy.
4. `agent-harness thesis` ingests the packet into `.agent-harness/ledger` by default.
5. `agent-harness fixtures refresh` rebuilds offline price fixtures from an
   external historical CSV source.
6. `agent-harness fixtures audit` validates offline fixture breadth, sector
   tags, hashes, aligned dates, and pairwise return correlations.
7. `agent-harness replay <packet>` renders the saved decision without executing
   engines.
8. `agent-harness thesis --sentiment` optionally runs `stock-sentiment-analysis`
   for the Monte Carlo primary pick and records it as an overlay.
9. `agent-harness eval <packet>` checks whether the packet is usable.
10. `agent-harness outcome <packet>` scores realized P&L and drawdown over a
   Date,Close price window.
11. `agent-harness regime-replay <packet>` generates deterministic synthetic
   price fixtures and scores primary trend, primary reversal, shock recovery,
   and cash-drag rally behavior, then ingests a compact replay row into the
   ledger by default.
12. `agent-harness ledger backfill-outcomes --rolling` bulk-scores saved ledger
   packets across available price windows and skips existing outcome digests.
13. `agent-harness ledger calibrate-outcomes` recommends production outcome
   thresholds from realized distributions.
14. `agent-harness.gates.json` supplies default promotion thresholds to
   `ledger outcomes`, `ledger report`, and `ledger promote`.
15. `agent-harness ledger list/show` exposes the append-only query surface.
16. `agent-harness ledger outcomes` aggregates realized outcome performance.
17. `agent-harness ledger regimes` aggregates deterministic replay performance.
18. `agent-harness ledger report` aggregates performance and promotion blockers.
19. `agent-harness ledger trust` shows branch, SHA, dirty state, status lines,
   and trust-policy classification for the latest or selected run.
20. `agent-harness ledger promote` writes a promotion attempt and publishes
   `canonical.json` only if promotion is ready.
21. `agent-harness ledger sync research-run-platform` writes an import bundle for
   the org-level run explorer.
22. `agent-harness ledger import research-run-platform <export-dir>` validates
   the receiving-side import contract and stages per-run records.
23. `research-run-platform ingest <export-dir>` persists the validated bundle
   into SQLite.
24. `research-run-platform evidence <run_id>` and
   `GET /runs/<run_id>/evidence` expose the run's primary pick, backtest,
   stress result, realized outcome, deterministic regime replay, promotion
   status, blockers, aggregate realized edge, forecast-error, drawdown, and
   regime fragility as one decision-grade envelope.

## Ledger Files

- `.agent-harness/ledger/runs.jsonl`: append-only compact events.
- `.agent-harness/ledger/index.json`: run id to ledger entry index.
- `.agent-harness/ledger/latest.json`: latest compact ledger entry.
- `.agent-harness/ledger/packets/<run_id>.json`: immutable packet copy.
- `.agent-harness/ledger/outcomes.jsonl`: append-only realized-outcome events.
- `.agent-harness/ledger/latest_outcome.json`: latest compact outcome entry.
- `.agent-harness/ledger/outcomes/<run_id>_<start>_<end>_<digest>.json`: immutable outcome copy.
- `.agent-harness/ledger/regimes.jsonl`: append-only deterministic-regime replay events.
- `.agent-harness/ledger/latest_regime.json`: latest compact regime replay entry.
- `.agent-harness/ledger/regimes/<run_id>_regime_replay_<digest>.json`: immutable replay copy.
- `.agent-harness/regimes/<run_id>/prices/<regime>/<ticker>.csv`: generated deterministic regime prices.
- `.agent-harness/regimes/<run_id>_regime_replay_<digest>.json`: immutable regime replay report.
- `.agent-harness/regimes/latest.json`: latest regime replay report.
- `.agent-harness/platform_exports/<export_id>/manifest.json`: external platform sync manifest.
- `.agent-harness/platform_exports/<export_id>/runs.jsonl`: compact run export rows.
- `.agent-harness/platform_exports/<export_id>/outcomes.jsonl`: compact outcome export rows.
- `.agent-harness/platform_exports/<export_id>/regimes.jsonl`: compact regime replay export rows.
- `.agent-harness/platform_exports/<export_id>/promotions.jsonl`: compact promotion attempt rows for exported runs.
- `.agent-harness/platform_exports/<export_id>/artifacts/...`: bundle-local packet, outcome, and regime copies.
- `.agent-harness/platform_exports/<export_id>/duckdb_import.sql`: local import helper.

Ledger ingest is idempotent when the same `run_id` and digest are seen again.
If a run id is reused for different content, ingest fails.

Platform sync bundles are immutable handoff artifacts. Each bundle records row
counts, latest run/outcome/regime/promotion identifiers, JSONL file digests,
artifact paths, bundle-local packet/outcome/regime copies, all promotion
attempts for exported runs, and copy hashes. `ledger sync
research-run-platform` validates the manifest digests, JSONL counts, row-level
platform digests, duplicate ids, and artifact byte/hash matches before it
returns success. That gives `research-run-platform` enough evidence to reject
partial, tampered, or mismatched imports.
Programmatic exporters can pass `discover_promotions=True` to discover a sibling
`promotions` directory beside the ledger directory when `promotions_dir` is not
passed, keeping receiver smokes and CLI sync on the same evidence contract
without surprising older callers.
Each `promotions.jsonl` row also carries a `promotion_attempt_report` containing
its blocker and category rollups, so receivers can expose remediation evidence
without reimplementing blocker classification.
`ledger import research-run-platform <export-dir>` is the receiving-side smoke:
it reruns validation, parses the JSONL tables, loads artifact payloads, checks
run/outcome/regime/promotion joins, rejects stale promotion rows whose `run_id`
is absent from the exported runs table, and reports compact staged records with
promotion-attempt counts, latest attempt metadata, blocker categories, blocker
frequency, and recent promotion rate.
The sibling `research-run-platform` receiver persists the validated bundle into
SQLite:

```bash
research-run-platform ingest .agent-harness/platform_exports/<export_id>
research-run-platform stats
research-run-platform show <run_id>
research-run-platform evidence <run_id>
research-run-platform serve --port 8765
```

The receiver's own CI checks out `agent-harness`, builds the synthetic
production fixture, exports it through `ledger sync` contract code, ingests the
bundle, and verifies CLI plus HTTP evidence for `run_ci_3`.

For tamper-evident handoff, set `AGENT_HARNESS_PLATFORM_SIGNING_KEY` or pass
`--signing-key-file` to `ledger sync`; the export writes
`manifest.signature.json` as HMAC-SHA256 over the exact `manifest.json` bytes.
Receiving-side validation can require it with
`ledger import --require-signature --signing-key-file <key>` or
`ledger verify-production --require-platform-signature --platform-signing-key-file <key>`.

## Promotion Readiness

`agent-harness ledger report` checks whether the latest run can be promoted as a
canonical research signal.

Current blockers:

- fewer than the required number of ledger runs
- latest run failed eval
- simulation did not execute
- walk-forward validation did not execute
- latest backtest failed to beat cash
- latest stress tests failed
- latest run has dirty repos
- fewer than the required number of realized outcomes when `--min-outcomes` is set
- latest realized outcome failed its scorecard when outcome gating is enabled
- realized outcomes failed to beat cash or equal weight on average when outcome
  gating is enabled
- realized outcome ok rate, forecast error, or drawdown violates explicit
  threshold flags
- realized sentiment overlays lack enough directional windows, miss the required
  hit rate, or fall below the configured confidence-weighted alignment threshold
- deterministic replay evidence is missing for the latest run, stale, fragile,
  above drawdown tolerance, or below configured worst-excess-return thresholds

When dirty repos block promotion, run `agent-harness ledger trust` to inspect the
exact branch, SHA, dirty count, and bounded status lines captured in the packet.
That keeps remediation tied to the same immutable run artifact that promotion is
evaluating.

Use `--trust-policy <path>` with `ledger trust`, `ledger report`, and
`ledger promote` to override the default policy path. When
`agent-harness.trust.json` exists in the working directory, it is loaded
automatically. Policy rules can allow precise repo/path/status combinations and
can add explicit hard blocks. Without a policy, all dirty changes block
promotion. Keep production policy narrow: documentation and generated local
artifacts can be allowed; capital-engine code, tests, dependency files, config,
trust policy changes, and data fixtures should remain blocking until committed.
Loaded policy files are schema-checked: allow rules must be named, scoped to a
repo, scoped to paths and statuses, justified, and time-limited.

`agent-harness ledger promote` persists each promotion attempt under
`.agent-harness/promotions/attempts`. Blocked attempts update
`.agent-harness/promotions/latest.json` but do not write
`.agent-harness/promotions/canonical.json`.
`agent-harness ledger report --promotions-dir .agent-harness/promotions`
summarizes promotion-attempt memory: attempt counts, promotion rate, latest
attempt, recent-window promotion rate, top blocker categories, and individual
blockers ranked by all-time and recent frequency. Use the category rollup to
route remediation to the owning subsystem, then use the individual blocker row
to confirm the latest affected run.

## Risk Stance

This harness is aggressive about research speed and conservative about capital
authority. Production leverage comes from tighter feedback loops, not from
allowing an agent to place trades.

Current hard controls:

- `max_position_weight`: 60%
- `min_cash_buffer_when_concentrated`: 20%
- `concentration_weight`: 50%
- `execution_authority`: `research_only`
- `requires_human_approval_for_orders`: `true`
- `sentiment_max_monte_carlo_confidence_adjustment`: +4 percentage points
- `sentiment_min_monte_carlo_confidence_adjustment`: -5 percentage points
- `allocation_repair`: enabled by default for thesis runs; disable with
  `--no-allocation-repair` only when capturing raw sibling-engine output
- `allocation_rows_cap_respected`: every allocation row must stay below
  `max_position_weight`
- `allocation_rows_cash_buffer_respected`: any concentrated allocation row must
  be backed by the configured cash buffer

Default offline backtest smoke:

- `lookback`: 3 trading days
- `hold`: 2 trading days
- `rebalance`: 2 trading days
- `scenarios`: 20 per rebalance

Those values are intentionally small because the bundled sibling fixtures only
contain 10 price observations. Larger datasets should use larger validation
windows.

Realized outcome scoring:

- evaluates packet allocation weights plus cash against observed CSV prices
- bulk-evaluates saved ledger packet copies with `ledger backfill-outcomes`
- compares allocation return to equal weight and cash
- records primary-pick hit/miss, forecast error, and realized max drawdown
- records per-ticker price CSV hashes and an aggregate price-source digest
- rejects duplicate price dates, negative weights, and unbalanced unlevered portfolios
- attributes return and active excess by position weight, omitted tickers, cash
  return contribution, and cash drag
- scores any captured sentiment overlay against realized primary-pick direction,
  signed return, and confidence-weighted alignment
- persists immutable outcome artifacts and compact ledger rows
- aggregates hit rate, beat-cash rate, beat-equal-weight rate, excess returns,
  forecast-error calibration, drawdown, attribution, and sentiment alignment
  with `ledger outcomes`
- supports promotion thresholds for minimum ok rate, minimum excess return,
  maximum average absolute forecast error, and maximum realized drawdown
- supports optional promotion thresholds for minimum realized sentiment
  directional outcomes, directional hit rate, and confidence-weighted alignment
- should be run once the thesis horizon has elapsed or whenever a new validated
  price window is available

Deterministic regime replay:

```bash
agent-harness regime-replay .agent-harness/runs/latest.json
agent-harness regime-replay .agent-harness/runs/latest.json --max-drawdown 0.08
agent-harness ledger regimes --min-regime-replays 1 \
  --require-latest-regime-replay \
  --require-regime-ok
```

Replay writes generated `Date,Close` fixtures and a report under
`.agent-harness/regimes`, ingests `.agent-harness/ledger/regimes.jsonl` by
default, and returns non-zero when any regime is fragile. The regimes are
deliberately simple and adversarial: primary trend, primary reversal, shock
recovery, and cash-drag rally. Each replay uses the realized outcome scorer, so
attribution, drawdown, cash drag, sentiment alignment fields, and excess-return
math match observed-window outcome reports. Use it before promotion to catch
concentrated primary-pick risk, cash drag in broad rallies, stale replay
evidence, and packets that pass terminal-return checks but violate drawdown
tolerance.

Regime replay pass/fail is scenario-specific. Primary trend requires positive
trend capture, primary reversal requires portfolio survival and better-than-naive
exposure, shock recovery requires positive recovery within drawdown tolerance,
and cash-drag rally requires keeping pace with risky assets. The goal is not to
pretend an intentionally losing primary pick was a hit; the goal is to force the
portfolio to survive that adversarial path.

Allocation repair:

```bash
agent-harness thesis
agent-harness thesis --no-allocation-repair
```

The repair step performs deterministic grid search over candidate non-negative
weights, evaluates each candidate through regime replay, and mutates the Monte
Carlo payload only when the best candidate improves replay score. The packet
records `allocation_repair.applied`, original allocation, selected allocation,
candidate count, max-position policy status, and original/selected replay
summaries. The raw engine headline is preserved as `action_plan.raw_headline`;
`action_plan.headline`, allocation rows, primary weight, and cash weight reflect
the repaired research allocation. When at least two non-primary alternatives are
available, repair candidates must satisfy the portfolio-wide max-position cap.
With only one non-primary alternative, the packet records
`insufficient_non_primary_alternatives`, and eval blocks production use if the
selected hedge row exceeds the cap.

The default offline thesis universe is `AAPL`, `MSFT`, `GOOGL`, `JPM`, and
`XOM`, so the live smoke path has enough non-primary alternatives and sector
breadth to satisfy deterministic replay and max-position controls without
operator intervention.

Fixture audit:

```bash
agent-harness fixtures refresh --source stooq --start-date 2024-01-02 --end-date 2024-01-15
agent-harness fixtures refresh --source csv-dir --source-dir /path/to/vendor-csvs
agent-harness fixtures refresh --no-verify-tls  # local CA fallback only
agent-harness fixtures audit
agent-harness fixtures audit --min-tickers 5 --min-sectors 3
```

Refresh currently supports Stooq's daily CSV download endpoint and `csv-dir` for
trusted vendor drops. It writes normalized `Date,Close` files with source
metadata in the refresh report. If Stooq returns a browser-verification HTML
challenge instead of CSV, refresh fails closed. The audit records per-ticker
file hashes, row counts, date coverage, sector tags, total return, period
volatility, an aligned return-correlation matrix, and readiness blockers. It
fails closed when the offline universe is too narrow, too short, sector-poor, or
effectively duplicated by excessive pairwise correlation.

Bulk backfill:

```bash
agent-harness ledger backfill-outcomes \
  --price-dir /path/to/csvs \
  --horizon-rows 5 \
  --rolling
```

Backfill evaluates every selected ledger packet copy, writes only new outcome
artifacts, ingests compact ledger rows, and reports `created`,
`skipped_existing`, `would_create`, and `failed` counts. It returns non-zero when
any packet/window fails, which keeps automated evidence collection fail-loud.

Calibration:

```bash
agent-harness ledger calibrate-outcomes --min-sample 20
agent-harness ledger calibrate-outcomes \
  --min-sample 20 \
  --write-gates \
  --gates-output agent-harness.gates.json
```

Calibration converts realized outcome rows into copyable promotion flags. It
uses p25 excess return, p75 absolute forecast error, p95 realized drawdown, and
observed ok-rate slack to recommend gates. It returns non-zero until the
configured minimum sample is present, which prevents thin samples from becoming
production defaults by accident. `--write-gates` writes nothing until
calibration is ready, then validates and atomically replaces the gates file.

Promotion gates:

- `agent-harness.gates.json` is loaded automatically by `ledger outcomes`,
  `ledger report`, and `ledger promote`.
- Explicit threshold flags override configured defaults for that command.
- `ledger --no-gates ...` disables configured defaults for inspection.
- Promotion attempts persist the applied gates summary in the report payload.
- The tracked gates file currently requires 20 realized outcomes, so the system
  fails closed until enough independent outcome windows exist.
- The tracked gates file also requires one latest-run deterministic replay with
  zero fragile regimes, bounded worst drawdown, and non-negative worst excess
  versus cash. Cash drag versus equal weight is enforced by the rally scenario
  criterion rather than a blunt all-regime aggregate threshold.

Production verification:

```bash
agent-harness ledger verify-production
```

This runs the fixture audit, outcome gate report, regime gate report, full
ledger promotion report, platform export validation, and receiving-side import
validation in one command. GitHub Actions also builds a synthetic
`.ci-production` ledger with `tools/build_ci_production_fixture.py` and runs the
same verifier against it with `--require-platform-signature`. That CI fixture
proves the signed export/import machinery works from a clean checkout and that
promotion-attempt blocker categories survive the platform handoff. Live
promotion evidence still comes from the real ledger and real observed outcomes.

Run `python3 tools/verify_research_run_platform_receiver.py --json` when the
sibling `research-run-platform` checkout is available. It exports the synthetic
fixture with `discover_promotions=True`, ingests it through the receiver, and
asserts the stored run record contains promotion attempts plus the top
`backtest` blocker category, and that the evidence payload exposes the latest
promoted attempt.
The patch artifact
`tools/patches/research-run-platform-promotion-evidence.patch` updates the
sibling receiver to expose `promotion.attempts` and latest attempt reports in
evidence JSON. After applying it, rerun the verifier with
`--require-evidence-promotion-attempts`.

## Next Production Commits

1. Run rolling outcome backfill against a larger price set, then regenerate
   `agent-harness.gates.json` with `ledger calibrate-outcomes --write-gates`.
2. Expand fixture refresh beyond the default five-name universe and wire a
   tracked sector/security-master source into refresh and audit.
3. Collect enough keyed sentiment windows to set production defaults for
   sentiment hit rate and confidence-weighted alignment.
