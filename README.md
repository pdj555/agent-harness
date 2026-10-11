# Agent Harness

A local coding agent you hire against an outcome, not a chat pane.

You allowlist a real Git repository, state the change that makes money, and watch one principal agent inspect the code, edit in an isolated worktree, run the project's tests, and take independent review. **Apply is explicit.** An agent saying "done" is not completion. Software verifies.

Cursor is a copilot in your editor. This is an operator: isolated execution, proof, then a decision to land the diff.

## How it works

1. **Scope.** You pick an allowlisted repository and state the objective. Software scans tests, TODO markers, and recent commits first.
2. **Isolate.** All mutating work happens in a Git worktree. The source tree does not move until you apply a verified delta.
3. **Verify.** The agent runs the project's checks and takes independent review. Pytest and review decide; models propose.
4. **Apply.** You land the diff. Explicit, deliberate, reversible.

The browser cannot create accounts or open arbitrary files. Stop ends mutating work. History is durable.

## Run it

Cheapest live path: local Ollama, already installed.

```bash
# ollama serve && ollama pull gpt-oss:20b
uv sync --extra dev
uv run harness user add preston --password 'choose-your-own'
uv run harness repo add /path/to/the/repo/that/prints/money
uv run harness serve
```

Open `http://127.0.0.1:7465`, log in, pick that repository, and click **Next dollar**. Software scans tests, TODO markers, and recent commits; the agent writes a live plan from that evidence, ships in isolation, and stops when checks pass. You can still type a specific objective.

No API key, no cost:

```bash
uv run harness demo
```

The demo runs a scripted sample agent so CI needs no vendor. It repairs a known defect in `examples/sample-repo`; it is not the product.

## Model routing

Serve prefers **local Ollama** when it is running (`gpt-oss:20b` if installed, never a `:cloud` tag unless you set `HARNESS_MODEL`). That is $0.

- **Ollama Cloud:** set `OLLAMA_API_KEY` in `.env` (runs `gpt-oss:120b` unless you set `HARNESS_MODEL`). Local $0 stays available with `HARNESS_PROVIDER=ollama`.
- **OpenAI:** pin `OPENAI_API_KEY` and `HARNESS_PROVIDER=openai`. Daily driver is **GPT-5.6 Luna** at `reasoning_effort=xhigh` ($0.20 / $1.20 per 1M). `HARNESS_MODEL=gpt-5.6-sol` for the close.
- **xAI:** pin `XAI_API_KEY` and `HARNESS_PROVIDER=xai` (default `grok-4.6`), or override with `HARNESS_MODEL`.

## Configure

Copy [harness.example.toml](harness.example.toml) to `harness.toml` to bind a different port, add roots, extend `check_timeout`, or set a `check_command` other than pytest (`npm test`, `go test ./...`). The command's exit code is the verdict. Add repositories:

```bash
uv run harness repo add ~/code/your-repo
```

## Why this is not a chat demo

- Models propose. Pytest and independent review decide.
- Mutating work happens in a Git worktree. The source tree does not move until you apply a verified delta.
- The browser cannot create accounts or open arbitrary files.
- Stop ends mutating work. History is durable.

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
