"""The preregistration file: it loads, it is hashed, and a run is checked against it per model and set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from harness.evals.prereg import PreregError, audit, family_members, load, pair_name, registered

SWEEP_3 = Path(__file__).resolve().parent.parent / "docs" / "prereg" / "sweep-3.json"
HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
SHORTCUT = "templates_right_vs_best_shortcut"


def test_the_committed_preregistration_loads_and_is_hashed_from_its_bytes():
    prereg = load(SWEEP_3)
    assert prereg["sha256"] == hashlib.sha256(SWEEP_3.read_bytes()).hexdigest()
    assert prereg["settings"]["trials"] == 3 and prereg["settings"]["effort"] == "medium"
    assert prereg["settings"]["sets"] == ["main", "spec-attacks", "same-shape", "paired", "paired-fresh"]
    assert prereg["thresholds"] == {"alpha": 0.05, "min_share_judged": 0.9}


def test_the_two_families_are_six_and_twelve_tests():
    members = family_members(load(SWEEP_3))
    assert len(members["model_vs_shortcut"]) == 6  # 3 models on paired and paired-fresh
    assert (OPUS, "paired-fresh", SHORTCUT) in members["model_vs_shortcut"]
    assert len(members["model_vs_model"]) == 12  # 3 pairs, 2 sets, 2 rates
    pair = pair_name(SONNET, HAIKU)
    assert pair == pair_name(HAIKU, SONNET) == f"{HAIKU} vs {SONNET}"
    assert (pair, "paired", "paired_difference.false_block_rate") in members["model_vs_model"]
    assert not [key for key in members["model_vs_model"] if key[1] == "same-shape"]


def test_every_registered_result_is_per_model_and_set():
    wanted = registered(load(SWEEP_3))
    assert wanted[HAIKU, "paired", SHORTCUT] == "primary"
    assert wanted[OPUS, "paired-fresh", "catch_rate"] == "primary"
    assert wanted[OPUS, "main", "catch_rate"] == "secondary"  # the wildcard, on every registered set
    assert wanted[SONNET, "spec-attacks", "cost_usd"] == "secondary"
    assert (HAIKU, "same-shape", SHORTCUT) not in wanted
    # spec-attacks has no clean change, so no false-block rate is registered there
    assert (HAIKU, "spec-attacks", "false_block_rate") not in wanted
    assert wanted[HAIKU, "spec-attacks", "catch_rate"] == "secondary"
    assert wanted[HAIKU, "main", "false_block_rate"] == "secondary"
    assert ("claude-fable-5-1", "paired", "catch_rate") not in wanted


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
        (lambda d: d["settings"].update(trials=0), "trials must be a whole number, 1 or more"),
        (lambda d: d["settings"].update(models=[]), "models must be a list of distinct names"),
        (lambda d: d["settings"].update(sets=["paired", "paired"]), "sets must be a list of distinct names"),
        (lambda d: d.update(metrics=[]), "list of 'metrics'"),
        (lambda d: d["metrics"].append({"set": "main", "metric": "catch_rate"}), "set, metric and a role"),
        (lambda d: d["metrics"][0].update(set="no-such-set"), "is not one of the preregistered sets"),
        (lambda d: d.pop("families"), "needs 'families'"),
        (lambda d: d["families"].pop("model_vs_shortcut"), "families need 'model_vs_shortcut'"),
        (lambda d: d["families"]["model_vs_model"].pop("metrics"), "needs a list of 'metrics'"),
        (lambda d: d["families"]["model_vs_shortcut"].update(sets=["x"]), "names sets the run does not"),
        (lambda d: d.pop("thresholds"), "needs 'thresholds'"),
        (lambda d: d["thresholds"].update(alpha=0.5), "alpha must be a number above 0 and at most 0.2"),
        (lambda d: d["thresholds"].update(alpha=0), "alpha must be a number above 0 and at most 0.2"),
        (lambda d: d["thresholds"].update(alpha=True), "alpha must be a number above 0 and at most 0.2"),
        (lambda d: d["thresholds"].update(saturation_lower_bound=0.9), "thresholds are exactly: alpha"),
        (lambda d: d["thresholds"].update(min_share_judged=0.1), "min_share_judged must be a number"),
        (lambda d: d["thresholds"].pop("min_share_judged"), "exactly: alpha, min_share_judged"),
        (lambda d: d.pop("decision_rules"), "needs 'decision_rules'"),
        (lambda d: d["decision_rules"][0].pop("kind"), "needs id, kind and text"),
        (lambda d: d["decision_rules"][3].update(kind="machine"), "cannot check rule 'saturated_sets'"),
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


def test_audit_is_per_model_and_set_and_reports_deviations():
    prereg = load(SWEEP_3)
    computed = [
        (HAIKU, "paired", "catch_rate"),
        (HAIKU, "paired", SHORTCUT),
        (HAIKU, "main", "catch_rate"),
        (pair_name(HAIKU, OPUS), "main", "paired_difference.catch_rate"),
        (HAIKU, "main", "kappa"),
        ("claude-fable-5-1", "paired", "catch_rate"),
    ]
    settings = {**prereg["settings"], "trials": 2, "sets": ["paired", "main"]}
    out = audit(prereg, computed, settings)
    assert out["primary"] == [[HAIKU, "paired", "catch_rate"], [HAIKU, "paired", SHORTCUT]]
    assert out["secondary"] == [[HAIKU, "main", "catch_rate"]]
    assert out["not_preregistered"] == [
        [pair_name(HAIKU, OPUS), "main", "paired_difference.catch_rate"],
        [HAIKU, "main", "kappa"],
        ["claude-fable-5-1", "paired", "catch_rate"],  # an unregistered model on a registered set
    ]
    missing = out["preregistered_not_computed"]
    assert [OPUS, "paired", SHORTCUT] in missing  # a registered model missing from a primary set
    assert [OPUS, "paired", "catch_rate"] in missing and [HAIKU, "paired-fresh", "catch_rate"] in missing
    assert [HAIKU, "paired", "catch_rate"] not in missing
    # primary: 6 shortcut tests, 12 pair tests, 12 rates; 2 of them were computed
    assert out["primary_not_computed"] == 6 + 12 + 12 - 2
    assert out["family_sizes"] == {"model_vs_shortcut": 6, "model_vs_model": 12}
    assert out["thresholds"] == {"alpha": 0.05, "min_share_judged": 0.9}
    assert {d["setting"]: (d["preregistered"], d["run"]) for d in out["deviations"]} == {
        "trials": (3, 2),
        "sets": (prereg["settings"]["sets"], ["paired", "main"]),
    }
    # the same models in another order is not a deviation
    reordered = {**prereg["settings"], "models": prereg["settings"]["models"][::-1]}
    assert audit(prereg, computed, reordered)["deviations"] == []
