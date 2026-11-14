"""The preregistration file: it loads, it is hashed, and a run is checked against it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from harness.evals.prereg import PreregError, audit, load, registered

SWEEP_3 = Path(__file__).resolve().parent.parent / "docs" / "prereg" / "sweep-3.json"


def test_the_committed_preregistration_loads_and_is_hashed_from_its_bytes():
    prereg = load(SWEEP_3)
    assert prereg["sha256"] == hashlib.sha256(SWEEP_3.read_bytes()).hexdigest()
    assert prereg["settings"]["trials"] == 3 and prereg["settings"]["effort"] == "medium"
    assert prereg["thresholds"] == {"alpha": 0.05, "saturation_lower_bound": 0.9}
    wanted = registered(prereg)
    assert wanted["paired", "templates_right_vs_best_shortcut"] == "primary"
    assert wanted["paired-fresh", "paired_difference.catch_rate"] == "primary"
    assert wanted["*", "cost_usd"] == "secondary"
    assert wanted["paired", "catch_rate"] == "primary"  # the wildcard does not demote it
    assert ("main", "paired_difference.catch_rate") not in wanted


def _write(tmp_path, change) -> Path:
    doc = json.loads(SWEEP_3.read_text())
    change(doc)
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(doc))
    return path


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("construct"), "needs 'construct'"),
        (lambda d: d["settings"].pop("trials"), "settings need 'trials'"),
        (lambda d: d.update(metrics=[]), "list of 'metrics'"),
        (lambda d: d["metrics"].append({"set": "main", "metric": "catch_rate"}), "set, metric and a role"),
        (lambda d: d.update(metrics=d["metrics"][7:]), "no primary metric"),
        (lambda d: d["comparisons"].pop("sets"), "comparisons need a list of 'sets'"),
        (lambda d: d["thresholds"].update(alpha=5), "'alpha' must be a number between 0 and 1"),
        (lambda d: d.pop("thresholds"), "needs 'thresholds'"),
    ],
)
def test_a_preregistration_that_leaves_something_out_is_refused(tmp_path, change, message):
    with pytest.raises(PreregError, match=message):
        load(_write(tmp_path, change))


def test_a_missing_or_unreadable_preregistration_is_refused(tmp_path):
    with pytest.raises(PreregError, match="cannot read"):
        load(tmp_path / "none.json")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(PreregError, match="cannot read"):
        load(tmp_path / "bad.json")


def test_audit_sorts_metrics_and_reports_deviations():
    prereg = load(SWEEP_3)
    computed = [
        ("paired", "catch_rate"),
        ("paired", "templates_right_vs_best_shortcut"),
        ("main", "catch_rate"),
        ("main", "paired_difference.catch_rate"),
        ("main", "kappa"),
    ]
    settings = {**prereg["settings"], "trials": 2, "sets": ["paired", "main"]}
    out = audit(prereg, computed, settings)
    assert out["primary"] == [["paired", "catch_rate"], ["paired", "templates_right_vs_best_shortcut"]]
    assert out["secondary"] == [["main", "catch_rate"]]
    assert out["not_preregistered"] == [["main", "paired_difference.catch_rate"], ["main", "kappa"]]
    assert ["paired-fresh", "catch_rate"] in out["preregistered_not_computed"]
    assert ["paired", "catch_rate"] not in out["preregistered_not_computed"]
    assert {d["setting"]: (d["preregistered"], d["run"]) for d in out["deviations"]} == {
        "trials": (3, 2),
        "sets": (prereg["settings"]["sets"], ["paired", "main"]),
    }
    # the same models in another order is not a deviation
    reordered = {**prereg["settings"], "models": prereg["settings"]["models"][::-1]}
    assert audit(prereg, computed, reordered)["deviations"] == []
