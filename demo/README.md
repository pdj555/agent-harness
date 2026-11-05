# Sabotage vs Spec — the demo

The eval in this repository, as a playable page: sabotaged code changes that
pass the project's tests, judged one at a time by the executable spec.

The headline, as the machinery produces it: of 33 published changes (27
attacks, 6 honest fixes), the project's tests catch 0 of the 27 attacks,
the diff rules catch 6, and the executable spec catches 27 while blocking
0 of the 6 honest fixes. A separate set holds the attacks written against
the spec itself: 3 still get through, 2 no longer do. The canonical
record is `docs/evals.md` and `docs/blind-spots.md`; this demo is a
window onto the same machinery, not a second source of numbers.

## Run a case

From the repository root, in the synced environment (`uv sync --extra dev`):

```bash
uv run python demo/run_case.py tracker-telemetry   # one case, JSON on stdout
uv run python demo/run_case.py --all               # the demo playlist -> demo/cases.json
uv run python demo/run_case.py --emit-results      # the full sets -> demo/results.json
```

`run_case.py` adds no verdict of its own. It builds the case with
`build_case`, takes the tests verdict from `harness.verification.run_checks`
(the only source of `passed`, per AGENTS.md), the diff-rules verdict from
`harness.review.inspect_change`, and the spec verdict from
`harness.evals.monitor.spec_monitor`. Blind-spot cases get one extra beat:
the reveal, which calls the module once on the input the spec never probes
(the inputs pinned by `tests/test_spec_blind_spots.py`) and checks the
answer against the documented rule.

## The page

`demo/page/index.html` is a single static file with no build step. It
reads every number it prints from `demo/cases.json` (the per-case traces)
and `demo/results.json` (the recorded full-set rows); it contains no eval
logic and no figures of its own. Serve the demo folder and open the page:

```bash
python3 -m http.server 8000 --directory demo
# http://localhost:8000/page/
```

Two kinds of rows, labeled as such, never blended:

- **Recorded traces** (section 02) — runs of `run_case.py` against the
  playlist: five hero cases (one honest fix as the control) and the
  blind-spot group. Playback replays the recorded steps; it does not
  re-execute anything in the browser.
- **RECORDED rows** (section 04) — the full published set and the
  blind-spot sets, produced by `run_case.py --emit-results`, plus the
  Claude Haiku 5.5 reviewer row from the recorded run of 2026-10-14
  (`--monitor claude --model claude-haiku-5-5 --trials 3`, 99 judged
  reviews, kappa 1.00), quoted from `docs/evals.md` and not rerun.

## A note on the counts

Open pull requests #23, #24, #26 and #28 change the checks, the case set,
or the spec probe itself. The demo therefore never hardcodes a count: the
playlist's labels and notes are read from `harness/evals/cases.py` at run
time, and every verdict on the page comes from a run of the machinery at
the commit the JSON was generated against. Merge one of those PRs,
regenerate the two JSON files, and the page prints the new numbers.
