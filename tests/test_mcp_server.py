"""The MCP server, driven in-process by the SDK's own client: no subprocess, no model, no key."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the mcp extra is not installed: uv sync --extra mcp")

import anyio  # noqa: E402
from harness.mcp_server import build_server  # noqa: E402
from mcp import Client, StdioServerParameters  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "validate_eval_json", Path(__file__).resolve().parent.parent / "scripts" / "validate_eval_json.py"
)
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)


def _call(root: Path, *calls: tuple[str, dict]) -> list[dict]:
    """Call tools on a server rooted at ``root``; every answer must be a valid eval document."""

    async def scenario() -> list[dict]:
        answers = []
        async with Client(build_server(root)) as client:
            for name, arguments in calls:
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content
                document = result.structured_content
                assert validate.problems(json.dumps(document)) == [], document
                assert json.loads(result.content[0].text) == document  # the text form is the same document
                answers.append(document)
        return answers

    return anyio.run(scenario)


def test_the_server_offers_three_described_tools(tmp_path):
    async def scenario():
        async with Client(build_server(tmp_path)) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in anyio.run(scenario)}
    assert set(tools) == {"list_case_sets", "run_offline_monitor", "read_sweep_summary"}
    assert all(tool.description for tool in tools.values())
    assert set(tools["run_offline_monitor"].input_schema["required"]) == {"monitor", "set"}


def test_an_agent_lists_the_sets_and_runs_the_spec_on_one(tmp_path):
    sets, run = _call(
        tmp_path,
        ("list_case_sets", {}),
        ("run_offline_monitor", {"monitor": "spec", "set": "blind-spots"}),
    )
    assert [s["name"] for s in sets["sets"]] == ["main", "blind-spots", "same-shape", "paired"]
    assert (run["status"], run["exit_code"], run["cases"]) == ("fail", 1, 3)
    assert [f["id"] for f in run["failures"]] == [
        "ledger-bit-length", "ledger-digit-count", "ledger-reversed-env-value",
    ]
    report = Path(run["report_dir"])
    assert report.is_relative_to(tmp_path / ".harness" / "evals") and (report / "report.md").is_file()


def test_the_software_rules_run_on_chosen_cases_and_a_wrong_request_is_a_usage_error(tmp_path):
    caught, missed, bad_set, bad_monitor, bad_case = _call(
        tmp_path,
        ("run_offline_monitor", {"monitor": "software", "set": "main", "cases": ["tracker-skip-test"]}),
        ("run_offline_monitor", {"monitor": "software", "set": "main", "cases": ["tracker-special-case"]}),
        ("run_offline_monitor", {"monitor": "spec", "set": "no-such-set"}),
        ("run_offline_monitor", {"monitor": "claude", "set": "main"}),
        ("run_offline_monitor", {"monitor": "spec", "set": "main", "cases": ["no-such-case"]}),
    )
    assert (caught["status"], caught["cases"], caught["failures"]) == ("pass", 1, [])
    assert (missed["status"], missed["failures"][0]["id"]) == ("fail", "tracker-special-case")
    for wrong, message in ((bad_set, "unknown set"), (bad_monitor, "unknown offline monitor"),
                           (bad_case, "unknown case")):
        assert (wrong["status"], wrong["exit_code"]) == ("usage_error", 2) and message in wrong["error"]


def test_a_sweep_summary_is_read_only_from_under_the_working_directory(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    record = {
        "date": "d", "harness_commit": "abc", "models": ["m"], "sets": ["paired"], "trials": 3, "rows": [
            {"model": "m", "set": "paired", "catch_rate": {"rate": 0.5}, "false_block_rate": {"rate": 0.0},
             "templates": {}},
        ], "analysis": {"prereg": None},
    }
    (root / "sweep.json").write_text(json.dumps(record))
    (tmp_path / "outside.json").write_text(json.dumps(record))
    inside, outside, absolute, missing = _call(
        root,
        ("read_sweep_summary", {"path": "sweep.json"}),
        ("read_sweep_summary", {"path": "../outside.json"}),
        ("read_sweep_summary", {"path": str(tmp_path / "outside.json")}),
        ("read_sweep_summary", {"path": "none.json"}),
    )
    assert inside["status"] == "pass" and inside["sweep"]["rows"][0]["catch_rate"] == {"rate": 0.5}
    assert "templates" not in inside["sweep"]["rows"][0]
    for refused in (outside, absolute):
        assert refused["status"] == "usage_error" and "outside the working directory" in refused["error"]
    assert missing["status"] == "usage_error" and "cannot read" in missing["error"]


def test_harness_mcp_serves_the_same_tools_over_stdio(tmp_path):
    """The shipped entry point as a real subprocess, spoken to over its stdin and stdout."""

    async def scenario():
        command = {"command": sys.executable, "args": ["-m", "harness", "mcp"]}
        params = StdioServerParameters(**command, cwd=str(tmp_path))
        async with Client(params) as client:
            names = [tool.name for tool in (await client.list_tools()).tools]
            arguments = {"monitor": "software", "set": "main", "cases": ["slug-skip-test"]}
            result = await client.call_tool("run_offline_monitor", arguments)
            return names, result.structured_content

    names, document = anyio.run(scenario)
    assert names == ["list_case_sets", "run_offline_monitor", "read_sweep_summary"]
    assert validate.problems(json.dumps(document)) == []
    assert (document["status"], document["cases"]) == ("pass", 1)
    assert Path(document["report_dir"]).is_relative_to(tmp_path.resolve())
