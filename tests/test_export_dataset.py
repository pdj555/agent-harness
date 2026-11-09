"""The published dataset is rebuilt from the cases, not maintained by hand."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from harness.evals.cases import BLIND_SPOTS, CASES, CLOSED_BLIND_SPOTS

_spec = importlib.util.spec_from_file_location(
    "export_dataset", Path(__file__).resolve().parent.parent / "scripts" / "export_dataset.py"
)
export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export)


def test_test_split_is_the_published_cases():
    rows = export.splits()["test"]
    assert [r["id"] for r in rows] == [c.id for c in CASES]
    assert sum(r["is_sabotage"] for r in rows) == 27


def test_blind_spot_split_marks_caught_and_open():
    rows = export.splits()["blind_spots"]
    status = {r["id"]: r["spec_status"] for r in rows}
    assert status == {c.id: "caught" for c in CLOSED_BLIND_SPOTS} | {c.id: "open" for c in BLIND_SPOTS}


def test_export_writes_three_files(tmp_path):
    export.main(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["blind_spots.jsonl", "cases.jsonl", "repos.jsonl"]
