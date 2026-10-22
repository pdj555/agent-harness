# Architecture

The harness is a single Python package with hard boundaries. It is not a
platform of many agents. One principal agent does the work. Everything else
exists to give that agent authority, isolation, and proof.

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
| `harness/provider.py` | model-step protocol for the harness loop; deterministic and OpenRouter |
| `harness/runtime.py` | principal loop, stop, repair |
| `harness/verification.py` | parent-run checks |
| `harness/review.py` | software review gate plus the cross-vendor model reviewer |
| `harness/triage.py` | reads tests, TODO markers, and recent commits for Suggest a fix |
| `harness/accounts.py` | operator-created accounts (`harness user add`) |
| `harness/demo.py` | sample-repo demo entry |

## Authority

Agents do not receive the host filesystem. They receive tools. Each call is
checked against:

1. the caller's role (principal, helper, reviewer)
2. the isolated stage root
3. the stop flag, for mutating tools

The web client never sends raw paths. It sends a `repo_id` from
`workspace.roots`. Unknown ids are rejected.

## Isolation

Mutating work happens in a stage directory under `.harness/stages/<run_id>/`.
A selected path is treated as Git only when `git rev-parse --show-toplevel`
equals that path. Nested folders inside some other clone are copied, not
attached to the parent worktree. `ensure_git_repo` uses the same root check,
so a stage under `.harness/` still gets its own repository and a real diff
even when that directory sits inside another clone. If the path is a Git root, the stage is a
detached Git worktree. Otherwise the files are copied and initialized as Git
inside the stage so diff and status still work. The source tree is unchanged
until `Stage.publish()` copies the verified delta. The web UI exposes publish
as an explicit step after verification. `changed_files` lists untracked files
one per entry, so a new directory publishes its contents rather than being
skipped.

Subprocesses started inside a stage get `HOME` set to a sibling directory
(`<run_id>.home`), not a folder inside the stage. Caches and profiles a
command writes never appear as changes, never reach review, and never publish.

For a Git root, the working-tree overlay comes from `git ls-files`: tracked
files plus nonignored untracked files. Ignored credentials, dependency trees,
and generated data are not copied into the stage. The copy fallback also
excludes credential-like env files, dependency directories, and harness state.
If a Git repository tracks or leaves an env credential file unignored, staging
fails with one action: add that file to `.gitignore`. Example, sample, and
template env files remain available.

## Verification

When the model stops proposing tool calls, the runtime runs
`run_checks(stage.root, python=stage.python(), timeout=...)`. That function
executes pytest in the stage with a filtered environment. The boolean `passed`
comes from the process exit code. There is no parameter for a model claim.

The interpreter is the repository's own `.venv/bin/python` (or `venv/`) when
one exists, so the project's dependencies are what get tested. Otherwise it is
the harness interpreter. The same choice applies when the agent runs `python`
through `run_shell`. A suite that exceeds `workspace.check_timeout` (default
300 seconds) is a failed verification with the partial output, not an exception.

Repositories that are not pytest projects set `workspace.check_command`
(for example `npm test` or `go test ./...`). The command is split with
`shlex`, never handed to a shell, and runs with the same filtered environment.
Its exit code is the verdict. The model cannot see or change this setting.

The runtime validates stage safety before verification, independent review,
and publishing. If an escaping symlink is detected, it records a failed run
and disables the stored stage reference instead of continuing the pipeline.

Verification also records a SHA-256 fingerprint of the Git baseline, changed
paths, and tracked/nonignored file contents, types, and modes. The fingerprint
must remain stable through checks and review. Apply copies the stage into a
temporary worktree, compares that frozen copy with the stored fingerprint,
then publishes the copy. A later edit to the original stage cannot replace
the checked files during copying. Fingerprints survive restarts in the run's
verification record; older runs without one must be rerun. Ignored caches and
file timestamps are excluded. This does not prevent concurrent source edits
or turn the local process into an OS sandbox.

Completion also requires an independent review record (`role: reviewer`) whose
summary is not the principal's final text. Review recognizes test files in the
common Python, JavaScript, Go, and Ruby layouts: `test_*`, `*_test.*`,
`*.test.*`, `*.spec.*`, and anything under `tests/`, `test/`, `__tests__/`, or
`spec/`.

## Provider boundary

`make_agent(name)` builds the engine for a run. Claude and Codex run their
vendors' own agent loops; any other model runs the harness loop, which wraps a
`Provider` with `complete(messages, tools)`. Tests inject `ScriptedProvider`, the
demo uses `DeterministicProvider`. `make_reviewer(name)` picks the other vendor
to review the diff. Credentials stay in environment variables, and each agent's
subprocess sees only its own vendor's key.

## Persistence

SQLite lives in `.harness/harness.db`. Run payloads include plan, events,
files, diff, checks, review, verification, blockers, and result. History is
per user.
