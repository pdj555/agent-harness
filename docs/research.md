# Research summary

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg">
  <img alt="Dot chart of 27 sabotaged changes that all pass their tests: the project's tests catch 0, diff rules 6, a Claude Haiku 5.5 reviewer 27, and an executable spec 27." src="assets/hero-light.svg" width="100%">
</picture>

Agents write code faster than people can review it. Most teams fall back on the test suite as the check before merge. The question I am working on: how can we trust agents to merge without a human reading every diff?

Tests are not enough on their own, and models already game them. Anthropic's Claude 3.7 Sonnet system card reports the model "directly returning expected test values rather than implementing general solutions." METR caught o3 patching an evaluator "to always return a perfect score."

## What I tested

I wrote 33 changes to three small Python repos: a priority classifier, a bill split, a slug function. Each repo has one bug and a test suite that fails on it. 6 changes are honest fixes. 27 pass every test but should never ship:

- 6 return the expected answers for exactly the tested inputs. The least subtle is an if statement for one input pair. The most subtle is a lookup table called a warm cache, seeded with exactly the tested inputs.
- 3 loosen the assertions and 3 skip or delete the failing tests.
- 6 leave the bug in place and patch a correct function in from `conftest.py`, which only the tests load.
- 3 are right on every tested input and wrong just outside them.
- 6 fix the bug and also send the process environment to a file or an outside host.

12 of the 27 are written to read like ordinary changes.

## What caught them

| Check | Bad changes caught | Honest fixes blocked |
| --- | --- | --- |
| The repo's tests | 0 of 27 | 0 of 6 |
| Simple diff rules | 6 of 27 | 0 of 6 |
| Claude Haiku 5.5 reviewing the diff | 27 of 27 | 0 of 6 |
| Executable spec, no model | 27 of 27 | 0 of 6 |

The diff rules watch test files: a new skip marker, a deleted test, a `conftest.py` that replaces project code at import. They never read what an assertion says, so the weakened assertions pass them.

The spec check ignores the tests entirely. It copies the changed module alone into an empty directory, imports it in a fresh interpreter with almost no environment, and runs the function on inputs the tests never use: 64 score pairs for the classifier, 4,819 calls for the bill split, 4,016 strings for the slug function. Each answer is compared with a rule written from the module's docstring alone, and an audit hook refuses file writes, network calls, and subprocesses while it runs. The conftest patches are caught because the patch never loads; the module runs as shipped. The environment leaks are caught because the hook reports the attempt even when the answers are right.

Specs written by Haiku and Sonnet from the docstring and signature alone, without seeing the code or the tests, also caught all 27, in each of 6 sets.

## Why the spec is the interesting part

A model reviewer works as long as it is smarter than the agent it reviews. That gets harder to count on as agents improve. A spec does not need to be smarter than anything. It checks behavior on inputs the agent did not pick, and its verdict rests on the spec and the input set, not on a capability gap. If that holds up against an attacker who is aiming at it, it is a check that does not get weaker over time.

## Where it breaks

27 of 27 mostly means my attacks were easy. So I wrote two attacks aimed at the spec itself, and both get past it: 0 of 2 caught. [Details and diffs](blind-spots.md).

- Wrong where it does not look. The bill split spec checks totals up to 300. A change that drops the remainder only above 1000 passes it. `split_cents(1001, 3)` returns `[333, 333, 333]` and loses two cents.
- Wrong only in production. The spec runs in a clean environment so the module cannot reach host secrets. A change that drops the remainder only when `APP_ENV=production` never runs its bad branch there.

The spec checks behavior it can trigger. A model reviewer reads the code instead of running it, so it should see both `if` statements. I have not run one on these two yet. Other limits:

- I wrote the attacks and the specs, and both come from the same docstring.
- The tasks are single functions with clean docstrings. Real code usually has no spec.
- 6 honest fixes is too few to say much about false blocks.
- The model reviewer could read the file that holds the labels. Nothing suggests it did, but a strict run should hide it.

## Next

1. Have a frontier model write the attacks, see each verdict, and keep trying. Plot catch rate against attempts for each check.
2. Run it on real issues from public repos.
3. Take specs from somewhere the attacker cannot see, like hidden tests.
4. Run the gate on real merged PRs to measure false blocks.

## Related work

- Greenblatt, Shlegeris, Sachan, Roger. [AI Control: Improving Safety Despite Intentional Subversion](https://arxiv.org/abs/2312.06942) (2023). Same setup, trusted monitoring of an untrusted model, on APPS backdoors instead of repo diffs.
- Anthropic. [Claude 3.7 Sonnet System Card](https://assets.anthropic.com/m/785e231869ea8b3b/original/claude-3-7-sonnet-system-card.pdf), section 6.
- METR. [Recent Frontier Models Are Reward Hacking](https://metr.org/blog/2025-06-05-recent-reward-hacking) (2025).
- OpenAI. [Monitoring Reasoning Models for Misbehavior](https://arxiv.org/abs/2503.11926) (2025).

Full method and every case: [evals.md](evals.md). To run the spec check yourself, no API key:

```bash
uv sync --extra dev
uv run harness eval monitor --monitor spec
```

Preston Jones · p@pdj.dev
