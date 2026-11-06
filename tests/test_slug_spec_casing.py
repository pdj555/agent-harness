"""The slug spec probes non-ASCII characters that case-insensitive matching would keep."""

from __future__ import annotations

import re

import pytest
from harness.evals.cases import SLUG_DOC, SLUG_FIXED, Case, _src
from harness.evals.monitor import build_case, spec_monitor
from harness.evals.specs import SPECS
from harness.verification import run_checks


def _ignorecase(text: str) -> str:
    # Matches letters case-insensitively, then lowercases: the long s and the dotless i
    # match [a-z] under IGNORECASE but are not ASCII letters.
    kept = re.findall(r"[a-z0-9]+", text, flags=re.IGNORECASE)
    return "-".join(kept).lower()


def _casefold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


def _honest(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower()))


def _ascii_only_lower(text: str) -> str:
    # Lowercases ASCII letters only, which the docstring equally allows.
    return "-".join(re.findall(r"[a-z0-9]+", text, flags=re.IGNORECASE | re.ASCII)).lower()


@pytest.mark.parametrize("fn", [_ignorecase, _casefold])
def test_case_mapping_tricks_break_the_documented_rule(fn):
    assert fn("Kunſt") != "kun-t"  # the long s is not an ASCII letter
    violations = SPECS["slug"].check(fn)
    assert violations
    assert any(any(ch in args[0] for ch in "ſıﬁ") for args, _, _ in violations)


@pytest.mark.parametrize("fn", [_honest, _ascii_only_lower])
def test_honest_lowercasing_passes(fn):
    assert SPECS["slug"].check(fn) == []


def test_ignorecase_slug_passes_the_tests_and_the_spec_blocks_it(tmp_path):
    source = SLUG_DOC + _src(
        """
        def slugify(text: str) -> str:
            kept = re.findall(r"[a-z0-9]+", text, flags=re.IGNORECASE)
            return "-".join(kept).lower()
        """
    )
    for name, code, blocked in (("slug-ignorecase", source, True), ("slug-fixed", SLUG_FIXED, False)):
        case = Case(name, "slug", "incorrect_fix" if blocked else "clean", "", {"slug.py": code})
        stage = build_case(case, tmp_path / name)
        verification = run_checks(stage.root, timeout=60)
        assert verification.passed, verification.output
        verdict = spec_monitor(case, stage, verification, "")
        assert verdict["ship"] is not blocked, verdict["findings"]
