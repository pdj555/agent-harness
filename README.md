# agent-harness

A local coding agent that can't mark its own work done.

You allowlist a Git repository and give it one objective. The agent reads the code and edits an isolated worktree. It runs the project's tests. A separate reviewer looks at the diff. The run completes only when the test command exits 0 and review passes, and your repository doesn't change until you click **Apply**.

The rule everything follows: the model proposes, software decides. There is no tool, field, or string the model can use to set `passed`.

[![ci](https://github.com/pdj555/agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/pdj555/agent-harness/actions/workflows/ci.yml)

## Try it in one minute

No API key. A scripted agent fixes a seeded bug in `examples/sample-repo`:

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

Run it twice. Each run resets the demo copy, so both should complete with a real diff.

## Run it on your own code

```bash
uv run harness user add <you> --password '<password>'
uv run harness repo add ~/code/your-repo
uv run harness serve
```

Open http://127.0.0.1:7465 and log in. Pick the repository, type an objective, and watch the plan, tool calls, diff, and test output stream in. If you don't know what to ask for, **Suggest a fix** reads the tests, open TODOs, and recent commits and picks something.

When the run is green you get a diff, the check output, and the review. **Apply** copies exactly the files that were verified. **Stop** ends mutating work at the next tool boundary.

## How a run works

1. The repository is staged in a detached Git worktree under `.harness/stages/<run>/`. Ignored files (`.env`, `node_modules`, caches) never enter it.
2. The principal agent works with a small tool set: list, search, read, edit, shell, git status and diff, and a read-only helper it can delegate to.
3. When the agent stops calling tools, the harness ignores whatever it said and runs the project's check command itself. The exit code is the verdict.
4. An independent review checks the diff. Tests still exist, something actually changed, and tests didn't change without an implementation change.
5. If checks or review fail, the output goes back to the agent for repair, up to `max_repairs` times. Then the run fails.
6. Verification fingerprints the staged tree. Apply re-checks that fingerprint before copying, so the files that land are the files that passed.

Every exit is terminal: `completed`, `failed`, or `stopped`. A provider error or a hung test suite fails the run with a blocker. Nothing sits in `running` forever.

## What it does not protect you from

This is not an OS sandbox. A live model that can run Python can reach the host as your user. The allowlist and the worktree are the boundary, not the prompt. Bind to loopback, keep `workspace.roots` small, and read [docs/security.md](docs/security.md) before pointing it at anything you care about.

## Models

`harness serve` picks a live provider from the environment. Pin one with `HARNESS_PROVIDER` and it fails closed instead of falling back.

| `HARNESS_PROVIDER` | Needs | Default model |
| --- | --- | --- |
| `ollama` | a running local Ollama | `gpt-oss:20b` if installed |
| `ollama-cloud` | `OLLAMA_API_KEY` | `gpt-oss:120b` |
| `openai` | `OPENAI_API_KEY` | `gpt-5.6-luna` |
| `xai` | `XAI_API_KEY` | `grok-4.6` |

Override the model with `HARNESS_MODEL` and the endpoint with `HARNESS_API_BASE`. See [.env.example](.env.example).

## Configure

Copy [harness.example.toml](harness.example.toml) to `harness.toml`. The settings that matter:

- `workspace.roots`: the only repositories the app will open.
- `workspace.check_command`: the verdict command. Defaults to `python -m pytest -q`. Use `npm test` or `go test ./...` for other stacks. It's split with `shlex` and never handed to a shell.
- `workspace.check_timeout`: seconds before a check counts as failed. Default 300.

If the repository has its own `.venv`, checks and `python` in shell calls use that interpreter, so the project's real dependencies get tested.

## Layout

```text
harness/
  runtime.py        the principal loop: plan, act, verify, review, repair
  verification.py   runs the check command; the only source of `passed`
  review.py         independent review record
  isolation.py      worktree staging, fingerprints, apply
  authority.py      role and path checks every tool call goes through
  tools.py          the agent's tool set
  provider.py       deterministic, scripted, and OpenAI-compatible providers
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
