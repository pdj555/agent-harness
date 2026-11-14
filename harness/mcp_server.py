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
    def run_offline_monitor(monitor: str, set: str, cases: list[str] | None = None) -> dict[str, Any]:  # noqa: A002
        """Run an offline monitor ("software" or "spec") on a case set and return its rates and failures.

        ``cases`` limits the run to those case ids. Status "fail" means the monitor shipped an
        attack or blocked a clean change; ``failures`` names each such case.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        out = root / ".harness" / "evals" / f"mcp-{monitor}-{set}-{stamp}"
        try:
            return run_offline(monitor, set, out, cases)
        except UsageError as exc:
            return error_document("eval monitor", EXIT_USAGE, str(exc))

    @server.tool()
    def read_sweep_summary(path: str) -> dict[str, Any]:
        """Read a reviewer sweep's sweep.json, from under the working directory: settings, rows, analysis."""
        target = (root / path).resolve()
        try:
            if not target.is_relative_to(root):
                raise UsageError(f"{path} is outside the working directory")
            return sweep_summary_document(target)
        except UsageError as exc:
            return error_document("eval sweep-summary", EXIT_USAGE, str(exc))

    return server


def serve() -> None:
    build_server().run("stdio")
