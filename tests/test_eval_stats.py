"""The statistics behind the sweep's comparisons, checked against values worked by hand."""

from __future__ import annotations

import pytest
from harness.evals.stats import holm, paired_difference, sign_test


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


def test_the_interval_is_never_zero_width_and_agrees_with_the_sign_test():
    same = {str(i): 1.0 for i in range(8)}
    low, high = paired_difference(same, same)["ci95"]
    assert low == pytest.approx(-(1 - 0.0125 ** (1 / 8))) and high == pytest.approx(-low)
    # 6 of 8 one way: the sign test gives 0.031, above the 0.025 each half of the interval gets
    six = paired_difference(same, {str(i): 0.0 if i < 6 else 1.0 for i in range(8)})
    assert six["p"] == pytest.approx(0.03125) and six["ci95"][0] < 0 < six["difference"] < six["ci95"][1]
    seven = paired_difference(same, {str(i): 0.0 if i < 7 else 1.0 for i in range(8)})
    assert seven["p"] < 0.025 and seven["ci95"][0] > 0
    swapped = paired_difference({str(i): 0.0 if i < 7 else 1.0 for i in range(8)}, same)
    assert swapped["ci95"] == pytest.approx((-seven["ci95"][1], -seven["ci95"][0]))
