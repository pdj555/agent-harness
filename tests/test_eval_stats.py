"""The statistics behind the sweep's comparisons, checked against values worked by hand."""

from __future__ import annotations

import random

import pytest
from harness.evals.stats import discordant_interval, holm, paired_difference, sign_test


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


def test_the_exact_interval_covers_the_true_difference_in_simulation():
    """Templates drawn at random: first higher with one chance, lower with another, else tied."""
    rng = random.Random(0)
    settings = [(0, 0), (0.05, 0.05), (0.1, 0), (0.25, 0.25), (0.5, 0), (0.5, 0.1), (0.9, 0),
                (0.6, 0.3), (0.5, 0.5), (0.2, 0.1), (1, 0), (0.8, 0.2)]
    for templates in (8, 24):
        for up, down in settings:
            covered = 0
            for _ in range(1500):
                draws = [rng.random() for _ in range(templates)]
                higher = sum(d < up for d in draws)
                lower = sum(up <= d < up + down for d in draws)
                low, high = discordant_interval(higher, lower, templates)
                covered += low - 1e-9 <= up - down <= high + 1e-9
            assert covered / 1500 >= 0.95, (templates, up, down, covered / 1500)


def test_paired_difference_says_which_interval_it_used():
    same = {str(i): 1.0 for i in range(8)}
    none = paired_difference(same, same)
    assert none["method"] == "exact" and none["p"] == 1.0
    assert none["ci95"] == pytest.approx((-(1 - 0.025 ** (1 / 8)), 1 - 0.025 ** (1 / 8)))
    five = paired_difference(same, {str(i): 0.0 if i < 5 else 1.0 for i in range(8)})
    assert five["p"] == pytest.approx(0.0625) and five["ci95"][0] < 0 < five["ci95"][1]
    six = paired_difference(same, {str(i): 0.0 if i < 6 else 1.0 for i in range(8)})
    assert six["p"] == pytest.approx(0.03125) and six["ci95"][0] > 0
    split = paired_difference(same, {str(i): 0.5 if i < 3 else 1.0 for i in range(8)})
    assert split["method"] == "bootstrap" and split["difference"] == pytest.approx(3 / 16)
    assert split["ci95"] == paired_difference(same, {str(i): 0.5 if i < 3 else 1.0 for i in range(8)})["ci95"]
    assert 0 <= split["ci95"][0] < split["difference"] < split["ci95"][1] <= 0.5
    assert paired_difference({"a": 1.0}, {"b": 1.0})["method"] is None
