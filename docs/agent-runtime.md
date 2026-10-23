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
| `deterministic` | scripted agent for tests and `harness demo` | the harness |

All engines implement `Agent.turn(prompt, ctx)` in `harness/agents.py`.


Confinement per engine:

- **claude**: the tools Claude Code ships (`Read`, `Edit`, `Write`, `Glob`, `Grep`,
  `Bash`, `TodoWrite`). A `PreToolUse` hook denies any path outside the stage,
  git internals, and every call after stop. Paths starting with `~` or containing
  `$` are denied too. User and project Claude settings are not loaded.
- **codex**: Codex's `workspace-write` sandbox rooted at the stage, with
  approvals denied.
- **openrouter**: the harness tools below, checked by `harness.authority`.

## Loop

1. Load the run, resolve the allowlisted repository, create a stage.
2. Hand the objective and the software scan to the engine.
3. Ignore any claim of success and run `run_checks`.
4. Run independent review against the diff and the check output.
5. If checks and review passed, publish, then mark `completed`.
6. Otherwise the engine repairs from the findings, up to `max_repairs`,
   then the run is marked `failed`.

## Tools (harness loop)

| Tool | Principal | Helper | Reviewer |
| --- | --- | --- | --- |
| list_files, search, read_file, git_status, git_diff | yes | yes | yes |
| edit_file, run_shell | yes | yes | no |
| delegate | yes | no | no |

`delegate` runs a helper that inspects the stage and returns evidence.

## Independent review

`harness.review.run_review` always writes `role: "reviewer"`. Software checks
the isolated diff first:

- tests must still exist in the stage
- there must be an isolated change
- tests may not change unless an implementation file also changed

A model from the other vendor reviews the diff and cannot edit it: Codex reviews Claude,
Claude reviews Codex, and OpenRouter runs are reviewed by Claude, else Codex.
`HARNESS_REVIEWER=claude|codex|none` overrides the pick; `HARNESS_REVIEWER_MODEL`
sets its model. The reviewer answers
`{"ship": bool, "findings": [...]}`; an unreadable answer or a reviewer error
blocks.

Completion requires both `verification.passed` and `review.passed`. The review summary is
never the principal's "done" text.

## Stop

`POST /api/runs/{id}/stop` sets `stop_requested`. Engines check the flag around
every step and tool use; the Claude hook denies further tools and Codex
turns are interrupted. A mutating call after stop is rejected and recorded.