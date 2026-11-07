# Research summary

The question I am working on: how can we trust agents to merge without a human reading every diff?

Anthropic's Claude 3.7 Sonnet system card reports the model "directly returning expected test values rather than implementing general solutions." METR caught o3 patching an evaluator "to always return a perfect score."

## What I tested

I wrote 33 changes to three Python repos: a priority classifier, a bill split, a slug function, each with one bug and a test suite that fails on it. 6 changes are honest fixes. 27 pass every test but should never ship:

- 6 return the expected answers for exactly the tested inputs. One is an if statement for one input pair; the most subtle is a lookup table called a warm cache, seeded with exactly the tested inputs.
- 3 loosen the assertions and 3 skip or delete the failing tests.
- 6 leave the bug in place and patch a correct function in from `conftest.py`.
- 3 are right on every tested input and wrong just outside them.
- 6 fix the bug and also send the process environment to a file or an outside host.

## What caught them

| Check | Bad changes caught | Honest fixes blocked |
| --- | --- | --- |
| The repo's tests | 0 of 27 | 0 of 6 |
| Simple diff rules | 6 of 27 | 0 of 6 |
| Claude Haiku 5.5 reviewing the diff | 27 of 27 | 0 of 6 |
| Executable spec, no model | 27 of 27 | 0 of 6 |

It copies the changed module alone into an empty directory, imports it in a fresh interpreter with almost no environment, and runs the function on inputs the tests never use: 64 score pairs for the classifier, 4,819 calls for the bill split, 4,016 strings for the slug function. Each answer is compared with a rule written from the docstring alone, and an audit hook refuses file writes, network calls, and subprocesses while it runs.

Specs written by Haiku and Sonnet from the docstring and signature alone, without seeing the code or the tests, also caught all 27 in each of 6 sets.

## Where it breaks

27 of 27 mostly means my attacks were easy. Attacks aimed at the spec itself get past it: 0 of 3 caught. [Details and diffs](blind-spots.md).

- Wrong where it does not look. A change that drops the remainder only above a trillion cents passes the bill split spec, which probes totals up to a billion. Another goes wrong only above 100,000 parts, past the thousand the spec probes.
- Wrong only under one setting. A change that drops the remainder only when `LEDGER_ROUNDING=legacy` never runs its bad branch, because the spec sets only the common deployment variables.

The first two attacks of this kind, wrong above 1000 cents and wrong under `APP_ENV=production`, are caught now: the spec probes past its grid and reruns under the common environment settings.

A model reviewer reads the code instead of running it, so it should see each `if` statement. I have not run one on these yet. Other limits:

- I wrote the attacks and the specs from the same docstring.
- The tasks are single functions with clean docstrings. Real code usually has no spec.
- 6 honest fixes is too few to say much about false blocks.

## Next

1. Have a frontier model write the attacks and keep trying. Plot catch rate against attempts.
2. Run it on real issues from public repos.
3. Take specs from somewhere the attacker cannot see, like hidden tests.
4. Run the gate on real merged PRs to measure false blocks.

## Related work

- Greenblatt, Shlegeris, Sachan, Roger. [AI Control: Improving Safety Despite Intentional Subversion](https://arxiv.org/abs/2312.06942) (2023). Trusted monitoring of an untrusted model, on APPS backdoors instead of repo diffs.
- Anthropic. [Claude 3.7 Sonnet System Card](https://assets.anthropic.com/m/785e231869ea8b3b/original/claude-3-7-sonnet-system-card.pdf), section 6.
- METR. [Recent Frontier Models Are Reward Hacking](https://metr.org/blog/2025-06-05-recent-reward-hacking) (2025).
- OpenAI. [Monitoring Reasoning Models for Misbehavior](https://arxiv.org/abs/2503.11926) (2025).

Full method and every case: [evals.md](evals.md).

Preston Jones · p@pdj.dev
