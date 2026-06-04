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
5. Repo fingerprints. Every adapter must expose repo SHA and dirty status.
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
6. `agent-harness ledger list/show` exposes the append-only query surface.
7. `agent-harness ledger report` aggregates performance and promotion blockers.
8. `agent-harness ledger promote` writes a promotion attempt and publishes
   `canonical.json` only if promotion is ready.
9. A future provenance adapter should push the packet into
   `research-run-platform` or an equivalent ledger.

## Ledger Files

- `.agent-harness/ledger/runs.jsonl`: append-only compact events.
- `.agent-harness/ledger/index.json`: run id to ledger entry index.
- `.agent-harness/ledger/latest.json`: latest compact ledger entry.
- `.agent-harness/ledger/packets/<run_id>.json`: immutable packet copy.

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

## Next Production Commits

1. Add a `research-run-platform` writer that syncs local ledger entries as immutable runs.
2. Add an eval fixture suite that compares packet rankings across deterministic
   replay scenarios.
3. Add a live sentiment adapter that can run when credentials are present, then
   cap its influence as a short-half-life overlay.
