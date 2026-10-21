# Security model

## Trust boundaries

| Surface | Trust |
| --- | --- |
| Browser | Untrusted. Cannot pick arbitrary files. Talks JSON to loopback HTTP. |
| Session cookie | Bearer of identity. Random, hashed at rest, httpOnly. |
| Model | Untrusted. Proposes tool calls and text. Cannot set verdicts. |
| Tools | Trusted code. Enforce role, path, and stop. |
| Stage | The only tree the model may edit. |
| Source repository | Read-only until a verified publish. |
| Environment | Holds API keys. Stripped from tool subprocesses, replaced for vendor agents (each keeps only its own key), and redacted from events. |

Git stages include tracked and nonignored untracked files only. Files already
represented by HEAD are materialized by Git; only modified,
added, renamed, or deleted paths are overlaid from the working tree. Ignored local
credentials and dependency trees do not enter the agent worktree. The non-Git
copy fallback excludes credential-like env files, dependency directories,
caches, and harness state. A tracked or unignored `.env`, `.env.local`, or
similar credential file makes Git staging fail closed; `.env.example`,
`.env.sample`, and `.env.template` are allowed.

## Authentication

- The browser cannot create accounts. `POST /api/signup` is refused.
  Request access by email, then the operator runs `harness user add`.
- Passwords are stored as `scrypt$<salt>$<digest>`.
- Login verifies with `hmac.compare_digest`.
- Logout deletes the hashed session and the cookie.
- `/api/me`, `/api/repos`, `/api/runs` require a valid session. Bodies on 401
  contain only `{"error": "authentication required"}`.

## Path restriction

`resolve_in_root` rejects:

- empty paths
- absolute paths outside the stage
- `..` escapes after `Path.resolve()`
- direct `.git` access through file tools

Git status and diff run with `cwd` set to the stage.

## Shell

`run_shell` uses `shlex.split` (no `shell=True`), forces `cwd` to the stage,
replaces `python`/`python3` with the repository's own virtual environment
interpreter when it has one (else the harness interpreter), and passes a
filtered environment without provider credentials. `HOME` points at a
directory beside the stage so nothing a command writes there enters the diff.
The timeout is `workspace.check_timeout` (default 300 seconds); a timed-out
command is reported to the model as a failure. Git subprocesses use a filtered
environment and `core.hooksPath=/dev/null`.

That describes the harness loop. The vendor engines run their own shells:
Claude Code's `Bash` runs in the stage, behind a `PreToolUse` hook that denies
paths outside it, git internals, and anything after stop. Codex runs in its own
`workspace-write` sandbox rooted at the stage, with approvals denied. Each
engine's subprocess gets a replacement environment: every host variable is
blanked except PATH, locale, proxy and certificate settings, HOME points beside
the stage, and only its own vendor key is present because the engine needs it
to call its model. Codex keeps its login under that HOME, never in `~/.codex`.
Codex's sandbox restricts writes to the stage but still allows reads elsewhere.

This is not an OS sandbox. A live model that can run Python can still touch
the host as the same user. Treat live vendors as code execution. Bind to
loopback. Keep `workspace.roots` small.

Escaping symlinks are rejected when a stage is created and before tools,
verification, review, and publishing use the stage. If a shell command leaves
one behind, the run fails before those pipeline steps and the stored stage
reference is disabled.

## Verification is not a tool the model owns

The model may run tests via `run_shell`. That output is evidence in the
transcript. The completion verdict is a separate parent-run of pytest in
`harness.verification`. A string such as `VERIFICATION_PASSED` in model text
has no effect.

## What this is not

It is not a multi-tenant cloud, an OS sandbox profile, or a substitute for
reviewing the diff. It is a local operator tool with explicit, auditable
limits. See [SECURITY.md](../SECURITY.md).
