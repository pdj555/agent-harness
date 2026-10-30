# Architecture

The harness is a single Python package. One principal agent does the work; everything else gives it authority, isolation, and proof.

```text
browser  ->  auth  ->  app  ->  runtime  ->  agent (claude | codex | harness loop)
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
| `harness/store.py` | users, sessions, runs |
| `harness/app.py` | HTTP API, static workspace |
| `harness/config.py` | allowlisted roots, provider, data dir |
| `harness/authority.py` | path and role checks |
| `harness/isolation.py` | Git worktree / copied git stage, publish |
| `harness/tools.py` | list, search, read, edit, shell, git, delegate |
| `harness/agents.py` | Claude Agent SDK, Codex SDK, harness loop; reviewer pick |
| `harness/provider.py` | model step protocol; deterministic and OpenRouter |
| `harness/runtime.py` | loop, stop, repair |
| `harness/verification.py` | checks run by the parent process |
| `harness/review.py` | software gate plus model reviewer |
| `harness/triage.py` | Suggest a fix scanning |
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

## Verification

When the model stops, the runtime runs `run_checks(stage.root, python=stage.python(), timeout=...)`: the check command in the stage. `passed` comes from the process exit code. When the command runs pytest, the harness adds `--junitxml` pointing outside the stage, and exit 0 passes only if that report exists, shows at least one test that ran, and shows no failure or error. That closes the ways stage code can end pytest with status 0 without a passing run: `os._exit(0)` in a conftest, a hook that resets `session.exitstatus`, `--collect-only` in the project's config, deselecting every test, skipping every test.

It does not close hooks that change results inside the pytest process, because the report is written by that same process: `pytest.exit(returncode=0)` called as soon as a test fails, a `pytest_runtest_makereport` wrapper that marks failures passed, xfail or deselect of only the failing tests, a conftest that rewrites the report. Those are changes to test infrastructure, and the review gate has to catch them.

Pytest is recognized as a `pytest` or `py.test` executable, a Python interpreter running `-m pytest` after its own flags (`python -I -m pytest`), `python -m coverage run -m pytest`, and any of those directly after `uv run`, `poetry run` or `pdm run`. A command that hides pytest in one string (`sh -c "pytest"`, `make test`, `tox`) keeps exit-code-only semantics. The harness's `--junitxml` overrides one in the project's own `addopts`, so that file is not written during checks, and a project that disables the plugin with `-p no:junitxml` fails verification.

The interpreter is the repository's own `.venv/bin/python` (or `venv/`) when one exists. Otherwise it is the harness interpreter. A suite that exceeds `workspace.check_timeout` (default 300 seconds) is a failed verification with the partial output, not an exception.

Repositories that are not pytest projects set `workspace.check_command`. The command is split with `shlex`, never handed to a shell, and runs with the same filtered environment.

Verification records a SHA-256 fingerprint of the Git baseline, the changed paths, and the contents, types, and modes of tracked and untracked files Git does not ignore. The fingerprint must stay stable through checks and review. Apply copies the stage into a temporary worktree, compares that frozen copy with the stored fingerprint, then publishes the copy. Before writing anything, apply checks every affected source path against the baseline: an overlapping source edit, a deletion, a collision with a new file, or a replaced parent path rejects the apply. Fingerprints survive restarts in the run's verification record.

Completion also requires an independent review record (`role: reviewer`) whose summary is not the principal's final text. Review recognizes test files in the Python, JavaScript, Go, and Ruby layouts: `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`, and under `tests/`, `test/`, `__tests__/`, or `spec/`.

## Provider boundary

`make_agent(name)` builds the engine for a run. Claude and Codex run their vendors' own agent loops; any other model runs the harness loop, which wraps a `Provider` with `complete(messages, tools)`. Tests inject `ScriptedProvider`; the demo uses `DeterministicProvider`. `make_reviewer(name)` picks the other vendor to review the diff.

## Persistence

SQLite lives in `.harness/harness.db`. Run payloads include plan, events, files, diff, checks, review, verification, blockers, result.
