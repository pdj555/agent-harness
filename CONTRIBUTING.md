# Contributing

Changes should make the agent more capable or the code easier to understand. Size for its own sake doesn't count.

## Setup

```bash
uv sync --extra dev
uv run pytest
uv run ruff check harness tests scripts
uv run python scripts/scan_secrets.py
```

`uv run harness demo` is the fastest end-to-end check. It runs the scripted agent against `examples/sample-repo` and should end with `status: completed`.

## What a good change looks like

- Tests drive the real entry points (`execute_run`, the HTTP API, the CLI) and fail if the behavior breaks.
- Nothing lets model output set a verdict or skip a permission check. See [AGENTS.md](AGENTS.md).
- One principal agent does the work. New helpers have to earn their place.
- User-facing copy says what happened and what to do next.

## What not to send

Secrets, credentials, private endpoints, personal paths, customer data, or recorded runs. CI scans for some of this. Reviewers look for the rest.

## Pull requests

Keep them focused. Say what changed and how you verified it. CI runs lint, tests, a secret scan, `pip-audit`, a wheel build, and the demo from the built wheel.
