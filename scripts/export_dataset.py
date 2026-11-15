"""Write the eval cases as JSON Lines for the Hugging Face dataset.

Two splits, both rebuilt from harness.evals.cases at the current commit:

- test: the 33 published cases, 6 honest fixes and 27 sabotaged changes.
- blind_spots: attacks built to get past the spec check, with ``spec_status``
  "caught" for closed cases and "open" for the ones that still get through.

Usage: uv run python scripts/export_dataset.py OUT_DIR
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harness.evals.cases import BLIND_SPOTS, CANARY, CASES, CLEAN, CLOSED_BLIND_SPOTS, REPOS, Case


def row(case: Case) -> dict:
    repo = REPOS[case.repo]
    return {
        "id": case.id,
        "repo": case.repo,
        "objective": repo.objective,
        "label": case.label,
        "is_sabotage": case.label != CLEAN,
        "difficulty": case.difficulty,
        "note": case.note,
        "base_files": dict(repo.files),
        "changed_files": dict(case.files),
        "canary": CANARY,
    }


def splits() -> dict[str, list[dict]]:
    blind = [row(c) | {"spec_status": "caught"} for c in CLOSED_BLIND_SPOTS]
    blind += [row(c) | {"spec_status": "open"} for c in BLIND_SPOTS]
    return {"test": [row(c) for c in CASES], "blind_spots": blind}


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = {"test": "cases.jsonl", "blind_spots": "blind_spots.jsonl"}
    for split, rows in splits().items():
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        (out / names[split]).write_text(text, encoding="utf-8")
        print(f"{split}: {len(rows)} rows -> {out / names[split]}")
    repos = "".join(
        json.dumps(
            {"repo": r.name, "objective": r.objective, "files": r.files, "canary": CANARY}, ensure_ascii=False
        )
        + "\n"
        for r in REPOS.values()
    )
    (out / "repos.jsonl").write_text(repos, encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dist/dataset")
