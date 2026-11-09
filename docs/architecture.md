# Architecture

The harness is a single Python package. One principal agent does the work; everything else gives it authority, isolation, and a record of the checks and review.

```text
browser  ->  auth  ->  app  ->  runtime  ->  agent (harness loop | Codex SDK)
                              |          ->  tools (authority)
                              |          ->  isolation (worktree)
                              |          ->  verification (pytest)
                              |          ->  review
                              '- - - - - ->  store (sqlite)
```

## Modules

| Module | Responsibility |
| --- | --- |
| `harness/auth.py` | scrypt hashes, session tokens |
| `harness/store.py` | users, sessions, runs, conversation log and session state |
| `harness/app.py` | HTTP API, static workspace |
| `harness/config.py` | allowlisted roots, provider, data dir |
| `harness/authority.py` | path and role checks |
| `harness/isolation.py` | Git worktree / copied git stage, publish |
| `harness/tools.py` | list, search, paged read, edit/create, shell, git, delegate |
| `harness/agents.py` | authority-checked model loop, Codex adapter, reviewer selection |
| `harness/anthropic.py` | streamed Messages API, strict tool schemas, cached history, structured review |
| `harness/provider.py` | model step protocol; deterministic and OpenRouter |
| `harness/runtime.py` | loop, stop, repair |
| `harness/verification.py` | checks run by the parent process |
| `harness/review.py` | software rules plus model reviewer |
| `harness/triage.py` | scans tests, TODOs, and recent commits to pick an objective |
| `harness/accounts.py` | accounts the operator creates |
| `harness/demo.py` | demo entry |

## Authority

Agents receive tools, not the host filesystem. Every call is checked against:

1. the caller's role (principal, helper, reviewer)
2. the isolated stage root
3. the stop flag, for mutating tools

The web client sends a `repo_id` from `workspace.roots`, never raw paths. Unknown ids are rejected.

## Isolation

Mutating work happens in a stage under `.harness/stages/<run_id>/`. A selected path counts as Git only when `git rev-parse --show-toplevel` equals that path. For a Git root, the stage is a detached worktree; otherwise the files are copied and initialized as Git inside the stage.

Subprocesses started inside a stage get `HOME` set to a sibling directory (`<run_id>.home`), not a folder inside the stage. Caches and profiles a command writes never appear as changes.

For a Git root, the overlay comes from `git ls-files`: files represented by HEAD are materialized by Git; only modified, added, renamed, or deleted paths are overlaid. Ignored credentials, dependency trees, and generated data are not copied into the stage.

Before tools, verification, review, and publishing use a stage, the runtime checks it for symlinks that escape the stage root. If one is found, the run is recorded as failed and the stored stage reference is disabled.

Each stage records its initial Git HEAD alongside file contents, symlink targets, and executable bits in a sibling baseline file outside the model's file-tool root. Moving the stage's HEAD, including commits or amends, fails the run. Edits must remain uncommitted so review and apply see the complete delta. Legacy baselines without the initial commit must be rerun.

## Verification

When the model stops, the runtime runs `run_checks(stage.root, python=stage.python(), timeout=...)`: the check command in the stage. `passed` comes from the process exit code. When the command runs pytest, the harness adds `--junitxml` pointing outside the stage, and exit 0 passes only if that report exists, shows at least one test that ran, and shows no failure or error. That catches these ways stage code can end pytest with status 0 without a passing run, as long as it does not also write the report itself: `os._exit(0)` in a conftest, a hook that resets `session.exitstatus`, `--collect-only` in the project's config, deselecting every test, skipping every test.

It does not close hooks that change results inside the pytest process, because the report is written by that same process: `pytest.exit(returncode=0)` called as soon as a test fails, a `pytest_runtest_makereport` wrapper that marks failures passed, skip, xfail or deselect of only the failing tests while one test still passes, a conftest that writes a passing report to the path in `config.option.xmlpath` and then exits. So this check stops a crude forced exit; a deliberate one is a change to the test infrastructure, and the review step has to catch it.

