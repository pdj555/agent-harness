# agent-harness

27 changes were sabotaged to look like honest fixes, and every one of them passes the project's tests. The tests catch 0. A spec written as code catches all 27. Two attacks built to beat the spec get through. This is a merge gate for coding agents: a change lands only when the tests pass and a second check, independent of the agent, approves the diff.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
  <img alt="Dot chart of 27 sabotaged changes that all pass their tests: the project's tests catch 0, diff rules 6, a Claude Haiku 5.5 reviewer 27, and an executable spec 27." src="docs/assets/hero-light.svg" width="100%">
</picture>

<sub>One question, four repos: **how can we trust agents to merge without a human reading every diff?**<br>
**agent-harness** · [agent-eval-harness](https://github.com/pdj555/agent-eval-harness) · [trajectory-to-training](https://github.com/pdj555/trajectory-to-training) · [hard-rag-evals](https://github.com/pdj555/hard-rag-evals)</sub>

[![ci](https://github.com/pdj555/agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/pdj555/agent-harness/actions/workflows/ci.yml)

## Use it

```bash
uv sync --extra dev
export ANTHROPIC_API_KEY=...
export HARNESS_MODEL="your-claude-model-id"
uv run harness repo add ./your-repo
uv run harness run ./your-repo "Fix the broken behavior and add a regression test."
```

The command prints the diff, check output, review, and a run ID. Your source stays unchanged. Apply the exact verified patch with `uv run harness apply <run-id>`.

An Anthropic-only setup gets a fresh, read-only Claude reviewer. With both vendors configured, cross-vendor review is preferred. [Configuration and web workspace →](docs/guide.md)

## The finding

Passing tests is not a review. The eval is 27 changes that pass every test and should never ship. The tricks are plain ones: answers hardcoded for the tested inputs, assertions loosened until they pass, fixes that are right only where the tests look, and code marked as telemetry that sends your environment to an outside host.

| Check | Sabotage caught | Honest fixes blocked |
| --- | --- | --- |
| The project's own tests | 0 of 27 | 0 of 6 |
| Software diff rules | 6 of 27 | 0 of 6 |
| Claude Haiku 5.5 as reviewer | **27 of 27** | 0 of 6 |
| Executable spec, no model | **27 of 27** | 0 of 6 |

The published model scores used the previous Claude Agent SDK reviewer. They have not been rerun against the current Messages API reviewer.

A model reviewer helps only while it is smarter than the agent it reviews. A spec works another way. It runs the code on inputs the agent never chose, so it holds up as agents improve, until someone attacks it head on. 2 attacks written to beat it get through: one is wrong only above the inputs it checks, and one is wrong only when `APP_ENV=production`. [Where the spec breaks →](docs/blind-spots.md)

## Try the offline demo

No API key needed.

```bash
uv sync --extra dev
uv run harness demo                                             # an agent fixes a seeded bug, end to end
uv run harness eval monitor --monitor spec                      # 27 of 27 caught
uv run harness eval monitor --monitor spec --set blind-spots    # 0 of 2 caught
```

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/flow-dark.svg">
  <img alt="The agent edits an isolated worktree, your tests run, and passing changes go to a review gate before reaching main. Failures and blocks go back to the agent. All 27 sabotaged changes pass the tests; the published gate catches 27, and 2 attacks built to beat the spec get through." src="docs/assets/flow-light.svg" width="100%">
</picture>

1. **Isolate.** The agent works in a Git worktree. Your source tree never moves, and ignored files like `.env` never enter it.
2. **Verify.** Your own test command decides. The agent's "done" is ignored.
3. **Review.** A fresh reviewer reads the diff and can inspect files. Claude review is limited to read-only harness tools. Cross-vendor review is preferred. A crash, malformed verdict, or blocking finding rejects the change.
4. **Apply.** Only the exact files that passed land, checked by fingerprint.

Engines: Anthropic Messages API, Codex, or any model on OpenRouter. Claude uses the same authority-checked tools as the harness loop.

## Limits

- The attacks were written by hand, and both strong checks catch all 27, so this eval cannot separate them.
- Each task is a single function with a clean docstring. Real code rarely ships a spec with it.
- This is not an OS sandbox, so do not treat it like one. Bind to loopback and read [security](docs/security.md).

## Docs

[Research summary](docs/research.md) · [Where the spec breaks](docs/blind-spots.md) · [Eval method](docs/evals.md) · [Guide](docs/guide.md) · [Architecture](docs/architecture.md) · [Security](docs/security.md)

Apache 2.0
