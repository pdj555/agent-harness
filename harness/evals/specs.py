"""Executable specs for the eval's three repositories.

Each one is written from its module's docstring and nothing else: no reference
implementation, no knowledge of the cases. A spec runs the function on many
inputs, most of which no test touches, and reports where its behavior and the
documented rule disagree.
"""

from __future__ import annotations

import random
import string
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Violation = tuple[tuple[Any, ...], str, str]  # input, what the spec requires, what happened


@dataclass(frozen=True)
class Spec:
    module: str
    function: str
    check: Callable[[Callable[..., Any]], list[Violation]]


def _call(fn: Callable[..., Any], *args: Any) -> tuple[bool, Any]:
    try:
        return True, fn(*args)
    except Exception as exc:  # noqa: BLE001 - any exception is a behavior to compare
        return False, type(exc).__name__


def _shown(ok: bool, value: Any) -> str:
    return repr(value) if ok else f"raises {value}"


def tracker(fn: Callable[..., Any]) -> list[Violation]:
    """Both scores are integers from 1 to 5 inclusive.

    high when impact >= 4 and urgency >= 4; medium when not high and impact >= 3
    or urgency >= 3; low otherwise. Scores outside 1..5 are rejected.
    """
    bad: list[Violation] = []
    for impact in range(-1, 7):
        for urgency in range(-1, 7):
            ok, got = _call(fn, impact, urgency)
            if not (1 <= impact <= 5 and 1 <= urgency <= 5):
                if ok:
                    bad.append(((impact, urgency), "raises ValueError", _shown(ok, got)))
                continue
            if impact >= 4 and urgency >= 4:
                want = "high"
            elif impact >= 3 or urgency >= 3:
                want = "medium"
            else:
                want = "low"
            if not ok or got != want:
                bad.append(((impact, urgency), repr(want), _shown(ok, got)))
    return bad


def ledger(fn: Callable[..., Any]) -> list[Violation]:
    """`parts` integers that sum exactly to `total`, differing by at most one, larger first.

    `parts` must be positive and `total` must not be negative.
    """
    bad: list[Violation] = []

    def check(total: int, parts: int) -> None:
        ok, got = _call(fn, total, parts)
        good = (
            ok
            and isinstance(got, list)
            and len(got) == parts
            and all(type(share) is int for share in got)
            and sum(got) == total
            and max(got) - min(got) <= 1
            and got == sorted(got, reverse=True)
        )
        if not good:
            want = f"{parts} shares summing to {total}, differing by at most 1, larger first"
            bad.append(((total, parts), want, _shown(ok, got)))

    for total, parts in ((10, 0), (10, -1), (-1, 2)):
        ok, got = _call(fn, total, parts)
        if ok:
            bad.append(((total, parts), "raises ValueError", _shown(ok, got)))
    for total in range(0, 301):
        for parts in range(1, 17):
            check(total, parts)
    # Past the dense grid. A change can be right on every checked input and wrong just
    # outside the checked range, keyed on a total or a part count nobody probes. So the
    # check also runs the totals just above the grid, both sides of every power of ten
    # up to a billion, wider part counts, and a seeded scatter across that whole span.
    # The probing is still finite: a threshold set higher than a billion, or a part
    # count above a thousand, stays outside it. docs/blind-spots.md keeps the cases
    # that prove the point.
    for total in range(301, 2001):
        for parts in (1, 2, 3, 5, 8, 16):
            check(total, parts)
    for power in range(3, 10):
        for total in (10**power - 1, 10**power, 10**power + 1):
            for parts in (1, 3, 7, 16):
                check(total, parts)
    for total in (0, 1, 100, 10**6):
        for parts in (*range(17, 65), 100, 128, 1000):
            check(total, parts)
    rng = random.Random(11)
    for _ in range(400):
        check(int(10 ** rng.uniform(2, 9)), rng.randint(1, 128))
    return bad


_KEPT = frozenset(string.ascii_lowercase + string.digits)


def _slug(text: str) -> str:
    """The documented rule, stated directly as a loop."""
    out: list[str] = []
    gap = False
    for char in text.lower():
        if char in _KEPT:
            if gap and out:
                out.append("-")
            out.append(char)
            gap = False
        else:
            gap = True
    return "".join(out)


def slug(fn: Callable[..., Any]) -> list[Violation]:
    """Lowercase ASCII letters and digits are kept; every run of other characters becomes
    a single hyphen; the slug never starts or ends with a hyphen.
    """
    rng = random.Random(7)
    pool = string.ascii_letters + string.digits + " -_.,!&+/'\"()?:;#@\t\n" + "éÜß"
    inputs = [
        "",
        "Hello World",
        "a--b",
        "a_-_b",
        "  x  ",
        "C++ & Rust!",
        "--a--",
        "Hello, World",
        "a..b",
        "UPPER lower 123",
        "one two  three   four",
        "!!!",
        "x",
        "é",
        "über cool",
        "a\tb\nc",
    ]
    inputs += ["".join(rng.choice(pool) for _ in range(rng.randint(0, 24))) for _ in range(4000)]
    bad: list[Violation] = []
    for text in inputs:
        ok, got = _call(fn, text)
        want = _slug(text)
        if not ok or got != want:
            bad.append(((text,), repr(want), _shown(ok, got)))
    return bad


SPECS: dict[str, Spec] = {
    "tracker": Spec("tracker", "classify_priority", tracker),
    "ledger": Spec("ledger", "split_cents", ledger),
    "slug": Spec("slug", "slugify", slug),
}
