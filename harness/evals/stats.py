"""Statistics for comparing reviewers on the same cases.

Two reviewers that saw the same templates are compared template by template, never by
their two separate rates: a paired difference removes the variation between templates,
which is most of the variation in a small set. Every function here is exact or
conservative; none assumes a normal distribution, because the sets have 8 to 24 templates.
"""

from __future__ import annotations

import functools
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


def fisher_exact(first_right: int, first_n: int, second_right: int, second_n: int) -> float:
    """Exact two-sided test that two unpaired groups have the same share right (Fisher's exact test).

    The p value is the chance, with the margins fixed, of a table no more likely than the one
    seen. Used where the two groups are different templates, so no pairing exists.
    """
    right = first_right + second_right
    total = first_n + second_n
    if not first_n or not second_n:
        return 1.0

    def chance(k: int) -> float:
        return math.comb(first_n, k) * math.comb(second_n, right - k) / math.comb(total, right)

    seen = chance(first_right)
    low, high = max(0, right - second_n), min(first_n, right)
    return min(1.0, sum(c for c in map(chance, range(low, high + 1)) if c <= seen * (1 + 1e-9)))


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


@functools.cache
def discordant_interval(higher: int, lower: int, templates: int) -> tuple[float, float]:
    """95% interval for a paired difference in rates when every template is all-or-nothing.

    The difference is the share of templates on which the two differ, times which way they
    differ (the share of those going to the first, doubled, minus one). "Which way" gets the
    exact 95% Clopper-Pearson interval, which is the same calculation as the two-sided sign
    test: it leaves out one half exactly when that test gives p below 0.05. So this interval
    excludes zero exactly when the unadjusted test rejects at 5%. Each end is then scaled by
    the end of the exact 95% interval for the discordant share that takes it further from
    zero. Two 95% intervals guarantee 90% coverage of the true difference. The exact coverage
    (``interval_coverage``, every outcome enumerated, on a 0.01 grid of the two chances) is
    at least 97.0% with 8 templates, 96.1% with 24 and 95.8% with 48, lowest where the two
    directions are about equally likely and no template ties. With no discordant template it is plus and
    minus the upper bound on the discordant share, never zero width.
    """
    differ = higher + lower
    share_low, share_high = _interval(differ, templates, 0.05)
    way_low, way_high = _interval(higher, differ, 0.05) if differ else (0.0, 1.0)
    low, high = 2 * way_low - 1, 2 * way_high - 1
    return low * (share_low if low > 0 else share_high), high * (share_high if high > 0 else share_low)


def paired_difference(first: dict[str, float], second: dict[str, float]) -> dict:
    """Compare two scorers' per-template rates on the templates both judged.

    ``first`` and ``second`` map a template to a rate on it: the share of judged reviews that
    blocked, or 1 and 0 for right and wrong. The result holds the mean per-template
    difference, first minus second; the templates where the first is higher and lower; and an
    exact two-sided sign test on those (the exact McNemar test when every rate is 0 or 1).
    ``ci95`` is ``discordant_interval`` when every difference is -1, 0 or 1. When a
    template's reviews split, so that a difference is a fraction, no interval is given
    (``ci95`` is None): none is known that agrees with the test, and the test stands alone.
    """
    shared = [t for t in first if t in second]
    n = len(shared)
    if not n:
        return {"templates": 0, "difference": None, "ci95": None, "higher": 0, "lower": 0, "p": None}
    diffs = [first[t] - second[t] for t in shared]
    higher, lower = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    exact = all(d in (-1, 0, 1) for d in diffs)
    return {
        "templates": n,
        "difference": sum(diffs) / n,
        "ci95": discordant_interval(higher, lower, n) if exact else None,
        "higher": higher,
        "lower": lower,
        "p": sign_test(higher, lower),
    }


def interval_coverage(templates: int, up: float, down: float) -> float:
    """The exact chance that ``discordant_interval`` covers the true difference ``up - down``.

    Each template independently has the first higher with chance ``up``, lower with chance
    ``down``, and tied otherwise. Every outcome is enumerated; nothing is simulated.
    """
    tie = 1 - up - down
    covered = 0.0
    for higher in range(templates + 1):
        for lower in range(templates + 1 - higher):
            ties = templates - higher - lower
            if (not up and higher) or (not down and lower) or (tie < 1e-12 and ties):
                continue
            log_p = (
                math.lgamma(templates + 1) - math.lgamma(higher + 1) - math.lgamma(lower + 1)
                - math.lgamma(ties + 1)
                + (higher * math.log(up) if higher else 0.0)
                + (lower * math.log(down) if lower else 0.0)
                + (ties * math.log(tie) if ties else 0.0)
            )
            low, high = discordant_interval(higher, lower, templates)
            if low - 1e-12 <= up - down <= high + 1e-12:
                covered += math.exp(log_p)
    return covered
