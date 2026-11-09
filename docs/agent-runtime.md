# Agent runtime

The runtime is a loop, not a workflow engine.

```text
queued -> running -> {completed | failed | stopped}
```

## Engines

| `HARNESS_PROVIDER` | Engine | Loop owner |
| --- | --- | --- |
| `claude` | Anthropic Messages API on harness tools | the harness |
| `codex` | Codex via the Codex SDK | OpenAI |
| `openrouter` | any OpenRouter model, on the harness tools | the harness |
| `deterministic` | scripted agent for tests and `harness demo` | the harness |

All engines implement `Agent.turn(prompt, ctx)` in `harness/agents.py`.
Claude and OpenRouter share `LoopAgent`; each tool call goes through the same
role, path, stop, and stage-safety checks. Claude's API only proposes tool calls.
The harness executes them. Codex uses its SDK with a `workspace-write` sandbox
rooted at the stage and approvals set to deny.

Harness tool subprocesses use a filtered environment without provider keys.
Codex's SDK subprocess gets only its own vendor credential, networking settings,
and a HOME beside the stage. Every other inherited variable is blanked.

## Loop

1. Load the run, resolve the allowlisted repository, create a stage.
2. Hand the objective and the software scan to the engine.
3. Ignore any claim of success and run `run_checks`.
4. Run independent review against the diff and the check output.
5. If checks and review passed, optionally publish, then mark `completed`. CLI runs always leave a staged patch for explicit `harness apply`.
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

When software accepts the change and the checks passed, review prefers another
vendor: Codex reviews Claude, Claude reviews Codex, and OpenRouter runs are
reviewed by Claude, else Codex. Anthropic-only runs get a fresh Claude context
with the same model. This provides independent conversation context, not
cross-vendor independence. `HARNESS_REVIEWER=claude|codex|none` overrides the pick.
Claude review of another vendor requires `HARNESS_REVIEWER_MODEL` with a Claude
model ID. Same-vendor Claude review defaults to `HARNESS_MODEL`. The review
diff includes new files and says so when it is truncated. The reviewer answers
`{"ship": bool, "findings": [...]}`. Claude uses strict structured output. An
unreadable answer, a reviewer error, or any finding blocks, including a
contradictory approval with findings.

Completion requires both `verification.passed` and `review.passed`. The review summary is
never the principal's "done" text.

## Stop

`POST /api/runs/{id}/stop` sets `stop_requested`. Engines check the flag around
every model step and tool use; the harness rejects further tool execution and
Codex turns are interrupted. A mutating call after stop is rejected and recorded as
skipped. The store performs every run update as one locked read-modify-write,
so a runtime write in flight cannot overwrite a stop that arrived from the
HTTP thread.

For the Messages API, Stop is observed after the current streamed response returns,
before its tools execute. An in-flight API request can continue until completion
or timeout.

## Persistence

The store keeps the harness loop's conversation messages and step count alongside
run events and review records. Claude uses this conversation persistence through the
Messages API, not a vendor agent session id. Codex records its SDK session id;
that record alone does not restore its process or stage.

On wake, a tool call with no recorded result gets an interrupted error result.
The harness does not replay it: execution is unknown, so the agent must inspect
the stage before deciding whether to repeat a mutation.
