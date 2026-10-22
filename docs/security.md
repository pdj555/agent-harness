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

Claude uses the Messages API in the harness process and the same tools as
OpenRouter. No Anthropic credential enters the command environment. Codex runs
its own shell in a `workspace-write` sandbox rooted at the stage, with approvals
denied. Its subprocess gets a replacement environment: every host variable is
blanked except PATH, locale, proxy and certificate settings, HOME points beside
the stage, and only its vendor key is present. Codex keeps its login under that
HOME, never in `~/.codex`. Codex's sandbox restricts writes to the stage but
still allows reads elsewhere.

This is not an OS sandbox. A live model that can run Python can still touch
the host as the same user. Treat live vendors as code execution. Bind to
loopback. Keep `workspace.roots` small.

Escaping symlinks are rejected when a stage is created and before tools,
verification, review, and publishing use the stage. If a shell command leaves
one behind, the run fails before those pipeline steps and the stored stage
reference is disabled.

## Verification is not a tool the model owns

Each stage records its starting file contents, symlink targets, and executable
bits in a sibling baseline file outside the model's file-tool root. Verification
binds that baseline along with the staged contents. Applying a verified delta
checks every affected source path before writing any file. An overlapping source
edit, deletion, new-file collision, or replaced parent path rejects the apply;
unrelated edits and files already equal to the verified result are preserved.
Runs created before baseline tracking must be rerun before they can be applied.

Harness applies to the same checkout share an exclusive lock in Git's common
directory (or `.harness` for non-Git sources), including across harness data
directories. A competing apply fails with a retry message. A process killed
without cleanup can leave a lock file; remove it only after confirming no apply
is active. This lock coordinates harness processes, not editors or other tools.
Preflight is not a filesystem transaction: external writers racing after the
check and I/O failures during copying can still produce a partial apply.

The model may run tests via `run_shell`. That output is evidence in the
transcript. The completion verdict is a separate parent-run of pytest in
`harness.verification`. A string such as `VERIFICATION_PASSED` in model text
has no effect.

## What this is not

It is not a multi-tenant cloud, an OS sandbox profile, or a substitute for
reviewing the diff. It is a local operator tool with explicit, auditable
limits. See [SECURITY.md](../SECURITY.md).
