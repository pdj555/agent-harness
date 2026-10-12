# Agent Harness

Hand a coding agent an objective and walk away. It works in an isolated copy of your repo. Nothing lands unless your tests pass and a model from a different vendor approves the diff.

Claude and GPT run in their vendors' own coding agents, Claude Code and Codex, because that is where the frontier work happens. Every other model runs on the harness's own loop through OpenRouter. The harness owns what none of them do: isolation, a test gate no model can override, and cross-vendor review.

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
2. **Isolate.** The agent works in a Git worktree. Your source tree does not move.
3. **Verify.** The project's own checks run after the agent stops. Their exit code is the verdict; the agent's "done" is ignored.
4. **Review.** Software checks the diff (tests still exist, implementation changed), then the second model reviews it.
5. **Apply.** You land the verified diff. Explicit and reversible.

Stop ends all further work. History is durable.

## Run it

```bash
uv sync --extra dev
export ANTHROPIC_API_KEY=...   # and OPENAI_API_KEY for cross-vendor review
uv run harness user add you --password 'choose-your-own'
uv run harness repo add /path/to/your/repo
uv run harness serve
```

Open `http://127.0.0.1:7465`, log in, pick the repository, and click **Next dollar** or type an objective.

No key, no cost:

```bash
uv run harness demo
```

The demo runs a scripted agent that repairs a known defect in `examples/sample-repo`, so CI needs no vendor.

## Configure

Copy [harness.example.toml](harness.example.toml) to `harness.toml` to change the port, add roots, extend `check_timeout`, or set a `check_command` other than pytest (`npm test`, `go test ./...`). See [.env.example](.env.example) for every variable.

## Guarantees

- Models propose. The project's checks and independent review decide.
- Claude's file tools are confined to the worktree by a hook. Codex runs in its own workspace sandbox. The OpenRouter loop goes through the harness's permission checks.
- Each agent runs with a stripped environment: its own vendor key, proxy and certificate settings, and a HOME beside the worktree. Host secrets and your real HOME never reach it. Codex keeps its login there too, so your own `~/.codex` is untouched.
- This is not an OS sandbox. Shell commands run in the worktree with your user's permissions. See [docs/security.md](docs/security.md).

## Tests

```bash
uv run pytest
uv run ruff check harness tests scripts
uv run python scripts/scan_secrets.py
uvx pip-audit
uv build
uv run python scripts/scan_secrets.py dist/*.whl
uv run python scripts/smoke_wheel.py dist/*.whl
```

Apache License 2.0. See [LICENSE](LICENSE).
