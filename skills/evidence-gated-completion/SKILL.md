---
name: evidence-gated-completion
description: Use when changing verification, review, runtime completion, or conversation persistence and wake behavior.
---

Models propose. Software verifies.

- `harness.verification.run_checks` owns `passed`. It reads a process exit code
  and, for pytest, the run's JUnit report. No report, no test run, or any failure
  or error is a failed check. Hooks that alter results or forge the report remain
  a review concern; a green report does not prove the tests are trustworthy.
- Do not add a parameter, tool, or JSON field that lets model text set `passed`.
- Independent review is a distinct `role: reviewer` record. Do not copy the
  principal's final text into it.
- Keep reviewer conversations and step counts out of the principal's persisted
  session. Verify this through the runtime, including blocking verdicts.
- On wake, preserve recorded tool results and close unresolved calls with
  explicit interrupted/error results before requesting another model response.
  Execution is unknown; recovery must not replay mutations automatically.
- Exercise interruption after a tool's side effect but before result persistence,
  and within a batch with some results already recorded. Check the adapter's
  outgoing exchange and real filesystem effects. See
  [wake tests](../../tests/test_wake.py) and
  [review isolation tests](../../tests/test_anthropic_runtime.py).
- A failed check is status `failed`, never `completed`.
- Tests must drive the shipped runtime, not a reimplemented oracle.
