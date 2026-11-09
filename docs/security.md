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
| Environment | Holds API keys in the harness process. Tool subprocesses receive no provider keys; the Codex subprocess receives only its vendor key. Events redact keys. |

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

Claude uses the Messages API in the harness process and the same tools as
OpenRouter. No Anthropic credential enters the command environment. Codex runs
its own shell in a `workspace-write` sandbox rooted at the stage, with approvals
denied. Its subprocess gets a replacement environment: every host variable is
blanked except PATH, locale, proxy and certificate settings, HOME points beside
the stage, and only its vendor key is present. Codex keeps its login under that
HOME, never in `~/.codex`. Codex's sandbox restricts writes to the stage but
still allows reads elsewhere.

This is not an OS sandbox. A live model that can run Python can touch the host as the same user. Bind to
loopback. Keep `workspace.roots` small.

Escaping symlinks are rejected when a stage is created and before the
pipeline uses it. If a shell command leaves one behind, the run fails before
those pipeline steps and the stored stage reference is disabled.

## Verification is not a tool the model owns

Each stage records its starting Git commit, file contents, symlink targets, and
executable bits in a sibling baseline file outside the model's file-tool root.
Moving the stage's HEAD, including committing or amending changes, fails the run;
edits must remain uncommitted so review and apply see the complete delta.
Verification binds that baseline along with the staged contents. Applying a
verified delta checks every affected source path before writing any file. An overlapping source
edit, deletion, new-file collision, or replaced parent path rejects the apply;
unrelated edits and files already equal to the verified result are preserved.
Runs created without a recorded starting commit must be rerun before they can be applied.

Applies to the same checkout share an exclusive lock in Git's common
directory (or `.harness` when the source is not Git). A process killed can leave a lock file; remove it after confirming no apply is active.
Preflight is not a filesystem transaction: writers racing after the check and I/O failures can produce a partial apply.

The model may run tests via `run_shell`. Only `harness.verification.run_checks` sets the completion check verdict. For pytest, it requires exit 0 and a JUnit report outside the stage showing at least one test ran and no failures or errors. Stage code still runs inside pytest and can alter results or forge that report; the [architecture notes](architecture.md#verification) describe the gaps the review step must catch. A string such as `VERIFICATION_PASSED` in model text has no effect.
