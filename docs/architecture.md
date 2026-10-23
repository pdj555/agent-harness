# Architecture

The harness is a single Python package. It is not a platform of many agents. One principal agent does the work. Everything else gives that agent authority, isolation, and proof.

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
| `harness/store.py` | users, sessions, durable runs |
| `harness/app.py` | HTTP API and static workspace |
| `harness/config.py` | allowlisted roots, provider name, data dir |
| `harness/authority.py` | path and role checks |
| `harness/isolation.py` | Git worktree / copied git stage, publish |
| `harness/tools.py` | list, search, read, edit, shell, git, delegate |
| `harness/agents.py` | engines: Claude Agent SDK, Codex SDK, harness loop; reviewer pick |
| `harness/provider.py` | protocol for one model step at a time, for the harness loop; deterministic and OpenRouter |
| `harness/runtime.py` | principal loop, stop, repair |
| `harness/verification.py` | checks run by the parent process |
| `harness/review.py` | software review gate plus the model reviewer from the other vendor |
| `harness/triage.py` | reads tests, TODO markers, and recent commits for Suggest a fix |
| `harness/accounts.py` | accounts the operator creates (`harness user add`) |
| `harness/demo.py` | demo entry for the sample repository |

## Authority

Agents do not receive the host filesystem. They receive tools, and every call is checked against:

1. the caller's role (principal, helper, reviewer)
2. the isolated stage root
3. the stop flag, for mutating tools

The web client never sends raw paths. It sends a `repo_id` from `workspace.roots`. Unknown ids are rejected.

## Isolation

Mutating work happens in a stage directory under `.harness/stages/<run_id>/`. A selected path counts as Git only when `git rev-parse --show-toplevel` equals that path. A nested folder inside some other clone is copied, not attached to the parent worktree. `ensure_git_repo` uses the same root check, so a stage under `.harness/` still gets its own repository and a real diff even when that directory sits inside another clone. For a Git root, the stage is a detached worktree; otherwise the files are copied and initialized as Git inside the stage, so diff and status still work. The source tree is unchanged until `Stage.publish()` copies the verified delta. The web UI exposes publish as an explicit step after verification. `changed_files` lists untracked files one per entry, so a new directory publishes its contents rather than being skipped.

Subprocesses started inside a stage get `HOME` set to a sibling directory (`<run_id>.home`), not a folder inside the stage. Caches and profiles a command writes never appear as changes, never reach review, and never publish.

For a Git root, the overlay of the working tree comes from `git ls-files`: files already represented by HEAD are materialized by Git, and only modified, added, renamed, or deleted paths are overlaid from the working tree. An earlier version overlaid every changed file; overlaying only the changed paths is a recent optimization. Ignored credentials, dependency trees, and generated data are not copied into the stage. The copy fallback also excludes env files that look like credentials, dependency directories, and harness state. If a Git repository tracks an env credential file or leaves one unignored, staging fails with one action: add that file to `.gitignore`. Example, sample, and template env files remain available.

Before tools, verification, review, and publishing use a stage, the runtime checks it for symlinks that escape the stage root. If one is found, the run is recorded as failed and the stored stage reference is disabled instead of the pipeline continuing.

## Verification

When the model stops proposing tool calls, the runtime runs `run_checks(stage.root, python=stage.python(), timeout=...)`. That function executes the check command in the stage with a filtered environment. The boolean `passed` comes from the process exit code. There is no parameter for a model claim.

The interpreter is the repository's own `.venv/bin/python` (or `venv/`) when one exists, so the project's dependencies are what get tested. Otherwise it is the harness interpreter. The same choice applies when the agent runs `python` through `run_shell`. A suite that exceeds `workspace.check_timeout` (default 300 seconds) is a failed verification with the partial output, not an exception.

Repositories that are not pytest projects set `workspace.check_command` (for example `npm test` or `go test ./...`). The command is split with `shlex`, never handed to a shell, and runs with the same filtered environment. Its exit code is the verdict. The model cannot see or change this setting.

Verification records a SHA-256 fingerprint of the Git baseline, the changed paths, and the contents, types, and modes of tracked files and of untracked files Git does not ignore. The fingerprint must stay stable through checks and review. Each stage also records its starting file contents, symlink targets, and executable bits in a baseline file beside the stage, outside the root the model's file tools can reach. Apply copies the stage into a temporary worktree, compares that frozen copy with the stored fingerprint, then publishes the copy, so a later edit to the original stage cannot replace the checked files during copying. Before writing anything, apply checks every affected source path against the baseline: an overlapping source edit, a deletion, a collision with a new file, or a replaced parent path rejects the apply, while unrelated edits and files already equal to the verified result are preserved. Applies to the same checkout share an exclusive lock in Git's common directory, so two harness processes cannot publish into it at once. The preflight is not a filesystem transaction: a writer racing in after the check, or an I/O failure during copying, can still leave a partial apply. Fingerprints survive restarts in the run's verification record; older runs without one must be rerun. Ignored caches and file timestamps are excluded. None of this prevents concurrent source edits or turns the local process into an OS sandbox.

Completion also requires an independent review record (`role: reviewer`) whose summary is not the principal's final text. Review recognizes test files in the common Python, JavaScript, Go, and Ruby layouts: `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`, and anything under `tests/`, `test/`, `__tests__/`, or `spec/`.

## Provider boundary

`make_agent(name)` builds the engine for a run. Claude and Codex run their vendors' own agent loops; any other model runs the harness loop, which wraps a `Provider` with `complete(messages, tools)`. Tests inject `ScriptedProvider`; the demo uses `DeterministicProvider`. `make_reviewer(name)` picks the other vendor to review the diff. Credentials stay in environment variables, and each agent's subprocess sees only its own vendor's key.

## Persistence

SQLite lives in `.harness/harness.db`. Run payloads include plan, events, files, diff, checks, review, verification, blockers, and result. History is per user.
