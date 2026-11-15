"""Check a ``harness eval ... --json`` document against its schema.

    uv run harness eval monitor --monitor spec --set paired --json > out.json; code=$?
    uv run python scripts/validate_eval_json.py out.json --exit-code "$code"

Exits 0 when the document is valid and matches what was asked for, 1 when it is not, and 2
when the file cannot be read, with the reason on stderr. ``--exit-code`` checks the
document's ``exit_code`` against the one the command returned, and ``--status`` its ``status``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness.evals.agent import STATUS
from harness.evals.agent import problems as schema_problems


def _not_json(name: str):
    raise ValueError(f"{name} is not a JSON number")


def problems(text: str, *, exit_code: int | None = None, status: str | None = None) -> list[str]:
    """Everything wrong with ``text`` as an eval document; empty when it is valid."""
    try:
        document = json.loads(text, parse_constant=_not_json)
    except ValueError as error:
        return [f"not one JSON document: {error}"]
    found = schema_problems(document)
    if not found and exit_code is not None and document["exit_code"] != exit_code:
        found.append(f"the command exited {exit_code} but its document says {document['exit_code']}")
    if not found and status is not None and document["status"] != status:
        found.append(f"status is {document['status']}, not {status}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("document", type=Path, help="the file holding the command's stdout")
    parser.add_argument("--exit-code", type=int, default=None, help="the exit code the command returned")
    parser.add_argument("--status", choices=list(STATUS.values()), default=None)
    args = parser.parse_args(argv)
    try:
        text = args.document.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"validate_eval_json: cannot read {args.document}: {error}", file=sys.stderr)
        return 2
    found = problems(text, exit_code=args.exit_code, status=args.status)
    for line in found:
        print(f"validate_eval_json: {line}", file=sys.stderr)
    if not found:
        print(f"valid: {args.document}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
