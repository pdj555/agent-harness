"""Statistics for comparing reviewers on the same cases.

Two reviewers that saw the same templates are compared template by template, never by
their two separate rates: a paired difference removes the variation between templates,
which is most of the variation in a small set. Every function here is exact or
conservative; none assumes a normal distribution, because the sets have 8 to 24 templates.
"""

from __future__ import annotations

import math
import random

from harness.evals.monitor import _beta_quantile


def sign_test(first: int, second: int) -> float:
    """Exact two-sided sign test: the chance of a split this lopsided if either side is as likely.

    On templates scored right or wrong for each of two reviewers, with ``first`` and
    ``second`` the two kinds of discordant template, this is the exact McNemar test.
    """
    n = first + second
    low = min(first, second)
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(low + 1)) / 2**n) if n else 1.0


def holm(pvalues: list[float], size: int | None = None) -> list[float]:
    """Holm's step-down adjustment, in the order given. Controls the chance of any false positive.

    ``size`` is the size of the family when it is larger than the list: a family registered
    with 6 tests of which 4 were run is still adjusted for 6, so leaving a test out never
    makes the others easier to pass.
    """
    size = max(size or 0, len(pvalues))
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted = [0.0] * len(pvalues)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (size - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def _interval(successes: float, trials: float, alpha: float) -> tuple[float, float]:
    """Clopper-Pearson interval at level ``1 - alpha``."""
    low = 0.0 if successes <= 0 else _beta_quantile(alpha / 2, successes, trials - successes + 1)
    high = 1.0 if successes >= trials else _beta_quantile(1 - alpha / 2, successes + 1, trials - successes)
    return low, high


def discordant_interval(higher: int, lower: int, templates: int) -> tuple[float, float]:
    """95% interval for a paired difference in rates when every template is all-or-nothing.

    The difference is the share of templates on which the two differ, times which way they
    differ (the share of those going to the first, doubled, minus one). "Which way" gets the
    exact 95% Clopper-Pearson interval, which is the same calculation as the two-sided sign
    test: it leaves out one half exactly when that test gives p below 0.05. So this interval
    excludes zero exactly when the unadjusted test rejects at 5%. Each end is then scaled by
    the end of the exact 95% interval for the discordant share that takes it further from
    zero. Two 95% intervals guarantee 90% coverage of the true difference; in simulation it
    was 97% or more (tests/test_eval_stats.py). With no discordant template it is plus and
    minus the upper bound on the discordant share, never zero width.
    """
    differ = higher + lower
    share_low, share_high = _interval(differ, templates, 0.05)
    way_low, way_high = _interval(higher, differ, 0.05) if differ else (0.0, 1.0)
    low, high = 2 * way_low - 1, 2 * way_high - 1
    return low * (share_low if low > 0 else share_high), high * (share_high if high > 0 else share_low)


def bootstrap_interval(diffs: list[float], resamples: int = 10_000, seed: int = 0) -> tuple[float, float]:
    """95% percentile bootstrap interval for the mean of per-template differences, fixed seed.

    Used when a template's rate is a fraction (its reviews split), where no exact interval
    matches the sign test. It resamples templates, so it is repeatable but approximate, and
    it can disagree with the test, which counts only the direction of each difference.
    """
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(resamples))
    return means[int(0.025 * resamples)], means[min(resamples - 1, int(0.975 * resamples))]


def paired_difference(first: dict[str, float], second: dict[str, float]) -> dict:
    """Compare two scorers' per-template rates on the templates both judged.

    ``first`` and ``second`` map a template to a rate on it: the share of judged reviews that
    blocked, or 1 and 0 for right and wrong. The result holds the mean per-template
    difference, first minus second; the templates where the first is higher and lower; an
    exact two-sided sign test on those (the exact McNemar test when every rate is 0 or 1);
    and a 95% interval. ``method`` says which interval: ``exact`` (``discordant_interval``,
    which excludes zero exactly when p is below 0.05) when every difference is -1, 0 or 1,
    otherwise ``bootstrap`` (``bootstrap_interval``), which need not agree with the test.
    """
    shared = [t for t in first if t in second]
    n = len(shared)
    empty = {"templates": 0, "difference": None, "ci95": None, "higher": 0, "lower": 0, "p": None}
    if not n:
        return {**empty, "method": None}
    diffs = [first[t] - second[t] for t in shared]
    higher, lower = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    exact = all(d in (-1, 0, 1) for d in diffs)
    return {
        "templates": n,
        "difference": sum(diffs) / n,
        "ci95": discordant_interval(higher, lower, n) if exact else bootstrap_interval(diffs),
        "method": "exact" if exact else "bootstrap",
        "higher": higher,
        "lower": lower,
        "p": sign_test(higher, lower),
    }
