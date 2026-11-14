"""Statistics for comparing reviewers on the same cases.

Two reviewers that saw the same templates are compared template by template, never by
their two separate rates: a paired difference removes the variation between templates,
which is most of the variation in a small set. Every function here is exact or
conservative; none assumes a normal distribution, because the sets have 8 to 24 templates.
"""

from __future__ import annotations

import math

from harness.evals.monitor import _beta_quantile


def sign_test(first: int, second: int) -> float:
    """Exact two-sided sign test: the chance of a split this lopsided if either side is as likely.

    On templates scored right or wrong for each of two reviewers, with ``first`` and
    ``second`` the two kinds of discordant template, this is the exact McNemar test.
    """
    n = first + second
    low = min(first, second)
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(low + 1)) / 2**n) if n else 1.0


def holm(pvalues: list[float]) -> list[float]:
    """Holm's step-down adjustment, in the order given. Controls the chance of any false positive."""
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted = [0.0] * len(pvalues)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvalues) - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def _interval(successes: float, trials: int, alpha: float) -> tuple[float, float]:
    """Clopper-Pearson interval at level ``1 - alpha``; ``successes`` may be fractional."""
    low = 0.0 if successes <= 0 else _beta_quantile(alpha / 2, successes, trials - successes + 1)
    high = 1.0 if successes >= trials else _beta_quantile(1 - alpha / 2, successes + 1, trials - successes)
    return low, high


def paired_difference(first: dict[str, float], second: dict[str, float]) -> dict:
    """Compare two reviewers' per-template rates on the templates both judged.

    ``first`` and ``second`` map a template to that reviewer's rate on it (the share of
    judged reviews that blocked). The result holds the mean of the per-template differences,
    first minus second; the templates where the first is higher and where it is lower; an
    exact two-sided sign test on those discordant templates; and a 95% interval for the mean
    difference. The interval is conservative. The mean difference is how much the two differ
    (the share of templates, weighted by the size of each difference) times which way (the
    share of that going to the first, doubled, minus one). Each factor gets an exact 97.5%
    Clopper-Pearson interval, and the two are combined, so together they cover with at least
    95%. It is never zero width: no difference on 8 templates still leaves -42% to +42%.
    """
    shared = [t for t in first if t in second]
    n = len(shared)
    if not n:
        return {"templates": 0, "difference": None, "ci95": None, "higher": 0, "lower": 0, "p": None}
    diffs = [first[t] - second[t] for t in shared]
    up = sum(d for d in diffs if d > 0)
    down = -sum(d for d in diffs if d < 0)
    differ_low, differ_high = _interval(up + down, n, 0.025)
    way_low, way_high = _interval(up, up + down, 0.025) if up + down else (0.0, 1.0)
    low, high = 2 * way_low - 1, 2 * way_high - 1
    higher, lower = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    ci = (low * (differ_low if low > 0 else differ_high), high * (differ_high if high > 0 else differ_low))
    return {
        "templates": n,
        "difference": sum(diffs) / n,
        "ci95": ci,
        "higher": higher,
        "lower": lower,
        "p": sign_test(higher, lower),
    }
