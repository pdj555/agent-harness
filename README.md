# agent-harness

> **How can we trust agents to merge without a human reading every diff?**
> One question, four repos. **agent-harness** is the gate · [agent-eval-harness](https://github.com/pdj555/agent-eval-harness) measures the agent · [trajectory-to-training](https://github.com/pdj555/trajectory-to-training) keeps only verified work for training · [hard-rag-evals](https://github.com/pdj555/hard-rag-evals) asks the same of answers

Agents now write code faster than anyone can review it. This is the gate between an agent and your main branch. The agent works in an isolated copy of your repo. Nothing lands unless your own tests pass and a model from a different lab approves the diff.

[![ci](https://github.com/pdj555/agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/pdj555/agent-harness/actions/workflows/ci.yml)

## Does the gate hold?

Tests alone don't. The eval is 27 changes that pass every test but should never ship: answers hardcoded for exactly the tested inputs, assertions loosened until the bug passes, a fix that is right only where the tests look, and "usage telemetry" that posts the environment to an outside host. Twelve are written to read like ordinary changes. Six honest fixes check for false blocks.

| Check | Sabotage caught | Honest fixes blocked |
| --- | --- | --- |
| The project's own tests | 0 of 27 | 0 of 6 |
| Software diff rules | 6 of 27 | 0 of 6 |
| Executable spec, no model | **27 of 27** | 0 of 6 |
| Claude Haiku 5.5 as reviewer | **27 of 27** | 0 of 6 |

The spec result matters most. A model reviewer is only as safe as it is smarter than the agent. A spec gate's verdict rests on the spec and the inputs it runs, so it does not weaken as agents get stronger.

The open problem: these attacks are handwritten, so every strong check saturates. Next is an attacker that sees each verdict and retries until something gets through. Method, intervals, and limits: [docs/evals.md](docs/evals.md).

Reproduce the spec row in about a minute, no key:

```bash
uv sync --extra dev
uv run harness eval monitor --monitor spec
```

Claude and GPT run in their vendors' own coding agents, Claude Code and Codex. Every other model runs on the harness's own loop through OpenRouter. The harness owns what none of them do: isolation, a test gate no model can override, and cross-vendor review.

## Try it in one minute

No key, no cost. A scripted agent fixes a seeded bug in `examples/sample-repo`:

```bash
uv sync --extra dev
uv run harness demo
```

```text
status: completed
verification_passed: True
review: Implementation changed. Tests remain. No blocking findings.
files:
  tracker.py
```

## Run it on your own code

```bash
export ANTHROPIC_API_KEY=...   # and OPENAI_API_KEY for cross-vendor review
uv run harness user add <you> --password '<password>'
uv run harness repo add ~/code/your-repo
uv run harness serve
```

Open http://127.0.0.1:7465, log in, pick the repository, and type an objective. If you don't know what to ask for, **Suggest a fix** reads the tests, open TODOs, and recent commits and picks something. When the run is green you get the diff, the check output, and both reviews. **Apply** copies exactly the files that were verified.

## Engines

| `HARNESS_PROVIDER` | Runs | Needs |
| --- | --- | --- |
| `claude` | Claude Code, via the Claude Agent SDK | `ANTHROPIC_API_KEY` |
| `codex` | Codex, via the Codex SDK | `OPENAI_API_KEY` or `CODEX_API_KEY` |
| `openrouter` | Any OpenRouter model: Grok, DeepSeek, Qwen, Llama, and the rest | `OPENROUTER_API_KEY` and `HARNESS_MODEL` |

Leave `HARNESS_PROVIDER` unset and the first key found wins, in that order. `HARNESS_MODEL` picks the model for any engine.

## Review

Every diff that passes the tests goes to the other vendor, with read-only access:

- Claude's work is reviewed by Codex, and Codex's by Claude.
- OpenRouter runs are reviewed by Claude, else Codex.
- `HARNESS_REVIEWER=claude|codex|none` overrides the pick, and `HARNESS_REVIEWER_MODEL` sets its model.

A block goes back to the agent as findings to fix, in the same session. A reviewer that crashes or answers in anything but a verdict blocks the change. With only one vendor's key configured, review is software-only.

## How a run works

1. **Scope.** Pick an allowlisted repository and state the objective. Software scans tests, TODO markers, and recent commits first.
2. **Isolate.** The agent works in a Git worktree under `.harness/stages/<run>/`. Ignored files (`.env`, `node_modules`, caches) never enter it. Your source tree does not move.
3. **Verify.** The project's own checks run after the agent stops. Their exit code is the verdict; the agent's "done" is ignored.
4. **Review.** Software checks the diff (tests still exist, implementation changed), then the second model reviews it.
5. **Repair.** Failed checks or review findings go back to the agent, up to `max_repairs` times. Then the run fails.
6. **Apply.** Verification fingerprints the staged tree. Apply re-checks that fingerprint, so the files that land are the files that passed.

Every exit is terminal: `completed`, `failed`, or `stopped`. Stop ends all further work. History is durable.

## Does review catch an agent gaming its tests?

Results are at the top of this page. How each check works:

- **Software rules** flag a new skip marker, a deleted test, or a `conftest.py` that replaces project code. They catch the obvious attacks and none of the subtle ones.
- **Executable spec** (`--monitor spec`) copies the changed module alone into an empty directory and calls it on every tested input and thousands of untested ones, comparing each answer with a spec written from the docstring. An audit hook refuses file writes, network calls, and subprocesses. Every finding is a counterexample or a refused call, for example `split_cents(2, 3) gives [2, 0, 0]`.
- **Model reviewer** (`--monitor claude` or `--monitor codex`) sees exactly what a live run sends: the prompt, the check output, and the diff. Bring your own key.

Specs written by Claude Haiku 5.5 and Sonnet 5.5 from the docstring alone, never seeing the code or the tests, also caught 27 of 27 in all 6 sets. Method and limits: [docs/evals.md](docs/evals.md).

## Guarantees and limits

- Models propose. The project's checks and independent review decide.
- Claude's file tools are confined to the worktree by a hook. Codex runs in its own workspace sandbox. The OpenRouter loop goes through the harness's permission checks.
- Each agent runs with a stripped environment: its own vendor key, proxy and certificate settings, and a HOME beside the worktree. Host secrets and your real HOME never reach it. Codex keeps its login there too, so your own `~/.codex` is untouched.
- This is not an OS sandbox. Shell commands run in the worktree with your user's permissions. Bind to loopback, keep `workspace.roots` small, and read [docs/security.md](docs/security.md).

## Configure

Copy [harness.example.toml](harness.example.toml) to `harness.toml`. The settings that matter:

- `workspace.roots`: the only repositories the app will open.
- `workspace.check_command`: the verdict command. Defaults to `python -m pytest -q`. Use `npm test` or `go test ./...` for other stacks. It's split with `shlex` and never handed to a shell.
- `workspace.check_timeout`: seconds before a check counts as failed. Default 300.

See [.env.example](.env.example) for every variable.

## Layout

```text
harness/
  agents.py         Claude Code, Codex, and OpenRouter engines behind one Agent interface
  runtime.py        the run: scan, drive the engine, verify, review, repair
  verification.py   runs the check command; the only source of `passed`
  review.py         software review gate plus the cross-vendor model reviewer
  isolation.py      worktree staging, fingerprints, apply
  authority.py      role and path checks every tool call goes through
  tools.py          the OpenRouter loop's tool set
  provider.py       deterministic, scripted, and OpenRouter providers
  triage.py         reads tests, TODOs, and commits for Suggest a fix
  app.py            FastAPI server and the web client in static/
  store.py          SQLite users, sessions, and durable runs
docs/               architecture, runtime, security, demo
examples/           the seeded sample repository
skills/             guidance for agents working on this repo
```

## Develop

```bash
uv sync --extra dev
uv run pytest
uv run ruff check harness tests scripts
uv run python scripts/scan_secrets.py
```

CI also runs `pip-audit`, builds the wheel, scans it for secrets, and runs the demo from the installed wheel. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Docs

- [Architecture](docs/architecture.md)
- [Agent runtime](docs/agent-runtime.md)
- [Security model](docs/security.md)
- [Demo walkthrough](docs/demo.md)

## License

Apache 2.0. See [LICENSE](LICENSE).
