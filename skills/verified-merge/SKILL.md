---
name: verified-merge
description: Use when opening a PR, waiting for CI before merging, or cleaning up a merged branch.
---

Deliver the checked revision and preserve unrelated work. Follow the user's
requested delivery and cleanup scope.

## Scope the change

- Inspect the base branch's effective rules before committing or pushing.
  Use its required PR workflow and allowed merge methods even when credentials
  permit administrative writes.
- Refresh remote refs before comparing the branch with its current base. Review
  every commit the PR would introduce; a reused branch can contain older work.
  Check existing PR state before creating or reopening one.
- Resolve conflicts against current behavior on both sides. A textual merge can
  combine incompatible runtime contracts. Use the relevant project skills and
  test the integrated entry points before publishing the resolution.
- Describe the final diff, compatibility changes, and validation that actually
  ran. Keep historical model scores separate from a replacement implementation.

## Merge the checked revision

- Read the current [CI workflow](../../.github/workflows/ci.yml). Wait for the
  whole workflow, including jobs dependent on the test matrix. An empty status
  list or passing test jobs with packaging pending is not a passing workflow.
- Associate CI with the PR's current head SHA. Recheck head, base, mergeability,
  and blocking reviews immediately before merging. Use an expected-head guard
  when the merge API supports it.
- If the head changes or a newer base introduces a conflict, refresh refs,
  integrate, and wait for CI on the new head. Do not reuse the previous green
  run or weaken checks to get a merge through. Keep the PR checklist current.
- Confirm the merged PR record and merge commit on the remote base before
  reporting completion.

## Clean up when requested

- Check for local edits, switch to the base branch, and pull with `--ff-only`.
- Delete only the named merged branch. A squash merge does not make its old
  head an ancestor of the base: verify the merged PR's head matches the branch
  tip and its merge commit is present on the base. New branch commits are
  unmerged work, even when the PR is closed.
- Prefer `git branch -d`. Use `git branch -D` only for the exact branch the user
  requested, after verifying the squash merge and unchanged branch tip.
- Treat an already absent remote branch as complete after checking remote state;
  remove its stale tracking ref. Verify the final branch, sync state, and working
  tree. Preserve other branches and worktrees.
