# agent-harness guide

For what this repo found and why it matters, see the [README](../README.md) and the [research summary](research.md).

## Run it on your own code

```bash
export ANTHROPIC_API_KEY=...   # and OPENAI_API_KEY for cross-vendor review
uv run harness user add <you> --password '<password>'
uv run harness repo add ~/code/your-repo
uv run harness serve
```

Open http://127.0.0.1:7465, log in, pick the repository, and type an objective. If you do not know what to ask for, **Suggest a fix** reads the tests, open TODOs, and recent commits and picks something. When the run is green you get the diff, the check output, and the review. **Apply** copies exactly the files that were verified.

The browser cannot create accounts. `POST /api/signup` is refused. The operator creates each account from the CLI, and passwords are stored as scrypt hashes.

## Engines

| `HARNESS_PROVIDER` | Runs | Needs |
| --- | --- | --- |
| `claude` | Claude Code, via the Claude Agent SDK | `ANTHROPIC_API_KEY` |
| `codex` | Codex, via the Codex SDK | `OPENAI_API_KEY` or `CODEX_API_KEY` |
| `openrouter` | Any OpenRouter model: Grok, DeepSeek, Qwen, Llama, and the rest | `OPENROUTER_API_KEY` and `HARNESS_MODEL` |

Leave `HARNESS_PROVIDER` unset and the first key found wins, in that order. `HARNESS_MODEL` picks the model for any engine.

## Review

Every diff that passes the tests is reviewed. The reviewer can read the worktree and cannot edit it:

- Software rules run first: tests must still exist, an implementation file must have changed, and the diff is checked for new skip markers, deleted tests, and a `conftest.py` that replaces project code at import. These are the rules the [monitor eval](evals.md) measures at 6 of 27 attacks caught.
- Then a model from the other vendor reads the diff. Claude's work is reviewed by Codex, and Codex's by Claude. OpenRouter runs are reviewed by Claude, else Codex. `HARNESS_REVIEWER=claude|codex|none` overrides the pick, and `HARNESS_REVIEWER_MODEL` sets its model.

A block goes back to the agent as findings to fix, in the same session. A reviewer that crashes or answers in anything but a verdict blocks the change. With only one vendor's key configured, review is software only.

## How a run works

1. Pick an allowlisted repository and state the objective. Software scans tests, TODO markers, and recent commits first.
2. The agent works in a Git worktree under `.harness/stages/<run>/`, staged from tracked files and untracked files Git does not ignore. `.env`, `node_modules`, and caches never enter it. Your source tree does not move.
3. The project's own checks run after the agent stops. Their exit code is the verdict; the agent's "done" is ignored. A suite that runs past `workspace.check_timeout` (default 300 seconds) fails with the partial output.
4. Software checks the diff, then the second model reviews it. Review runs against a fingerprint of the staged tree, so what gets reviewed is what passed the checks.
5. Failed checks or review findings go back to the agent, up to `max_repairs` times. Then the run fails.
6. Apply checks the fingerprint again on a copy of the stage before publishing. The files that land are the files that passed, byte for byte.

Every exit is terminal: `completed`, `failed`, or `stopped`. Stop ends all further work: engines check the flag around every step, and a mutating call after stop is rejected and recorded. History is durable in SQLite under `.harness/`.

## Guarantees and limits

- Models propose. The project's checks and independent review decide.
- Claude's file tools are confined to the worktree by a hook that denies paths outside it, git internals, and every call after stop. Codex runs in its own workspace sandbox. The OpenRouter loop goes through the harness's permission checks.
- Each engine runs with a stripped environment: its own vendor key, proxy and certificate settings, and a HOME beside the worktree. Host secrets and your real HOME never reach it. Codex keeps its login there too, so your own `~/.codex` is untouched.
- This is not an OS sandbox. Shell commands run in the worktree with your user's permissions. Bind to loopback, keep `workspace.roots` small, and read [docs/security.md](security.md).

## Configure

Copy [harness.example.toml](../harness.example.toml) to `harness.toml`. The settings that matter:

- `workspace.roots`: the only repositories the app will open.
- `workspace.check_command`: the verdict command. Defaults to `python -m pytest -q`. Use `npm test` or `go test ./...` for other stacks. It is split with `shlex` and never handed to a shell.
- `workspace.check_timeout`: seconds before a check counts as failed. Default 300.

See [.env.example](../.env.example) for every variable.

## Layout

```text
harness/
  agents.py         Claude Code, Codex, and OpenRouter engines behind one Agent interface
  runtime.py        the run: scan, drive the engine, verify, review, repair
  verification.py   runs the check command; the only source of `passed`
  review.py         software review gate plus the model reviewer from the other vendor
  isolation.py      worktree staging, fingerprints, apply
  authority.py      role and path checks every tool call goes through
  tools.py          the OpenRouter loop's tool set
  provider.py       deterministic, scripted, and OpenRouter providers
  triage.py         reads tests, TODOs, and commits for Suggest a fix
  app.py            FastAPI server and the web client in static/
  store.py          SQLite users, sessions, and durable runs
  auth.py           scrypt hashes, session tokens
  config.py         allowlisted roots, provider name, data dir
  accounts.py       operator-created accounts (`harness user add`)
  demo.py           sample-repo demo entry
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
