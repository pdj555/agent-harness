"""The sweep's arithmetic and its spending cap, with no model called."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "reviewer_sweep", Path(__file__).resolve().parent.parent / "scripts" / "reviewer_sweep.py"
)
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)


def test_cost_bills_cache_writes_and_reads_at_their_rates():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
    }
    # Sonnet 5.5: $2 in, $10 out, writes 1.25x in, reads 0.1x in.
    assert sweep.cost(usage, "claude-sonnet-5-5") == pytest.approx(2 + 10 + 2.5 + 0.2)


def test_sets_hold_the_published_cases_and_every_spec_attack():
    assert len(sweep.SETS["main"]) == 33
    assert len(sweep.SETS["spec-attacks"]) == 8
    assert all(case.label != "clean" for case in sweep.SETS["spec-attacks"])


def test_budget_refuses_reviews_once_the_cap_is_spent():
    calls = []

    def expensive(case, stage, verification, objective):
        calls.append(case)
        return {"ship": False, "findings": ["no"], "usage": {"output_tokens": 1_000_000}}

    budget = sweep.Budget(max_usd=1.0)
    guarded = budget.wrap(expensive, "claude-haiku-5-5")  # $0.50 per call
    verdicts = [guarded(n, None, None, "") for n in range(4)]
    assert len(calls) == 2
    assert budget.spent == pytest.approx(1.0)
    assert [v.get("error", False) for v in verdicts] == [False, False, True, True]


def test_estimate_needs_no_key_and_prices_every_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = sweep.estimate(["claude-haiku-5-5", "claude-opus-5-5"], ["spec-attacks"], trials=1)
    haiku, opus = report["rows"]
    assert haiku["reviews"] == opus["reviews"] == 8
    assert haiku["input_tokens"] == opus["input_tokens"] > 0
    assert opus["usd"] > haiku["usd"] > 0


def test_run_refuses_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert sweep.main(["--run"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_unknown_model_is_refused():
    with pytest.raises(SystemExit):
        sweep.main(["--models", "gpt-nothing"])
