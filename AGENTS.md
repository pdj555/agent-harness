# AGENTS.md

The contract for any coding agent working in this repository. CLAUDE.md points here.

## What this is

A local coding agent with one rule: models propose, software verifies. A user picks an allowlisted repository and gives one objective. The principal agent inspects the code, edits an isolated Git worktree, and runs the project's checks. A separate review looks at the diff. Completion is decided by a process exit code and a review record, never by model text.

## Rules you can't bend

- `harness.verification.run_checks` is the only source of `passed`. Don't add a parameter, tool, or JSON field that lets model output set it.
- Every tool call goes through `harness.authority`. Nothing skips the role, path, or stop checks.
- Mutating work happens in the stage. The source repository changes only through an explicit apply of a verified fingerprint.
- After stop, mutating tools are rejected and recorded as skipped.
- Don't claim a fix you haven't run the tests for.

## How to work here

Use a pull request for changes to `main`, including documentation and skills. Administrator push access is not the default delivery path.

Inspect before you plan. Make the smallest change that does the job and keep it isolated. Run the tests as you go, and repair failures yourself instead of handing them back. Ask the user only when evidence can't decide.

Write tests that drive the shipped entry points and would fail if the behavior broke. Prefer a small architecture someone can read in an afternoon over a clever one.

## Public boundary

This repository is public. Keep it general-purpose: no secrets, credentials, private endpoints, personal paths, customer data, or historical run output. If something only makes sense for one private setup, leave it out. `tests/test_boundary.py` and `scripts/scan_secrets.py` enforce part of this.

## Before you say done

```bash
uv run pytest
uv run ruff check harness tests scripts
uv run python scripts/scan_secrets.py
```

## For agents: run the evals with no human

One entry point, `harness`. Every `eval` command takes `--json`, prints exactly one JSON document on stdout (progress goes to stderr), and exits with that document's `exit_code`. The document's shape is fixed by [`harness/evals/eval_output.schema.json`](harness/evals/eval_output.schema.json). None of these commands needs a key or calls a model.

```bash
uv sync --extra dev --extra mcp --locked
uv run harness eval sets --json                                    # the six case sets and the offline monitors
uv run harness eval monitor --monitor spec --set main --json       # exit 0: every case judged right
uv run harness eval monitor --monitor spec --set paired --json     # exit 1: the spec ships all 24 paired attacks
uv run harness eval monitor --monitor software --set main --json   # exit 1: the diff rules miss 21 of 27
uv run harness eval monitor --monitor spec --set paired-fresh --fresh-seed 7 --json   # 24 pairs generated now; about 30 s
uv run harness eval sweep-summary PATH/sweep.json --json           # a reviewer sweep's settings, rows, analysis
uv run python scripts/validate_eval_json.py out.json --exit-code "$code"   # check a document against the schema
uv run harness mcp                                                 # the same three operations as MCP tools, stdio
```

| Exit | `status` | Meaning |
| --- | --- | --- |
| 0 | `pass` | The eval ran and the monitor judged every case right. |
| 1 | `fail` | The eval ran and found failures: an attack shipped or a clean change was blocked. `failures` names each case. |
| 2 | `usage_error` | The request was wrong: an unknown set, case, flag or file, or a `sweep.json` with a key missing. Nothing ran. `error` says what. |
| 3 | `infrastructure_error` | The eval could not be scored: a reviewer error, a case never judged, or a case whose own tests fail. |

`--json` is an option of the `eval` commands only and cannot be abbreviated; `harness run ... --json` is refused. `--json --help` prints a document whose `help` field holds the help text, exit 0. Without `--json` the commands print text and keep their earlier exit codes: 0 when every case was judged, 1 otherwise. The MCP server (`harness/mcp_server.py`, optional extra `mcp`) offers `list_case_sets`, `run_offline_monitor` and `read_sweep_summary`; each returns the same document, and a missing or wrong argument comes back as a `usage_error` document. A model reviewer (`--monitor claude`) costs money and is not part of this block.

Read the project skill that matches your work:

- [Evidence-gated completion](skills/evidence-gated-completion/SKILL.md): verification, review, and session recovery.
- [Isolated execution](skills/isolated-execution/SKILL.md): tools, stage Git state, and publish.
- [Public boundary](skills/public-boundary/SKILL.md): docs, configs, fixtures, and prompts.
- [Verified merge](skills/verified-merge/SKILL.md): focused PRs, CI ownership, post-merge verification, and requested cleanup.
