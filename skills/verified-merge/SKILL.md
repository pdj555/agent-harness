---
name: verified-merge
description: Use for PR scope, merging checked revisions, post-merge verification, and requested branch cleanup.
---

Deliver the requested patch at the authorized stage: local, committed, pushed,
merged, or externally verified. Keep cleanup within the user's named scope.

## Scope the change

- Inspect the base branch's effective rules before committing or pushing.
  Use its required PR workflow and allowed merge methods even when credentials
  permit administrative writes.
- Refresh the remote base and review the complete proposed diff and commit range.
  A reused feature branch can contain unrelated work. For a focused fix, extract
  that patch onto the current base and adapt its tests to the entry points that
  already exist there. Check existing PR state before creating another PR.
- Reuse a suitable clean checkout. Record protected worktrees' branches, HEADs,
  staged and unstaged edits, and untracked paths. Keep their work in place while
  preparing and verifying the requested patch elsewhere.
- Resolve conflicts against both sides' current behavior. A textual merge can
  combine incompatible runtime contracts. Use the relevant project skills and
  exercise the integrated entry points before publishing the resolution.
- Describe the final diff and checks that actually ran. Keep historical model
  scores separate from the behavior of a replacement implementation.

## Verify and merge the revision

- Use the required checks in [AGENTS.md](../../AGENTS.md) and the current
  [CI workflow](../../.github/workflows/ci.yml). Assign one owner to collect the
  full check results. Review helpers should use focused controls unless assigned
  a full run. Stop redundant runs through the owning session; interrupted runs
  are not passing evidence.
- Match CI to the PR's current head SHA. Wait for the whole workflow, including
  jobs that follow the test matrix. An empty status list or green test jobs with
  packaging pending does not establish a passing workflow.
- Recheck head, base, mergeability, and blocking reviews immediately before
  merging. Use an expected-head guard when available. A changed head needs new
  evidence; a conflicting base needs integration and checks on the new head.
- Confirm the merged PR record and its merge commit on the remote base before
  reporting the patch as merged.

## Verify the requested outcome

- For post-merge CI, read the remote base's current SHA and inspect its `push`
  workflow. A pre-merge `pull_request` run proves a different event. PR-only run
  lookups cannot establish post-merge success. If the base advances while
  waiting, check the newer tip before calling the current base green.
- For dependency-alert closure, confirm the patched version on the default
  branch and query the alert's actual state. Wait for the platform to report
  `fixed`; a branch push or green CI does not close the alert. Preserve the
  distinction between fixing, pushing, merging, and confirmed closure, and
  report the remaining step when the user's authorized scope ends earlier.

## Clean up when requested

- Inspect exact local and remote refs and `git worktree list` before deletion.
  Ref cleanup does not require switching, pulling, stashing, or resetting a
  protected checkout.
- Verify each branch tip still matches the merged PR's head and its merge commit
  is present on the remote base. A squash merge does not make the old head an
  ancestor of the base. New commits on that branch are unmerged work, even when
  the PR is closed.
- Delete only the named refs. Prefer `git branch -d`; stronger local deletion is
  limited to a requested squash-merged branch with a verified unchanged tip.
  Treat an already absent remote branch as complete after checking the server,
  then remove only its stale tracking ref.
- Recheck removed refs and protected worktree state. If another process changed
  that state, report what changed and what this cleanup modified. Preserve the
  new state rather than restoring it or recreating refs without authorization.
  Do not claim the checkout stayed unchanged merely because this agent avoided
  editing it.
- Treat worktree removal separately from branch deletion. Respect shared or
  platform-protected checkouts and report any retained verification checkout.
