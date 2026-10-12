# CLAUDE.md

Read [AGENTS.md](AGENTS.md). It is the contract for this repository and applies to Claude Code the same as any other agent.

The short version:

- Models propose, software verifies. Only `harness.verification.run_checks` sets `passed`.
- Inspect first. Edit only inside the isolated worktree. Apply is a separate, verified step.
- Keep the public boundary: no secrets, private endpoints, personal paths, or run history.
- Run `uv run pytest` and `uv run ruff check harness tests scripts` before claiming done.

Architecture lives in [docs/architecture.md](docs/architecture.md).
