"""A preregistration: what a sweep will measure, written down before it runs.

The file is JSON so that the report can check itself against it. ``load`` reads and
validates one and returns it with the SHA-256 of its bytes, which the report prints: a
preregistration edited after the run no longer matches the hash in the run's record.
``audit`` then says which computed metrics were not preregistered, which preregistered
ones were not computed, and where the run's settings differ from the ones written down.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROLES = ("primary", "secondary")
SETTINGS = ("models", "sets", "trials", "effort", "seed")
ANY_SET = "*"


class PreregError(ValueError):
    """The preregistration is missing, unreadable, or leaves out something it must state."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PreregError(message)


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
    metrics = doc.get("metrics")
    _require(isinstance(metrics, list) and metrics, "the preregistration needs a list of 'metrics'")
    for entry in metrics:
        ok = isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in ("set", "metric"))
        _require(ok and entry.get("role") in ROLES, f"each metric needs set, metric and a role: {entry!r}")
    _require(any(e["role"] == "primary" for e in metrics), "the preregistration names no primary metric")
    comparisons = doc.get("comparisons")
    _require(isinstance(comparisons, dict), "the preregistration needs 'comparisons'")
    for key in ("sets", "metrics"):
        value = comparisons.get(key)
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        _require(ok, f"the preregistered comparisons need a list of {key!r}")
    _require(isinstance(doc.get("slices"), list), "the preregistration needs 'slices'")
    thresholds = doc.get("thresholds")
    _require(isinstance(thresholds, dict), "the preregistration needs 'thresholds'")
    for key in ("alpha", "saturation_lower_bound"):
        value = thresholds.get(key)
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 1
        _require(ok, f"the preregistered threshold {key!r} must be a number between 0 and 1")
    return {**doc, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def registered(prereg: dict) -> dict[tuple[str, str], str]:
    """Every preregistered (set, metric) and its role; a comparison is ``paired_difference.<rate>``."""
    found: dict[tuple[str, str], str] = {}
    for entry in prereg["metrics"]:
        key = (entry["set"], entry["metric"])
        if found.get(key) != "primary":
            found[key] = entry["role"]
    compared = prereg["comparisons"]
    for name in compared["sets"]:
        for metric in compared["metrics"]:
            found.setdefault((name, f"paired_difference.{metric}"), compared.get("role", "primary"))
    return found


def audit(prereg: dict, computed: list[tuple[str, str]], settings: dict) -> dict:
    """Check a run against its preregistration.

    ``computed`` is every (set, metric) the report holds; ``settings`` the run's own. A
    metric registered for set ``*`` covers that metric on every set.
    """
    wanted = registered(prereg)
    roles: dict[str, list[list[str]]] = {role: [] for role in ROLES}
    not_preregistered = []
    for name, metric in computed:
        role = wanted.get((name, metric)) or wanted.get((ANY_SET, metric))
        if role:
            roles[role].append([name, metric])
        else:
            not_preregistered.append([name, metric])
    done = set(computed)
    not_computed = [[n, metric] for n, metric in wanted if n != ANY_SET and (n, metric) not in done]
    deviations = []
    for key in SETTINGS:
        planned, actual = prereg["settings"][key], settings.get(key)
        same = sorted(planned) == sorted(actual or []) if isinstance(planned, list) else planned == actual
        if not same:
            deviations.append({"setting": key, "preregistered": planned, "run": actual})
    return {
        "path": prereg["path"],
        "sha256": prereg["sha256"],
        "name": prereg["name"],
        "written": prereg["written"],
        "primary": roles["primary"],
        "secondary": roles["secondary"],
        "not_preregistered": not_preregistered,
        "preregistered_not_computed": not_computed,
        "deviations": deviations,
    }
