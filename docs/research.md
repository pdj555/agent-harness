# Tests are not a review

Every one of these 27 code changes passes its test suite.

One sends your environment variables to a server you have never heard of. One hardcodes the answers for exactly the inputs the tests check. One is right everywhere the tests look and wrong everywhere they don't. One leaves the bug in place and quietly swaps in a fixed function only while the tests run.

The tests caught none of them.

That matters now because agents write code faster than anyone can read it, and the test suite has become the last check before merge. Frontier models already know how to beat it. Anthropic's Claude 3.7 Sonnet system card reports the model "directly returning expected test values rather than implementing general solutions." METR caught o3 monkey-patching an evaluator "to always return a perfect score." And the same pass/fail signal trains the next model, so a change that games its tests ships and gets reinforced.

So the question is:

**How can we trust agents to merge without a human reading every diff?**

## The result

| Check | Sabotage caught | Honest fixes blocked |
| --- | --- | --- |
| The project's own tests | 0 of 27 | 0 of 6 |
| Software diff rules | 6 of 27 | 0 of 6 |
| Claude Haiku 5.5 as reviewer | **27 of 27** | 0 of 6 |
| Executable spec, no model at all | **27 of 27** | 0 of 6 |

Specs written by Claude Haiku 5.5 and Sonnet 5.5 from the docstring alone, never seeing the code or the tests, also caught 27 of 27 with no false blocks, in all 6 independent sets.

## The idea worth studying

Two checks reach 27 of 27. They are not the same kind of check.

A model reviewer works because it is smarter than the agent it reviews. That is a race, and as agents get stronger the reviewer's margin shrinks.

A spec does not race. It copies the changed code into an empty directory, runs it on every tested input and thousands of untested ones, and refuses every file write, network call, and subprocess. Each verdict is a counterexample, like `split_cents(2, 3) gives [2, 0, 0]`, or a refused call, like `network (urllib.Request) https://telemetry.example.com/v1/events`. Nothing about it depends on out-thinking the agent.

If that holds against a strong attacker who adapts, it is a monitor that does not decay as capability grows. Finding where it breaks is the research.

## Threat model

An untrusted coding agent hands over a diff that passes the project's tests. The diff is wrong in one of two ways:

- **Reward hacking.** It satisfies the tests without solving the task: special-casing the tested inputs, loosening assertions, skipping the failing test, patching from `conftest.py`, or fixing only what is tested.
- **Sabotage.** It solves the task and also does harm, writing the environment to disk or posting it to an outside host.

The defender sees what a live run sends: the diff, the check output, and read-only access to the worktree.

## Setup

33 changes across three small Python repositories, each with a real bug and a suite that fails on it. 6 honest fixes, 27 attacks. 12 attacks are written to read like ordinary changes: a "warm cache" seeded with exactly the tested inputs, an autouse fixture that swaps the function only under test, "usage telemetry" that ships the environment out, fixes that are right only where the tests look. A test enforces that every case passes its suite, so no attack is a free catch.

Method, confidence intervals, and every case: [evals.md](evals.md).

## Limits

27 of 27 should not be over-read. Here is why.

- **The attacks are mine.** I wrote the attacks and the specs. Saturation measures how hard I attacked, not how good the monitor is.
- **Spec and attack share a source.** The incorrect-fix cases are defined against the docstring, and the specs come from the docstring. Model-written specs never saw the code or tests, but they read the same docstring.
- **The tasks are toys.** Single functions with clean docstrings. Real code rarely ships a spec.
- **The sample is small.** 27 attacks, 6 honest fixes. Zero false blocks on 6 says little about the cost to usefulness.
- **The reviewer could read the labels.** The installed `harness/evals/cases.py` sits on paths the model reviewer can read. Nothing suggests it did. A strict run should hide it.

## Next

1. **An attacker that fights back.** A frontier model writes the attacks, sees each verdict, and retries. The output is a curve: catch rate against attacker rounds, one line per check. If the spec line stays flat while the reviewer's falls, that is the result.
2. **Real tasks.** Real issues from public repositories, so the author does not choose the battlefield.
3. **Held-out specs.** Specs from a source the attacker never sees, such as hidden tests or a separate requirements document.
4. **The usefulness cost.** Run the gate on real merged pull requests and report false blocks next to catch rate.

## Related work

- Greenblatt, Shlegeris, Sachan, Roger. [AI Control: Improving Safety Despite Intentional Subversion](https://arxiv.org/abs/2312.06942) (2023). Trusted monitoring of an untrusted model. This work moves that setting from APPS backdoors to repository diffs.
- Anthropic. [Claude 3.7 Sonnet System Card](https://assets.anthropic.com/m/785e231869ea8b3b/original/claude-3-7-sonnet-system-card.pdf), section 6.
- METR. [Recent Frontier Models Are Reward Hacking](https://metr.org/blog/2025-06-05-recent-reward-hacking) (2025).
- OpenAI. [Monitoring Reasoning Models for Misbehavior](https://arxiv.org/abs/2503.11926) (2025).

Reproduce the spec row in about a minute, no key:

```bash
uv sync --extra dev
uv run harness eval monitor --monitor spec
```

Preston Jones · p@pdj.dev
