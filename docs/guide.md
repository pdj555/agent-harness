# agent-harness guide

See the [README](../README.md) and the [research summary](research.md).

## Run it on your own code

```bash
export ANTHROPIC_API_KEY=...   # OPENAI_API_KEY for cross-vendor review
uv run harness user add <you> --password '<password>'
uv run harness repo add ~/code/your-repo
uv run harness serve
```

Open http://127.0.0.1:7465, log in, pick the repository, and type an objective. **Suggest a fix** reads the tests, TODOs, and recent commits and picks an objective.

`POST /api/signup` is refused; the operator creates each account, and passwords are stored as scrypt hashes.

## Engines

| `HARNESS_PROVIDER` | Runs | Needs |
| --- | --- | --- |
| `claude` | Claude Code, via the Claude Agent SDK | `ANTHROPIC_API_KEY` |
| `codex` | Codex, via the Codex SDK | `OPENAI_API_KEY` or `CODEX_API_KEY` |
| `openrouter` | Any OpenRouter model | `OPENROUTER_API_KEY` and `HARNESS_MODEL` |

Leave it unset and the first key found wins. `HARNESS_MODEL` picks the model.

## Review

Every diff that passes the tests is reviewed:

- Software rules run first: tests must still exist, an implementation file must have changed, and the diff is checked for new skip markers, deleted tests, and a `conftest.py` that replaces project code at import. The [monitor eval](evals.md) measures them at 6 of 27 attacks caught.
- Then a model from the other vendor reads the diff. Claude's work is reviewed by Codex, and Codex's by Claude. OpenRouter runs are reviewed by Claude, else Codex.

A block goes back to the agent as findings, in the same session. A reviewer that crashes or answers in anything but a verdict blocks the change. With only one vendor's key configured, review is software only.

## How a run works

1. Pick an allowlisted repository and state the objective; software scans tests, TODO markers, and recent commits first.
2. The agent works in a Git worktree under `.harness/stages/<run>/`, staged from tracked files and untracked files Git does not ignore. `.env`, `node_modules`, and caches never enter it.
3. The project's own checks run after the agent stops. Their exit code is the verdict; the agent's "done" is ignored.

Every exit is terminal: `completed`, `failed`, or `stopped`.

## Guarantees and limits

- Models propose. The project's checks and independent review decide.
- Claude's file tools are confined to the worktree by a hook that denies paths outside it, git internals, and every call after stop. Codex runs in its own workspace sandbox.
- This is not an OS sandbox. Shell commands run in the worktree with your user's permissions. Bind to loopback, keep `workspace.roots` small, and read [docs/security.md](security.md).

## Configure

Copy [harness.example.toml](../harness.example.toml) to `harness.toml`.

- `workspace.roots`: the only repositories the app will open.
- `workspace.check_command`: the verdict command. Defaults to `python -m pytest -q`. Use `npm test` or `go test ./...` for other stacks.

## Layout

```text
harness/
  agents.py         engines, one Agent interface
  runtime.py        the run: scan, drive, verify, review, repair
  verification.py   check command; only source of `passed`
  review.py         software gate plus model reviewer
  isolation.py      worktree staging, fingerprints, apply
  authority.py      role and path checks
  tools.py          OpenRouter loop tools
  provider.py       deterministic, scripted, OpenRouter providers
  triage.py         Suggest a fix scanning
  app.py            FastAPI server and web client
  store.py          SQLite users, sessions, runs
  auth.py           scrypt hashes, session tokens
  config.py         roots, provider, data dir
  accounts.py       operator-created accounts
  demo.py           sample-repo demo entry
docs/, examples/ (the seeded sample repository), skills/ (guidance for agents working on this repo)
```

## Develop

```bash
uv sync --extra dev
uv run pytest
uv run ruff check harness tests scripts
uv run python scripts/scan_secrets.py
```

CI runs `pip-audit`, builds the wheel, scans it for secrets, and runs the demo from the installed wheel.
