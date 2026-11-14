"""An MCP server over the evals that need no key, for an agent with no human at the keyboard.

Three tools, each returning the same JSON document ``harness eval ... --json`` prints
(``harness/evals/eval_output.schema.json``):

- ``list_case_sets``: the case sets and the offline monitors.
- ``run_offline_monitor``: run the software rules or the executable spec on a set.
- ``read_sweep_summary``: read a reviewer sweep's ``sweep.json`` from under the working directory.

Nothing here calls a model, spends money, or changes a repository. Reports are written
under ``.harness/evals`` in the working directory, as the command line does.

    uv sync --extra mcp
    uv run harness mcp          # stdio
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from harness.evals.agent import (
    EXIT_USAGE,
    UsageError,
    error_document,
    run_offline,
    sets_document,
    sweep_summary_document,
)


def _is_name(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)  # True is not a seed


def _wrong_arguments(*checks: tuple[str, object, Any, str]) -> str | None:
    """What is wrong with a tool's arguments, missing ones first; None when all are usable."""
    missing = [name for name, value, valid, _ in checks if value is None and not valid(None)]
    if missing:
        return f"missing argument: {', '.join(missing)}"
    wrong = [f"{name} must be {needs}" for name, value, valid, needs in checks if not valid(value)]
    return f"wrong argument: {'; '.join(wrong)}" if wrong else None


INSTRUCTIONS = (
    "Offline evals of a code-review gate. Every tool returns one JSON document with status "
    "pass, fail, usage_error or infrastructure_error and the matching exit_code 0 to 3. "
    "No tool calls a model or costs money."
)


def build_server(root: Path | None = None) -> MCPServer:
    """The server. ``root`` is where reports go and the only place a sweep summary is read from."""
    root = (root or Path.cwd()).resolve()
    server = MCPServer("agent-harness-evals", instructions=INSTRUCTIONS)

    @server.tool()
    def list_case_sets() -> dict[str, Any]:
        """List the eval case sets (name, size, attacks, clean changes) and the offline monitors."""
        return sets_document()

    @server.tool()
    def run_offline_monitor(
        monitor: Any = None,
        set: Any = None,  # noqa: A002 - the name an agent passes
        cases: Any = None,
        fresh_seed: Any = None,
    ) -> dict[str, Any]:
        """Run an offline monitor ("software" or "spec") on a case set and return its rates and failures.

        ``monitor`` and ``set`` are required strings. ``cases`` is an optional list of case
        ids to limit the run to. ``fresh_seed`` is an optional whole number for the generated
        set "paired-fresh" (about 30 seconds to build); without it a seed is drawn and
        returned under ``fresh``. Status "fail" means the monitor shipped an attack or blocked
        a clean change; ``failures`` names each such case. A missing or wrongly typed argument
        comes back as a usage_error document.
        """
        wrong = _wrong_arguments(
            ("monitor", monitor, _is_name, "a non-empty string"),
            ("set", set, _is_name, "a non-empty string"),
            ("cases", cases, lambda v: v is None or (isinstance(v, list) and all(map(_is_name, v))),
             "a list of case ids"),
            ("fresh_seed", fresh_seed, lambda v: v is None or (_is_whole(v) and v >= 0),
             "a whole number, 0 or more"),
        )
        if wrong:
            return error_document("eval monitor", EXIT_USAGE, wrong)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        out = root / ".harness" / "evals" / f"mcp-{monitor}-{set}-{stamp}"
        try:
            return run_offline(monitor, set, out, cases, fresh_seed)
        except UsageError as exc:
            return error_document("eval monitor", EXIT_USAGE, str(exc))

    @server.tool()
    def read_sweep_summary(path: Any = None) -> dict[str, Any]:
        """Read a reviewer sweep's sweep.json, from under the working directory: settings, rows, analysis.

        ``path`` is a required string. A missing or wrongly typed argument, a path outside the
        working directory and a file that is not a complete sweep summary each come back as a
        usage_error document.
        """
        wrong = _wrong_arguments(("path", path, lambda v: _is_name(v) and "\x00" not in v, "a file path"))
        if wrong:
            return error_document("eval sweep-summary", EXIT_USAGE, wrong)
        try:
            target = (root / path).resolve()
            if not target.is_relative_to(root):
                raise UsageError(f"{path} is outside the working directory")
            return sweep_summary_document(target)
        except (UsageError, OSError, ValueError) as exc:
            return error_document("eval sweep-summary", EXIT_USAGE, str(exc))

    return server


def serve() -> None:
    build_server().run("stdio")
