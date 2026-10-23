# Security model

## Trust boundaries

| Surface | Trust |
| --- | --- |
| Browser | Untrusted. Cannot pick arbitrary files. Talks JSON to loopback HTTP. |
| Session cookie | Bearer of identity. Random, hashed at rest, httpOnly. |
| Model | Untrusted. Proposes tool calls and text. Cannot set verdicts. |
| Tools | Trusted code. Enforce role, path, and stop. |
| Stage | The only tree the model may edit. |
| Source repository | Read only until a verified publish. |
| Environment | Holds API keys. Stripped from tool subprocesses, replaced for vendor agents (each keeps its own key), redacted from events. |

Git stages include tracked files and untracked files that are not ignored. Ignored credentials and dependency trees do not enter the worktree. The copy fallback, for sources that are not Git, excludes env files that look like credentials, dependency directories, caches, and harness state. A tracked or unignored `.env`, `.env.local`, or
similar credential file makes Git staging fail; `.env.example`,
`.env.sample`, and `.env.template` are allowed.

## Authentication

- The operator runs `harness user add`.
- Passwords are stored as `scrypt$<salt>$<digest>`.
- Login verifies with `hmac.compare_digest`.
- `/api/me`, `/api/repos`, `/api/runs` require a valid session. Bodies on 401 contain `{"error": "authentication required"}`.

## Path restriction

`resolve_in_root` rejects:

- empty paths
- absolute paths outside the stage
- `..` escapes after `Path.resolve()`
- direct `.git` access

## Shell

`run_shell` uses `shlex.split` (no `shell=True`), forces `cwd` to the stage,
replaces `python`/`python3` with the repository's venv
interpreter (else the harness's), and passes a
filtered environment without provider credentials.
`workspace.check_timeout` (default 300 seconds) bounds commands; timeouts are reported as failures.

The vendor engines run their own shells: Claude Code's `Bash` in the stage,
behind its `PreToolUse` hook; Codex in its `workspace-write` sandbox, approvals
denied. Each engine's subprocess gets a replacement environment: every host
variable blanked except PATH, locale, proxy and certificate settings, HOME
beside the stage, and only its own vendor key. Codex keeps its login under
that HOME, never in `~/.codex`.

This is not an OS sandbox. A live model that can run Python can touch the host as the same user. Bind to
loopback. Keep `workspace.roots` small.

Escaping symlinks are rejected when a stage is created and before the
pipeline uses it. If a shell command leaves one behind, the run fails before
those pipeline steps and the stored stage reference is disabled.

## Verification is not a tool the model owns

Each stage records its file contents, symlink targets, and executable bits in a baseline file outside the model's reach. An overlapping source
edit, deletion, collision with a new file, or replaced parent path rejects the apply;
unrelated edits and files already equal to the result are preserved.
Runs created before baseline tracking must be rerun.

Applies to the same checkout share an exclusive lock in Git's common
directory (or `.harness` when the source is not Git). A process killed can leave a lock file; remove it after confirming no apply is active.
Preflight is not a filesystem transaction: writers racing after the check and I/O failures can produce a partial apply.

The model may run tests via `run_shell`. The completion verdict is a run of pytest by the parent process. A string such as `VERIFICATION_PASSED` in model text
has no effect.
