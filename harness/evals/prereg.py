"""A preregistration: what a sweep will measure and decide, written down before it runs.

The file is JSON so that the report can check itself against it. ``load`` reads and
validates one and returns it with the SHA-256 of its bytes, which the report prints: a
preregistration edited after the run no longer matches the hash in the run's record.
``audit`` then says, per model and set, which computed metrics were not preregistered,
which preregistered ones were not computed, and where the run's settings differ.

Two families of tests are registered, each Holm-adjusted on its own at the registered size:
``model_vs_shortcut`` (each model against the best listed shortcut on each named set) and
``model_vs_model`` (every two models on each named set and rate). Anything computed outside
them is exploratory and is reported unadjusted.
"""

from __future__ import annotations

import hashlib
import json
from itertools import combinations
from pathlib import Path

ROLES = ("primary", "secondary")
SETTINGS = ("models", "sets", "trials", "effort", "seed")
ANY_SET = "*"
MAX_ALPHA = 0.2
MIN_SHARE = 0.5
SHORTCUT_METRIC = "templates_right_vs_best_shortcut"
# Decision rules the report evaluates itself. Any other rule must be marked "judgement".
MACHINE_RULES = ("above_shortcut", "difference_found", "seen_published_pairs")
RULE_KINDS = ("machine", "judgement")


class PreregError(ValueError):
    """The preregistration is missing, unreadable, or leaves out something it must state."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PreregError(message)


def _names(value: object) -> bool:
    ok = isinstance(value, list) and value and all(isinstance(v, str) and v for v in value)
    return bool(ok) and len(set(value)) == len(value)


def pair_name(first: str, second: str) -> str:
    """One name for two models whichever is listed first."""
    return " vs ".join(sorted((first, second)))


def load(path: Path) -> dict:
    """Read a preregistration, refusing one that does not state everything the audit needs."""
    try:
        raw = Path(path).read_bytes()
        doc = json.loads(raw)
    except (OSError, ValueError) as error:
        raise PreregError(f"cannot read the preregistration {path}: {error}") from error
    _require(isinstance(doc, dict), "the preregistration must be a JSON object")
    for key in ("name", "written", "construct"):
        _require(isinstance(doc.get(key), str) and doc[key].strip(), f"the preregistration needs {key!r}")
    settings = doc.get("settings")
    _require(isinstance(settings, dict), "the preregistration needs 'settings'")
    for key in SETTINGS:
        _require(key in settings, f"the preregistered settings need {key!r}")
    for key in ("models", "sets"):
        _require(_names(settings[key]), f"the preregistered {key} must be a list of distinct names")
    trials = settings["trials"]
    _require(isinstance(trials, int) and not isinstance(trials, bool) and trials >= 1,
             "the preregistered trials must be a whole number, 1 or more")
    sets = settings["sets"]

    metrics = doc.get("metrics")
    _require(isinstance(metrics, list) and metrics, "the preregistration needs a list of 'metrics'")
    for entry in metrics:
        ok = isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in ("set", "metric"))
        _require(ok and entry.get("role") in ROLES, f"each metric needs set, metric and a role: {entry!r}")
        _require(entry["set"] == ANY_SET or entry["set"] in sets,
                 f"metric set {entry['set']!r} is not one of the preregistered sets")

    families = doc.get("families")
    _require(isinstance(families, dict), "the preregistration needs 'families'")
    for name, needs in (("model_vs_shortcut", ("sets",)), ("model_vs_model", ("sets", "metrics"))):
        family = families.get(name)
        _require(isinstance(family, dict), f"the preregistered families need {name!r}")
        for key in needs:
            _require(_names(family.get(key)), f"the family {name!r} needs a list of {key!r}")
        outside = [s for s in family["sets"] if s not in sets]
        _require(not outside, f"the family {name!r} names sets the run does not: {', '.join(outside)}")

    _require(isinstance(doc.get("slices"), list), "the preregistration needs 'slices'")
    thresholds = doc.get("thresholds")
    _require(isinstance(thresholds, dict), "the preregistration needs 'thresholds'")
    _require(set(thresholds) == {"alpha", "min_share_judged"},
             "the preregistered thresholds are exactly: alpha, min_share_judged")
    alpha, share = thresholds["alpha"], thresholds["min_share_judged"]
    ok = isinstance(alpha, (int, float)) and not isinstance(alpha, bool) and 0 < alpha <= MAX_ALPHA
    _require(ok, f"the preregistered alpha must be a number above 0 and at most {MAX_ALPHA}")
    ok = isinstance(share, (int, float)) and not isinstance(share, bool) and MIN_SHARE <= share <= 1
    _require(ok, f"the preregistered min_share_judged must be a number from {MIN_SHARE} to 1")

    rules = doc.get("decision_rules")
    _require(isinstance(rules, list) and rules, "the preregistration needs 'decision_rules'")
    for rule in rules:
        ok = isinstance(rule, dict) and all(isinstance(rule.get(k), str) and rule[k] for k in ("id", "text"))
        ok = ok and rule.get("kind") in RULE_KINDS
        _require(ok, f"each decision rule needs id, kind and text: {rule!r}")
        if rule["kind"] == "machine":
            _require(rule["id"] in MACHINE_RULES,
                     f"the report cannot check rule {rule['id']!r}; mark it 'judgement' or use one of "
                     f"{', '.join(MACHINE_RULES)}")
    return {**doc, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def family_members(prereg: dict) -> dict[str, list[tuple[str, str, str]]]:
    """Each family's registered tests as (model or pair, set, metric). Its length is the family size."""
    models = prereg["settings"]["models"]
    families = prereg["families"]
    shortcut = [(m, s, SHORTCUT_METRIC) for s in families["model_vs_shortcut"]["sets"] for m in models]
    pairs = [
        (pair_name(a, b), s, f"paired_difference.{metric}")
        for s in families["model_vs_model"]["sets"]
        for a, b in combinations(models, 2)
        for metric in families["model_vs_model"]["metrics"]
    ]
    return {"model_vs_shortcut": shortcut, "model_vs_model": pairs}