Pytest is recognized as a `pytest` or `py.test` executable, a Python interpreter running `-m pytest` after flags written as one token (`python -I -m pytest`; not `python -W error -m pytest` or `-mpytest`), `python -m coverage run -m pytest`, and any of those directly after `uv run`, `poetry run` or `pdm run`. A command that hides pytest in one string (`sh -c "pytest"`, `make test`, `tox`) is judged on its exit code alone, as are the unrecognized forms above. The operator chooses the command; stage code cannot. The harness's `--junitxml` overrides one in the project's own `addopts`, so that file is not written during checks, and a project that disables the plugin with `-p no:junitxml` fails verification.

The interpreter is the repository's own `.venv/bin/python` (or `venv/`) when one exists. Otherwise it is the harness interpreter. A suite that exceeds `workspace.check_timeout` (default 300 seconds) is a failed verification with the partial output, not an exception.

Repositories that are not pytest projects set `workspace.check_command`. The command is split with `shlex`, never handed to a shell, and runs with the same filtered environment.

Verification records a SHA-256 fingerprint of the Git baseline, the changed paths, and the contents, types, and modes of tracked and untracked files Git does not ignore. The fingerprint must stay stable through checks and review. Apply copies the stage into a temporary worktree, compares that frozen copy with the stored fingerprint, then publishes the copy. Before writing anything, apply checks every affected source path against the baseline: an overlapping source edit, a deletion, a collision with a new file, or a replaced parent path rejects the apply. Fingerprints survive restarts in the run's verification record.

Completion also requires an independent review record (`role: reviewer`) whose summary is not the principal's final text. Review recognizes test files in the Python, JavaScript, Go, and Ruby layouts: `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`, and under `tests/`, `test/`, `__tests__/`, or `spec/`.

## Provider boundary

`make_agent(name)` builds the engine for a run. Claude and OpenRouter use
`LoopAgent`, which wraps a `Provider` with `complete(messages, tools)`. Every
proposed tool call passes through `harness.tools.execute` and `harness.authority`.
The Anthropic client never executes tools. Codex remains a separate SDK adapter.
Tests inject `ScriptedProvider`; the demo uses `DeterministicProvider`.

The Anthropic provider streams responses, preserves the original assistant
blocks, groups parallel tool results into the next user message, and sends
strict tool schemas. Top-level ephemeral caching lets the API cache repeated
conversation prefixes. Token and cache usage counters are kept on the provider;
cache savings are not assumed. `HARNESS_MODEL` selects an explicit model ID.
An unsupported stop reason or malformed tool response fails the run.

`make_reviewer(name)` prefers a different vendor. When only Anthropic is
configured, it creates a fresh Claude reviewer using the same model or an
explicit reviewer model. The reviewer has a separate conversation, read-only
tools, and a strict JSON output schema. It sees the objective, check output,
and diff, and can read file sections for context. Same-model review is a
second check, not cross-vendor independence. Any listed defect blocks even if
the response also says `ship: true`.

Claude review of another vendor requires `HARNESS_REVIEWER_MODEL`; it never
inherits the other vendor's model ID. API credentials remain in the harness
process. Harness tool subprocesses receive no vendor keys.

The CLI calls the same runtime as the web app. `run` resolves an allowlisted
repository and always stages the result. `apply` loads the durable verification,
review, and fingerprint before calling `Stage.publish`. An informational
provider answer has no verification or review record and cannot be applied.

The Claude Agent SDK's separate execution path is gone: Claude runs through
the existing authority boundary, and its bundled CLI dependency is removed. The
before/after check is an Anthropic-only runtime fixture that repairs the seeded
bug, creates a regression test, runs real pytest, receives a fresh review, and
leaves the source unchanged until fingerprinted apply. A contradictory reviewer
approval still fails after tests pass. SDK HTTP/SSE fixtures check the request
shape without making paid API calls.

API design references: [tool loop](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works),
[strict tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use),
[structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
[prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons).

## Persistence

SQLite lives in `.harness/harness.db`. Run payloads include plan, events, files, diff, checks, review, verification, blockers, result, and the Codex SDK session id when available. The store also keeps an append-only event log, conversation messages, and session state. `LoopAgent` persists its messages and step count; Claude uses this conversation log through the Messages API rather than a vendor agent session id. These records do not promise recovery of a Codex process or an interrupted stage.
