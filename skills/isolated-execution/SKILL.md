---
name: isolated-execution
description: Use when changing tools, path checks, git worktrees, publish, shell, or stop behavior.
---

- Resolve every file path with `resolve_in_root`. Deny escapes and `.git` edits.
- Mutate only the stage. Publish is a separate, verified step.
- Bind verification and publish to the recorded starting Git HEAD and source
  file baseline outside the file-tool root. A missing or legacy baseline needs
  a new run; never reconstruct it from the stage after edits.
- Include staged and unstaged changes in the diff. Reject moved HEAD before
  verification or apply: a commit can hide files from review and publication
  while checks still run against them.
- Preserve the frozen verified copy and complete source-conflict preflight when
  changing publish. An earlier fingerprint does not authorize copying a later
  mutable stage.
- Shell runs with `cwd=stage`, no `shell=True`, filtered env, timeout.
- After stop is requested, reject mutating tools. Record the skip.
- Reviewers cannot edit. Helpers do not require the user to manage them.
- Never add a browser filesystem or terminal surface.

Exercise staged and unstaged edits, additions, deletions, renames, moved HEAD,
reconstructed stages, and overlapping source edits through the affected entry
points. Start with [stage history tests](../../tests/test_stage_history.py) and
[isolation tests](../../tests/test_isolation.py).
