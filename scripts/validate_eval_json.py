"""Check a ``harness eval ... --json`` document against its schema.

    uv run harness eval monitor --monitor spec --set paired --json > out.json; code=$?
    uv run python scripts/validate_eval_json.py out.json --exit-code "$code"

Exits 0 when the document is valid and matches what was asked for, 1 when it is not, with
the reason on stderr. ``--exit-code`` checks the document's ``exit_code`` against the one
the command returned, and ``--status`` checks its ``status``. Needs ``jsonschema`` (the
``dev`` extra).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness.evals.agent import SCHEMA_PATH, STATUS


def problems(text: str, *, exit_code: int | None = None, status: str | None = None) -> list[str]:
    """Everything wrong with ``text`` as an eval document; empty when it is valid."""
    from jsonschema import Draft202012Validator

    try:
        document = json.loads(text)
    except ValueError as error:
        return [f"not one JSON document: {error}"]
    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    found = [
        f"{'/'.join(str(part) for part in error.absolute_path) or 'document'}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    ]
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
    found = problems(args.document.read_text(encoding="utf-8"), exit_code=args.exit_code, status=args.status)
    for line in found:
        print(f"validate_eval_json: {line}", file=sys.stderr)
    if not found:
        print(f"valid: {args.document}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
