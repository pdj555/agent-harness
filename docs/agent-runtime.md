# Agent runtime

The runtime is a loop, not a workflow engine.

```text
queued -> running -> {completed | failed | stopped}
```

## Engines

| `HARNESS_PROVIDER` | Engine | Loop owner |
| --- | --- | --- |
| `claude` | Claude Code via the Claude Agent SDK | Anthropic |
| `codex` | Codex via the Codex SDK | OpenAI |
| `openrouter` | any OpenRouter model, on the harness tools | the harness |
| `deterministic` | scripted sample agent for tests and `harness demo` | the harness |

Claude and Codex run in their vendors' own coding agents. Everything else runs on the harness's
own loop. All engines implement `Agent.turn(prompt, ctx)` in
`harness/agents.py`; the runtime never learns which one it is driving.

Confinement per engine:

- **claude**: built-in tools only (`Read`, `Edit`, `Write`, `Glob`, `Grep`,
  `Bash`, `TodoWrite`). A `PreToolUse` hook denies any path outside the stage,
  git internals, and every call after stop. User and project Claude settings
  are not loaded.
- **codex**: Codex's own `workspace-write` sandbox rooted at the stage, with
  approvals set to deny.
- **openrouter**: the harness tools below, checked by `harness.authority`.

Each vendor agent runs with a replacement environment: its own key, proxy and
certificate settings, and a HOME beside the stage. Every other host variable is
blanked. Paths starting with `~` or containing `$` are denied by the Claude hook.

## Loop

1. Load the run, resolve the allowlisted repository, create a stage.
2. Hand the objective and the software scan to the engine. It works
   until it stops. Every tool use is recorded as an event; stop and stage
   safety are checked around each one.
3. **Ignore any claim of success** and run `run_checks`.
4. Run independent review against the diff and the check output.
5. If checks and review passed, optionally publish, then mark `completed`.
6. Otherwise hand the engine the check output or the reviewer's findings in
   the same session and let it repair, up to `max_repairs`. Then mark `failed`.

Every exit lands in a terminal status. A provider error, a check that times
out, or an unexpected exception inside the loop marks the run `failed` with a
blocker; nothing is left in `running` for the UI to poll forever.

Events recorded for the UI are actions, evidence, artifacts, decisions, and
results. They are not a chain of thought dump.

## Tools (harness loop)

| Tool | Principal | Helper | Reviewer |
| --- | --- | --- | --- |
| list_files, search, read_file, git_status, git_diff | yes | yes | yes |
| edit_file, run_shell | yes | yes | no |
| delegate | yes | no | no |

`delegate` runs a software helper that inspects the stage and returns evidence.
The user never manages that helper.

## Independent review

`harness.review.run_review` always writes `role: "reviewer"`. Software checks
the isolated diff first, not pytest's exit code:

- tests must still exist in the stage (Python, JavaScript, Go, and Ruby
  layouts are recognized)
- there must be an isolated change
- tests may not change unless an implementation file also changed

When software accepts the change and the checks passed, a model from the
other vendor reviews the diff with read-only access: Codex reviews Claude,
Claude reviews Codex, and OpenRouter runs are reviewed by Claude, else Codex.
`HARNESS_REVIEWER=claude|codex|none` overrides the pick; `HARNESS_REVIEWER_MODEL`
sets its model, so it never inherits the principal's `HARNESS_MODEL`. The review
diff includes new files and says so when it is truncated. The reviewer answers
`{"ship": bool, "findings": [...]}`; an unreadable answer or a reviewer error
blocks.

Completion requires **both** `verification.passed` and `review.passed`. Green
checks with no implementation change do not complete. The review summary is
never the principal's "done" text.

## Stop

`POST /api/runs/{id}/stop` sets `stop_requested`. Engines check the flag around
every model step and tool use; the Claude hook denies further tools and Codex
turns are interrupted. A mutating call after stop is rejected and recorded as
skipped. The store performs every run update as one locked read-modify-write,
so a runtime write in flight cannot overwrite a stop that arrived from the
HTTP thread.
