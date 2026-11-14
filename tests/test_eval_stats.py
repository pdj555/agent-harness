"""The statistics behind the sweep's comparisons, checked against values worked by hand."""

from __future__ import annotations

import pytest
from harness.evals.stats import discordant_interval, holm, interval_coverage, paired_difference, sign_test


def test_sign_test_matches_exact_binomial_tails():
    assert sign_test(0, 0) == 1.0
    assert sign_test(5, 0) == pytest.approx(2 / 32)  # the best 5 discordant templates can do
    assert sign_test(0, 6) == pytest.approx(2 / 64)
    assert sign_test(3, 1) == pytest.approx(2 * (1 + 4) / 16)
    assert sign_test(4, 4) == 1.0


def test_holm_steps_down_and_never_reorders():
    # sorted 0.01, 0.03, 0.04 -> 3 * 0.01, 2 * 0.03, then at least the one before
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5, 0.5]) == [1.0, 1.0]
    assert holm([]) == []
    assert holm([0.02]) == [0.02]


def test_paired_difference_uses_only_templates_both_judged():
    first = {"a": 1.0, "b": 1.0, "c": 1.0, "only-first": 0.0}
    second = {"a": 1.0, "b": 0.0, "c": 0.5, "only-second": 1.0}
    out = paired_difference(first, second)
    assert out["templates"] == 3
    assert out["difference"] == pytest.approx(0.5)
    assert (out["higher"], out["lower"]) == (2, 0)
    assert out["p"] == pytest.approx(0.5)
    assert paired_difference({"a": 1.0}, {"b": 1.0})["difference"] is None


def test_holm_adjusts_for_the_registered_family_even_when_some_tests_are_missing():
    assert holm([0.01, 0.03], size=6) == pytest.approx([0.06, 0.15])
    assert holm([0.01, 0.03], size=1) == holm([0.01, 0.03])  # never smaller than the list


@pytest.mark.parametrize("templates", [1, 2, 5, 8, 14, 22, 24, 48])
def test_the_exact_interval_excludes_zero_exactly_when_the_test_rejects(templates):
    """Every table with this many templates: the interval and the unadjusted test never disagree."""
    for higher in range(templates + 1):
        for lower in range(templates + 1 - higher):
            low, high = discordant_interval(higher, lower, templates)
            rejects = sign_test(higher, lower) < 0.05
            assert rejects == (low > 0 or high < 0), (higher, lower, templates)
            assert low < high and -1 <= low and high <= 1  # never zero width
            assert low <= (higher - lower) / templates <= high
            mirrored = discordant_interval(lower, higher, templates)
            assert mirrored == pytest.approx((-high, -low))


def test_the_exact_interval_covers_the_true_difference_by_enumeration():
    """Exact coverage, every outcome counted, on a 0.05 grid: never below 95%, and the documented minima."""
    for templates, documented in ((8, 0.9735), (24, 0.9649)):
        worst = min(
            interval_coverage(templates, i / 20, j / 20) for i in range(21) for j in range(21 - i)
        )
        assert worst == pytest.approx(documented, abs=1e-4)
        assert worst >= 0.95
    # the finer grid's minima quoted in the docstring and docs/evals.md
    assert interval_coverage(8, 0.47, 0.53) == pytest.approx(0.9699, abs=1e-4)
    assert interval_coverage(24, 0.48, 0.52) == pytest.approx(0.9612, abs=1e-4)
    assert interval_coverage(48, 0.51, 0.49) == pytest.approx(0.9579, abs=1e-4)
    assert interval_coverage(24, 0, 0) == 1.0 and interval_coverage(24, 1, 0) == pytest.approx(1.0)


def test_split_templates_get_the_exact_test_and_no_interval():
    same = {str(i): 1.0 for i in range(8)}
    none = paired_difference(same, same)
    assert none["p"] == 1.0
    assert none["ci95"] == pytest.approx((-(1 - 0.025 ** (1 / 8)), 1 - 0.025 ** (1 / 8)))
    five = paired_difference(same, {str(i): 0.0 if i < 5 else 1.0 for i in range(8)})
    assert five["p"] == pytest.approx(0.0625) and five["ci95"][0] < 0 < five["ci95"][1]
    six = paired_difference(same, {str(i): 0.0 if i < 6 else 1.0 for i in range(8)})
    assert six["p"] == pytest.approx(0.03125) and six["ci95"][0] > 0
    split = paired_difference(same, {str(i): 0.5 if i < 3 else 1.0 for i in range(8)})
    assert split["ci95"] is None and split["difference"] == pytest.approx(3 / 16)
    assert (split["higher"], split["lower"], split["p"]) == (3, 0, 0.25)
    assert paired_difference({"a": 1.0}, {"b": 1.0})["ci95"] is None


def test_fisher_exact_matches_known_tables():
    from harness.evals.stats import fisher_exact

    assert fisher_exact(3, 4, 1, 4) == pytest.approx(34 / 70)  # the tea-tasting table
    assert fisher_exact(8, 10, 1, 10) == pytest.approx(0.005477, abs=1e-6)
    assert fisher_exact(48, 48, 48, 48) == 1.0 and fisher_exact(5, 10, 5, 10) == 1.0
    assert fisher_exact(48, 48, 30, 48) == fisher_exact(30, 48, 48, 48) < 1e-5  # two-sided, symmetric
    assert fisher_exact(0, 0, 3, 5) == 1.0  # an empty group says nothing