def registered(prereg: dict) -> dict[tuple[str, str, str], str]:
    """Every preregistered (model or pair, set, metric) and its role.

    A metric registered for set ``*`` is registered on every preregistered set. A row metric
    is registered for every preregistered model; a family's tests are primary.
    """
    found: dict[tuple[str, str, str], str] = {}
    settings = prereg["settings"]
    for members in family_members(prereg).values():
        for key in members:
            found[key] = "primary"
    for entry in prereg["metrics"]:
        for name in settings["sets"] if entry["set"] == ANY_SET else [entry["set"]]:
            for model in settings["models"]:
                key = (model, name, entry["metric"])
                if found.get(key) != "primary":
                    found[key] = entry["role"]
    return found


def deviations(prereg: dict, settings: dict) -> list[dict]:
    """Every setting of a run that differs from the one preregistered. Order of a list does not count."""
    out = []
    for key in SETTINGS:
        planned, actual = prereg["settings"][key], settings.get(key)
        same = sorted(planned) == sorted(actual or []) if isinstance(planned, list) else planned == actual
        if not same:
            out.append({"setting": key, "preregistered": planned, "run": actual})
    return out


def audit(prereg: dict, computed: list[tuple[str, str, str]], settings: dict) -> dict:
    """Check a run against its preregistration, per model and set.

    ``computed`` is every (model or pair, set, metric) the report holds; ``settings`` the
    run's own. A registered model with no result on a registered set is listed under
    ``preregistered_not_computed``; ``primary_not_computed`` counts the primary ones, and a
    report with any is incomplete.
    """
    wanted = registered(prereg)
    done = set(computed)
    by_role: dict[str, list[list[str]]] = {role: [] for role in ROLES}
    not_preregistered = []
    for key in computed:
        role = wanted.get(key)
        (by_role[role] if role else not_preregistered).append(list(key))
    not_computed = [list(key) for key in wanted if key not in done]
    return {
        "path": prereg["path"],
        "sha256": prereg["sha256"],
        "name": prereg["name"],
        "written": prereg["written"],
        "thresholds": prereg["thresholds"],
        "family_sizes": {name: len(members) for name, members in family_members(prereg).items()},
        "primary": by_role["primary"],
        "secondary": by_role["secondary"],
        "not_preregistered": not_preregistered,
        "preregistered_not_computed": not_computed,
        "primary_not_computed": sum(wanted[tuple(key)] == "primary" for key in not_computed),
        "deviations": deviations(prereg, settings),
    }
