# agent-harness guide

The full reference: setup, usage, architecture, and design. For what this repo found and why it matters, see the [README](../README.md) and the [research summary](research.md).

## Run it on your own code

One objective produces a staged patch with checks and review. No server or account is required:

```bash
export ANTHROPIC_API_KEY=...
export HARNESS_MODEL="your-claude-model-id"
uv run harness repo add ./your-repo
uv run harness run ./your-repo "Fix the broken behavior and add regression coverage."
uv run harness apply <run-id>
```

`run` prints the diff and proof. It leaves the source unchanged, even if `auto_publish` is enabled for the web workspace. `apply` requires the stored passing checks, review, and matching fingerprint. A failed or stopped run exits nonzero. Ctrl-C records a stopped run. Use the same `--config` for both commands when selecting a custom configuration.

For the web workspace:

```bash
export ANTHROPIC_API_KEY=...
export HARNESS_MODEL="your-claude-model-id"
# Optional: OPENAI_API_KEY enables cross-vendor review.
uv run harness user add <you> --password '<password>'
uv run harness repo add ~/code/your-repo
uv run harness serve
```

Open http://127.0.0.1:7465, log in, pick the repository, and type an objective. If you don't know what to ask for, **Suggest a fix** reads the tests, open TODOs, and recent commits and picks something. When the run is green you get the diff, the check output, and both reviews. **Apply** copies exactly the files that were verified.

## Engines

| `HARNESS_PROVIDER` | Runs | Needs |
| --- | --- | --- |
| `claude` | Anthropic Messages API on harness tools | `ANTHROPIC_API_KEY` and `HARNESS_MODEL` |
| `codex` | Codex, via the Codex SDK | `OPENAI_API_KEY` or `CODEX_API_KEY` |
| `openrouter` | Any OpenRouter model: Grok, DeepSeek, Qwen, Llama, and the rest | `OPENROUTER_API_KEY` and `HARNESS_MODEL` |

Leave `HARNESS_PROVIDER` unset and the first key found wins, in that order. `HARNESS_MODEL` picks the model for any engine.

## Review

Every diff that passes the tests goes through the software review gate. Model review prefers another vendor:

- Claude's work is reviewed by Codex when its key is available. An Anthropic-only run gets a fresh Claude review context, using the principal model unless `HARNESS_REVIEWER_MODEL` is set. This separates the review from the author's conversation; it does not provide cross-vendor independence.
- Codex's work is reviewed by Claude when its key is available.
- OpenRouter runs are reviewed by Claude, else Codex.
- `HARNESS_REVIEWER=claude|codex|none` overrides the pick. Claude review of another vendor requires `HARNESS_REVIEWER_MODEL` with an explicit Claude model ID, so the reviewer never inherits an incompatible principal model. Codex keeps its vendor default unless overridden.

A block goes back to the agent as findings to fix, in the same conversation. Claude review uses strict JSON output plus read-only harness tools. A crash, incomplete response, malformed verdict, or any blocking finding rejects the change. A Codex-only setup uses software review unless a Claude reviewer is configured. `HARNESS_REVIEWER=none` explicitly selects software review.

## How a run works

1. **Scope.** Pick an allowlisted repository and state the objective. Software scans tests, TODO markers, and recent commits first.
2. **Isolate.** The agent works in a Git worktree under `.harness/stages/<run>/`. Ignored files (`.env`, `node_modules`, caches) never enter it. Your source tree does not move.
3. **Verify.** The project's own checks run after the agent stops. Their exit code is the verdict; the agent's "done" is ignored.
4. **Review.** Software checks the diff (tests still exist, implementation changed), then the second model reviews it.
5. **Repair.** Failed checks or review findings go back to the agent, up to `max_repairs` times. Then the run fails.
6. **Apply.** Verification fingerprints the staged tree. Apply re-checks that fingerprint, so the files that land are the files that passed.

Every exit is terminal: `completed`, `failed`, or `stopped`. Stop blocks further tool execution. A pending Messages API request can continue until it returns or times out. History is durable.

## Guarantees and limits

- Models propose. The project's checks and independent review decide.
- Claude and OpenRouter use the same harness tools and authority checks. Claude has no separate built-in shell or file tools. Codex runs in its own workspace sandbox.
- Harness tool subprocesses receive a filtered environment without API keys and a HOME beside the worktree. The Anthropic client stays in the harness process. Codex receives only its vendor credential, keeps its login beside the stage, and leaves your own `~/.codex` untouched.
- This is not an OS sandbox. Shell commands run in the worktree with your user's permissions. Bind to loopback, keep `workspace.roots` small, and read [docs/security.md](security.md).

## Configure

Copy [harness.example.toml](../harness.example.toml) to `harness.toml`. The settings that matter:

- `workspace.roots`: the only repositories the app will open.
- `workspace.check_command`: the verdict command. Defaults to `python -m pytest -q`. Use `npm test` or `go test ./...` for other stacks. It's split with `shlex` and never handed to a shell.
- `workspace.check_timeout`: seconds before a check counts as failed. Default 300.

See [.env.example](../.env.example) for every variable.

## Layout

```text
harness/
  agents.py         authority-checked model loop, Codex adapter, and reviewer selection
  anthropic.py      streamed Messages API, strict tools, cached history, structured review
  runtime.py        the run: scan, drive the engine, verify, review, repair
  verification.py   runs the check command; the only source of `passed`
  review.py         software review gate plus the cross-vendor model reviewer
  isolation.py      worktree staging, fingerprints, apply
  authority.py      role and path checks every tool call goes through
  tools.py          shared authority-checked tools for Claude and OpenRouter
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

CI also runs `pip-audit`, builds the wheel, scans it for secrets, and runs the demo from the installed wheel. See [CONTRIBUTING.md](../CONTRIBUTING.md).
