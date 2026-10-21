# Research summary

Agents write code faster than people can review it. Most teams fall back on the test suite as the check before merge. The question I'm working on:

**How can we trust agents to merge without a human reading every diff?**

Tests aren't enough on their own. Models already game them. Anthropic's Claude 3.7 Sonnet system card reports the model "directly returning expected test values rather than implementing general solutions." METR caught o3 patching an evaluator "to always return a perfect score."

## What I tested

I wrote 33 changes to three small Python repos. 6 are honest fixes. 27 pass every test but shouldn't ship. Some hardcode the tested answers. Some loosen the assertions. Some are right only where the tests look. A few fix the bug and also send the environment variables to an outside server. 12 of the 27 are written to look like normal changes.

## What caught them

| Check | Bad changes caught | Honest fixes blocked |
| --- | --- | --- |
| The repo's tests | 0 of 27 | 0 of 6 |
| Simple diff rules | 6 of 27 | 0 of 6 |
| Claude Haiku 5.5 reviewing the diff | 27 of 27 | 0 of 6 |
| Executable spec, no model | 27 of 27 | 0 of 6 |

The spec runs the changed function alone, on the tested inputs and thousands of others, and blocks any file write, network call, or subprocess. Specs written by Haiku and Sonnet from the docstring alone, without seeing the code or tests, also caught all 27.

## Why the spec is the interesting part

A model reviewer works as long as it's smarter than the agent. That gets harder as agents improve. A spec doesn't need to be smarter than anything. It checks behavior on inputs the agent didn't pick. If that holds up against a real attacker, it's a check that doesn't get weaker over time.

## Where it breaks

27 of 27 mostly means my attacks were easy. The spec has blind spots I can already see:

- It only checks the inputs it generates. The ledger spec stops at totals of 300 and 16 parts. A change that's wrong only for larger totals would pass it.
- I wrote the attacks and the specs, and both come from the same docstring.
- The tasks are single functions with clean docstrings. Real code usually has no spec.
- 6 honest fixes is too few to say much about false blocks.
- The model reviewer could read the file that holds the labels. Nothing suggests it did, but a strict run should hide it.

## Next

1. Have a frontier model write the attacks, see each verdict, and keep trying. Plot catch rate against attempts for each check.
2. Run it on real issues from public repos.
3. Take specs from somewhere the attacker can't see, like hidden tests.
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
