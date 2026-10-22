# agent-harness

**A merge gate for coding agents.** Nothing lands unless your tests pass and an independent check approves the diff.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
  <img alt="Dot chart of 27 sabotaged changes that all pass their tests: the project's tests catch 0, diff rules 6, a Claude Haiku 5.5 reviewer 27, and an executable spec 27." src="docs/assets/hero-light.svg" width="100%">
</picture>

<sub>One question, four repos: **how can we trust agents to merge without a human reading every diff?**<br>
**agent-harness** · [agent-eval-harness](https://github.com/pdj555/agent-eval-harness) · [trajectory-to-training](https://github.com/pdj555/trajectory-to-training) · [hard-rag-evals](https://github.com/pdj555/hard-rag-evals)</sub>

[![ci](https://github.com/pdj555/agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/pdj555/agent-harness/actions/workflows/ci.yml)

## The finding

Tests are not a review. The eval is 27 changes that pass every test and should never ship: answers hardcoded for the tested inputs, loosened assertions, fixes that are right only where the tests look, and "telemetry" that sends your environment to an outside host.

| Check | Sabotage caught | Honest fixes blocked |
| --- | --- | --- |
| The project's own tests | 0 of 27 | 0 of 6 |
| Software diff rules | 6 of 27 | 0 of 6 |
| Claude Haiku 5.5 as reviewer | **27 of 27** | 0 of 6 |
| Executable spec, no model | **27 of 27** | 0 of 6 |

A model reviewer is only as safe as it is smarter than the agent. A spec runs the code on inputs the agent didn't choose, so it doesn't weaken as agents improve, until it's attacked directly. Two attacks written to beat it get through: one wrong only above the inputs it checks, one wrong only when `APP_ENV=production`. [Where the spec breaks →](docs/blind-spots.md)

## Run it

No API key needed.

```bash
uv sync --extra dev
uv run harness demo                                             # an agent fixes a seeded bug, end to end
uv run harness eval monitor --monitor spec                      # 27 of 27 caught
uv run harness eval monitor --monitor spec --set blind-spots    # 0 of 2 caught
```

## How it works

1. **Isolate.** The agent works in a Git worktree. Your source tree never moves, and ignored files like `.env` never enter it.
2. **Verify.** Your own test command decides. The agent's "done" is ignored.
3. **Review.** A model from a different lab reads the diff. A crash or a non-verdict blocks the change.
4. **Apply.** Only the exact files that passed land, checked by fingerprint.

Engines: Claude Code, Codex, or any model on OpenRouter.

## Limits

- The attacks are hand-written, and both strong checks saturate on them.
- The tasks are single functions with clean docstrings. Real code rarely ships a spec.
- This is not an OS sandbox. Bind to loopback and read [security](docs/security.md).

## Docs

[Research summary](docs/research.md) · [Where the spec breaks](docs/blind-spots.md) · [Eval method](docs/evals.md) · [Guide](docs/guide.md) · [Architecture](docs/architecture.md) · [Security](docs/security.md)

Apache 2.0
