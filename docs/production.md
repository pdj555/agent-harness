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
7. Ledger ingest. Packets must enter the append-only ledger unless explicitly
   disabled by an operator.
8. Ledger report. Promotion readiness must be checked across the ledger, not just
   one packet.
9. Promotion gate. Canonical artifacts must be written only by `ledger promote`.
10. Evaluation. Packets must pass schema, risk-order, simulation, backtest,
    stress, position-cap, cash-buffer, and fingerprint checks before they are
    treated as production-ready.

## Packet Lifecycle

1. `agent-harness thesis ...` executes simulation plus walk-forward validation
   and writes a packet.
2. `.agent-harness/runs/latest.json` points to the most recent packet copy.
3. `agent-harness thesis` ingests the packet into `.agent-harness/ledger` by default.
4. `agent-harness replay <packet>` renders the saved decision without executing
   engines.
5. `agent-harness eval <packet>` checks whether the packet is usable.
6. `agent-harness outcome <packet>` scores realized P&L and drawdown over a
   Date,Close price window.
7. `agent-harness ledger list/show` exposes the append-only query surface.
8. `agent-harness ledger outcomes` aggregates realized outcome performance.
9. `agent-harness ledger report` aggregates performance and promotion blockers.
10. `agent-harness ledger trust` shows branch, SHA, dirty state, status lines,
   and trust-policy classification for the latest or selected run.
11. `agent-harness ledger promote` writes a promotion attempt and publishes
   `canonical.json` only if promotion is ready.
12. A future provenance adapter should push the packet into
   `research-run-platform` or an equivalent ledger.

## Ledger Files

- `.agent-harness/ledger/runs.jsonl`: append-only compact events.
- `.agent-harness/ledger/index.json`: run id to ledger entry index.
- `.agent-harness/ledger/latest.json`: latest compact ledger entry.
- `.agent-harness/ledger/packets/<run_id>.json`: immutable packet copy.
- `.agent-harness/ledger/outcomes.jsonl`: append-only realized-outcome events.
- `.agent-harness/ledger/latest_outcome.json`: latest compact outcome entry.
- `.agent-harness/ledger/outcomes/<run_id>_<start>_<end>_<digest>.json`: immutable outcome copy.

Ledger ingest is idempotent when the same `run_id` and digest are seen again.
If a run id is reused for different content, ingest fails.

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

When dirty repos block promotion, run `agent-harness ledger trust` to inspect the
exact branch, SHA, dirty count, and bounded status lines captured in the packet.
That keeps remediation tied to the same immutable run artifact that promotion is
evaluating.

Use `--trust-policy <path>` with `ledger trust`, `ledger report`, and
`ledger promote` to classify dirty changes. Policy rules can allow precise
repo/path/status combinations and can add explicit hard blocks. Without a policy,
all dirty changes block promotion. Keep production policy narrow: documentation
and generated local artifacts can be allowed; capital-engine code, tests,
dependency files, config, and data fixtures should remain blocking until
committed.

`agent-harness ledger promote` persists each promotion attempt under
`.agent-harness/promotions/attempts`. Blocked attempts update
`.agent-harness/promotions/latest.json` but do not write
`.agent-harness/promotions/canonical.json`.

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
- compares allocation return to equal weight and cash
- records primary-pick hit/miss, forecast error, and realized max drawdown
- records per-ticker price CSV hashes and an aggregate price-source digest
- rejects duplicate price dates, negative weights, and unbalanced unlevered portfolios
- attributes return and active excess by position weight, omitted tickers, cash
  return contribution, and cash drag
- persists immutable outcome artifacts and compact ledger rows
- aggregates hit rate, beat-cash rate, beat-equal-weight rate, excess returns,
  forecast-error calibration, drawdown, and attribution with `ledger outcomes`
- supports promotion thresholds for minimum ok rate, minimum excess return,
  maximum average absolute forecast error, and maximum realized drawdown
- should be run once the thesis horizon has elapsed or whenever a new validated
  price window is available

## Next Production Commits

1. Add a `research-run-platform` writer that syncs local ledger entries as immutable runs.
2. Add a tracked production trust policy after reviewing which docs/generated
   paths can be allowed without weakening capital controls.
3. Add enough independent realized outcome windows to enable a non-zero
   `--min-outcomes` promotion gate by default.
4. Choose production default outcome thresholds for forecast error and drawdown
   after collecting enough independent windows.
5. Add an eval fixture suite that compares packet rankings across deterministic
   replay scenarios.
6. Add a live sentiment adapter that can run when credentials are present, then
   cap its influence as a short-half-life overlay.
