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

from jsonschema import Draft202012Validator
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools import Tool

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

# Each tool's input, declared in full: the type of every argument, which are required, and no
# others. The server checks a call against this itself, so that a missing, unknown or wrongly
# typed argument comes back as a usage_error document like any other wrong request.
SEED = {"type": "integer", "minimum": 0, "description": "seed for the generated set paired-fresh"}
INPUTS: dict[str, dict] = {
    "list_case_sets": {"type": "object", "properties": {}, "additionalProperties": False},
    "run_offline_monitor": {
        "type": "object",
        "properties": {
            "monitor": {"type": "string", "enum": ["software", "spec"]},
            "set": {"type": "string", "minLength": 1, "description": "a set name from list_case_sets"},
            "cases": {"type": "array", "items": {"type": "string", "minLength": 1},
                      "description": "case ids to limit the run to"},
            "fresh_seed": SEED,
        },
        "required": ["monitor", "set"],
        "additionalProperties": False,
    },
    "read_sweep_summary": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "pattern": "^[^\\u0000]+$",
                     "description": "a sweep.json under the working directory"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}
COMMANDS = {"list_case_sets": "eval sets", "run_offline_monitor": "eval monitor",
            "read_sweep_summary": "eval sweep-summary"}


def wrong_arguments(tool: str, arguments: dict[str, Any]) -> str | None:
    """What is wrong with a call's arguments against the tool's declared input; None when nothing is."""
    errors = sorted(Draft202012Validator(INPUTS[tool]).iter_errors(arguments), key=lambda e: list(e.path))
    if not errors:
        return None
    parts = []
    for error in errors:
        if error.validator == "required":
            missing = [name for name in error.validator_value if name not in arguments]
            parts.append(f"missing argument: {', '.join(missing)}")
        elif error.validator == "additionalProperties":
            unknown = sorted(set(arguments) - set(INPUTS[tool]["properties"]))
            parts.append(f"unknown argument: {', '.join(unknown)}")
        else:
            name = ".".join(str(part) for part in error.path) or "arguments"
            shown = error.message if len(error.message) < 200 else "it is not of the declared type"
            parts.append(f"wrong argument {name}: {shown}")
    return "; ".join(parts)


class DocumentTool(Tool):
    """A tool that answers a call it cannot run with a usage_error document, not a protocol error."""

    async def run(self, arguments: dict[str, Any], context: Any, convert_result: bool = False) -> Any:
        wrong = wrong_arguments(self.name, arguments)
        if wrong is None:
            return await super().run(arguments, context, convert_result)
        document = error_document(COMMANDS[self.name], EXIT_USAGE, wrong)
        return self.fn_metadata.convert_result(document) if convert_result else document


def build_server(root: Path | None = None) -> MCPServer:
    """The server. ``root`` is where reports go and the only place a sweep summary is read from."""
    root = (root or Path.cwd()).resolve()

    def list_case_sets() -> dict[str, Any]:
        """List the eval case sets (name, size, attacks, clean changes) and the offline monitors."""
        return sets_document()

    def run_offline_monitor(
        monitor: str,
        set: str,  # noqa: A002 - the name an agent passes
        cases: list[str] | None = None,
        fresh_seed: int | None = None,
    ) -> dict[str, Any]:
        """Run an offline monitor ("software" or "spec") on a case set and return its rates and failures.

        ``cases`` limits the run to those case ids. ``fresh_seed`` is for the generated set
        "paired-fresh" (about 30 seconds to build); without it a seed is drawn and returned
        under ``fresh``. Status "fail" means the monitor shipped an attack or blocked a clean
        change; ``failures`` names each such case. A missing, unknown or wrongly typed
        argument comes back as a usage_error document.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        out = root / ".harness" / "evals" / f"mcp-{monitor}-{set}-{stamp}"
        try:
            return run_offline(monitor, set, out, cases, fresh_seed)
        except UsageError as exc:
            return error_document("eval monitor", EXIT_USAGE, str(exc))

    def read_sweep_summary(path: str) -> dict[str, Any]:
        """Read a reviewer sweep's sweep.json, from under the working directory: settings, rows, analysis.

        Status "fail" means the sweep supports no claim: it deviated from its preregistration,
        is missing a primary result, or could not be matched to it; ``claims`` says which. A
        path outside the working directory and a file that is not a complete sweep summary
        come back as usage_error documents.
        """
        try:
            target = (root / path).resolve()
            if not target.is_relative_to(root):
                raise UsageError(f"{path} is outside the working directory")
            return sweep_summary_document(target)
        except (UsageError, OSError, ValueError) as exc:
            return error_document("eval sweep-summary", EXIT_USAGE, str(exc))

    tools = []
    for function in (list_case_sets, run_offline_monitor, read_sweep_summary):
        tool = DocumentTool.from_function(function)
        tool.parameters = INPUTS[tool.name]
        tools.append(tool)
    return MCPServer("agent-harness-evals", instructions=INSTRUCTIONS, tools=tools)


def serve() -> None:
    build_server().run("stdio")
