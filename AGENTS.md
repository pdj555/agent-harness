# AGENTS.md

The contract for any coding agent working in this repository. CLAUDE.md points here.

## What this is

A local coding agent with one rule: models propose, software verifies. A user picks an allowlisted repository and gives one objective. The principal agent inspects the code, edits an isolated Git worktree, and runs the project's checks. A separate review looks at the diff. Completion is decided by a process exit code and a review record, never by model text.

## Rules you can't bend

- `harness.verification.run_checks` is the only source of `passed`. Don't add a parameter, tool, or JSON field that lets model output set it.
- Every tool call goes through `harness.authority`. Nothing skips the role, path, or stop checks.
- Mutating work happens in the stage. The source repository changes only through an explicit apply of a verified fingerprint.
- After stop, mutating tools are rejected and recorded as skipped.
- Don't claim a fix you haven't run the tests for.

## How to work here

Inspect before you plan. Make the smallest change that does the job and keep it isolated. Run the tests as you go, and repair failures yourself instead of handing them back. Ask the user only when evidence can't decide.

Write tests that drive the shipped entry points and would fail if the behavior broke. Prefer a small architecture someone can read in an afternoon over a clever one.

## Public boundary

This repository is public. Keep it general-purpose: no secrets, credentials, private endpoints, personal paths, customer data, or historical run output. If something only makes sense for one private setup, leave it out. `tests/test_boundary.py` and `scripts/scan_secrets.py` enforce part of this.

## Before you say done

```bash
uv run pytest
uv run ruff check harness tests scripts
uv run python scripts/scan_secrets.py
```

Project skills in [`skills/`](skills/) cover verification, isolation, and the public boundary. Read the one that matches your change.
