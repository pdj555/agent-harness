"""The MCP server, driven in-process by the SDK's own client: no subprocess, no model, no key."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the mcp extra is not installed: uv sync --extra mcp")

import anyio  # noqa: E402
from harness.mcp_server import INPUTS, build_server  # noqa: E402
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
    assert set(tools["run_offline_monitor"].input_schema["properties"]) == {
        "monitor", "set", "cases", "fresh_seed",
    }


def test_an_agent_lists_the_sets_and_runs_the_spec_on_one(tmp_path):
    sets, run = _call(
        tmp_path,
        ("list_case_sets", {}),
        ("run_offline_monitor", {"monitor": "spec", "set": "blind-spots"}),
    )
    assert [s["name"] for s in sets["sets"]] == [
        "main", "spec-attacks", "blind-spots", "same-shape", "paired", "paired-fresh",
    ]
    assert (run["status"], run["exit_code"], run["cases"]) == ("fail", 1, 3)
    assert [f["id"] for f in run["failures"]] == [
        "ledger-bit-length", "ledger-digit-count", "ledger-reversed-env-value",
    ]
    report = Path(run["report_dir"])
    assert report.is_relative_to(tmp_path / ".harness" / "evals") and (report / "report.md").is_file()


def test_the_software_rules_run_on_chosen_cases_and_a_wrong_request_is_a_usage_error(tmp_path):
    caught, missed, bad_set, bad_monitor, bad_case, no_args, no_set, no_path, bad_seed = _call(
        tmp_path,
        ("run_offline_monitor", {"monitor": "software", "set": "main", "cases": ["tracker-skip-test"]}),
        ("run_offline_monitor", {"monitor": "software", "set": "main", "cases": ["tracker-special-case"]}),
        ("run_offline_monitor", {"monitor": "spec", "set": "no-such-set"}),
        ("run_offline_monitor", {"monitor": "claude", "set": "main"}),
        ("run_offline_monitor", {"monitor": "spec", "set": "main", "cases": ["no-such-case"]}),
        ("run_offline_monitor", {}),
        ("run_offline_monitor", {"monitor": "spec"}),
        ("read_sweep_summary", {}),
        ("run_offline_monitor", {"monitor": "spec", "set": "main", "fresh_seed": 3}),
    )
    assert (caught["status"], caught["cases"], caught["failures"]) == ("pass", 1, [])
    assert (missed["status"], missed["failures"][0]["id"]) == ("fail", "tracker-special-case")
    wrong_requests = (
        (bad_set, "unknown set"), (bad_monitor, "wrong argument monitor"), (bad_case, "unknown case"),
        (no_args, "missing argument: monitor, set"), (no_set, "missing argument: set"),
        (no_path, "missing argument: path"), (bad_seed, "a fresh seed only applies"),
    )
    for wrong, message in wrong_requests:
        assert (wrong["status"], wrong["exit_code"]) == ("usage_error", 2) and message in wrong["error"]


RATE = {"rate": 0.5, "ci95": [0.3, 0.7], "cases": 24, "templates": 24}
SWEEP = {
    "date": "d", "harness_commit": "abc", "models": ["m"], "sets": ["paired"], "trials": 3,
    "effort": "medium", "seed": 0, "max_usd": 10.0, "spent_usd": 1.5,
    "rows": [{"model": "m", "set": "paired", "catch_rate": RATE, "false_block_rate": RATE,
              "judged_trials": 144, "reviewer_errors": 0, "usd": 1.5, "templates": {}}],
    "analysis": {"prereg": None},
}


def test_a_sweep_summary_is_read_only_from_under_the_working_directory(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    (root / "sweep.json").write_text(json.dumps(SWEEP))
    (root / "broken.json").write_text(json.dumps({**SWEEP, "rows": [{"model": "m"}]}))
    (root / "empty.json").write_text("{}")
    (tmp_path / "outside.json").write_text(json.dumps(SWEEP))
    inside, outside, absolute, missing, broken, empty = _call(
        root,
        ("read_sweep_summary", {"path": "sweep.json"}),
        ("read_sweep_summary", {"path": "../outside.json"}),
        ("read_sweep_summary", {"path": str(tmp_path / "outside.json")}),
        ("read_sweep_summary", {"path": "none.json"}),
        ("read_sweep_summary", {"path": "broken.json"}),
        ("read_sweep_summary", {"path": "empty.json"}),
    )
    assert inside["status"] == "pass" and inside["sweep"]["rows"][0]["catch_rate"] == RATE
    assert "templates" not in inside["sweep"]["rows"][0]
    for refused in (outside, absolute):
        assert refused["status"] == "usage_error" and "outside the working directory" in refused["error"]
    assert missing["status"] == "usage_error" and "cannot read" in missing["error"]
    # a malformed file is a usage error through MCP as on the command line, never a pass
    assert broken["status"] == "usage_error" and "'set' is a required property" in broken["error"]
    assert empty["status"] == "usage_error" and "it has no rows" in empty["error"]


def test_an_agent_runs_the_spec_on_a_set_generated_from_its_seed(tmp_path, monkeypatch):
    from harness.evals import paired_generator

    for key, value in (("pairs", 3), ("steps", 300), ("sample", 300)):
        monkeypatch.setitem(paired_generator.FRESH_SEARCH, key, value)
    arguments = {"monitor": "spec", "set": "paired-fresh", "fresh_seed": 4}
    first, second = _call(tmp_path, ("run_offline_monitor", arguments), ("run_offline_monitor", arguments))
    assert (first["status"], first["cases"], first["fresh"]["seed"]) == ("fail", 6, 4)
    assert first["fresh"] == second["fresh"] and len(first["failures"]) == 3


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


def test_a_wrongly_typed_argument_is_a_usage_error_document(tmp_path):
    spec = {"monitor": "spec", "set": "main"}
    calls = [
        ("run_offline_monitor", {**spec, "set": 123}),
        ("run_offline_monitor", {**spec, "monitor": ["spec"]}),
        ("run_offline_monitor", {**spec, "cases": "tracker-clean"}),
        ("run_offline_monitor", {**spec, "cases": ["tracker-clean", 3]}),
        ("run_offline_monitor", {**spec, "set": "paired-fresh", "fresh_seed": "abc"}),
        ("run_offline_monitor", {**spec, "set": "paired-fresh", "fresh_seed": True}),
        ("run_offline_monitor", {**spec, "set": "paired-fresh", "fresh_seed": -1}),
        ("run_offline_monitor", {**spec, "set": "paired-fresh", "fresh_seed": 1.5}),
        ("read_sweep_summary", {"path": 123}),
        ("read_sweep_summary", {"path": "sweep\x00.json"}),
        ("read_sweep_summary", {"path": ""}),
    ]
    needs = ["wrong argument set", "wrong argument monitor", "wrong argument cases", "wrong argument cases.1",
             "wrong argument fresh_seed", "wrong argument fresh_seed", "wrong argument fresh_seed",
             "wrong argument fresh_seed", "wrong argument path", "wrong argument path", "wrong argument path"]
    for document, message in zip(_call(tmp_path, *calls), needs, strict=True):
        assert (document["status"], document["exit_code"]) == ("usage_error", 2), document
        assert message in document["error"]
    assert not (tmp_path / ".harness").exists()  # nothing ran


def test_a_sweep_summary_with_a_number_json_cannot_hold_is_refused(tmp_path):
    (tmp_path / "nan.json").write_text(json.dumps(SWEEP).replace('"spent_usd": 1.5', '"spent_usd": NaN'))
    (tmp_path / "bare.json").write_text(json.dumps({k: v for k, v in SWEEP.items() if k != "analysis"}))
    nan, bare = _call(tmp_path, ("read_sweep_summary", {"path": "nan.json"}),
                      ("read_sweep_summary", {"path": "bare.json"}))
    assert nan["status"] == "usage_error" and "NaN or Infinity" in nan["error"]
    assert bare["status"] == "usage_error" and "'analysis' is a required property" in bare["error"]


def test_an_unknown_or_missing_argument_is_a_usage_error_document(tmp_path):
    calls = [
        ("run_offline_monitor", {"monitor": "spec", "set": "main", "trials": 3}),
        ("run_offline_monitor", {"monitor": "spec"}),
        ("list_case_sets", {"verbose": True}),
        ("read_sweep_summary", {"path": "sweep.json", "strict": False}),
    ]
    needs = ["unknown argument: trials", "missing argument: set", "unknown argument: verbose",
             "unknown argument: strict"]
    for document, message in zip(_call(tmp_path, *calls), needs, strict=True):
        assert (document["status"], document["exit_code"]) == ("usage_error", 2), document
        assert message in document["error"]
        assert validate.problems(json.dumps(document)) == []
    assert not (tmp_path / ".harness").exists()  # nothing ran


def test_every_tool_declares_a_typed_input_that_admits_no_other_argument(tmp_path):
    async def scenario():
        async with Client(build_server(tmp_path)) as client:
            return {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}

    declared = anyio.run(scenario)
    assert declared == INPUTS
    for schema in declared.values():
        assert schema["additionalProperties"] is False
        assert all("type" in spec for spec in schema["properties"].values())
    run = declared["run_offline_monitor"]
    assert run["required"] == ["monitor", "set"]
    assert run["properties"]["monitor"]["enum"] == ["software", "spec"]
    assert run["properties"]["fresh_seed"]["type"] == "integer"


def test_a_sweep_that_supports_no_claim_reads_as_a_failed_summary(tmp_path):
    audit = {"deviations": [{"setting": "trials"}], "primary_not_computed": 0}
    deviated = {"saturation": [], "prereg": audit,
                "claims": {"allowed": False, "withheld": "deviated", "reason": "trials"}}
    (tmp_path / "deviated.json").write_text(json.dumps({**SWEEP, "analysis": deviated}))
    (tmp_path / "plain.json").write_text(json.dumps(SWEEP))
    failed, plain = _call(tmp_path, ("read_sweep_summary", {"path": "deviated.json"}),
                          ("read_sweep_summary", {"path": "plain.json"}))
    assert (failed["status"], failed["exit_code"]) == ("fail", 1)
    assert failed["claims"] == {"preregistered": True, "allowed": False,
                                "reason": "settings deviate from the preregistration: trials"}
    assert (plain["status"], plain["claims"]["preregistered"]) == ("pass", False)
    for document in (failed, plain):
        assert validate.problems(json.dumps(document)) == []
