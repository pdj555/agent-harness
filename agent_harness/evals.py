"""Production checks for saved run packets."""

from __future__ import annotations

from typing import Any

from agent_harness.packets import validate_run_packet


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def evaluate_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Evaluate whether a packet is production-usable."""

    checks: list[dict[str, Any]] = []
    schema_problems = validate_run_packet(packet)
    checks.append(
        _check(
            "schema_valid",
            not schema_problems,
            "; ".join(schema_problems) if schema_problems else "packet schema and digest are valid",
        )
    )

    loops = packet.get("ranked_loops")
    top_loop = loops[0] if isinstance(loops, list) and loops else {}
    checks.append(
        _check(
            "ranked_loops_present",
            isinstance(loops, list) and bool(loops),
            "ranked loops exist" if loops else "no ranked implementation loops",
        )
    )
    checks.append(
        _check(
            "risk_gate_first",
            isinstance(top_loop, dict) and top_loop.get("repo") == "monte-carlo",
            f"top loop is {top_loop.get('repo') if isinstance(top_loop, dict) else 'missing'}",
        )
    )

    monte_run = (
        packet.get("engine_runs", {}).get("monte_carlo")
        if isinstance(packet.get("engine_runs"), dict)
        else None
    )
    checks.append(
        _check(
            "monte_carlo_executed",
            isinstance(monte_run, dict) and bool(monte_run.get("ok")),
            "monte-carlo run succeeded"
            if isinstance(monte_run, dict) and monte_run.get("ok")
            else "monte-carlo run missing or failed",
        )
    )

    backtest_run = (
        packet.get("engine_runs", {}).get("monte_carlo_backtest")
        if isinstance(packet.get("engine_runs"), dict)
        else None
    )
    ran_backtest = bool(packet.get("inputs", {}).get("ran_backtest"))
    checks.append(
        _check(
            "walk_forward_backtest_executed",
            (not ran_backtest) or (isinstance(backtest_run, dict) and bool(backtest_run.get("ok"))),
            "walk-forward backtest succeeded"
            if isinstance(backtest_run, dict) and backtest_run.get("ok")
            else "walk-forward backtest not requested"
            if not ran_backtest
            else "walk-forward backtest missing or failed",
        )
    )
    if isinstance(backtest_run, dict) and backtest_run.get("ok"):
        summary = backtest_run.get("payload", {}).get("summary", {})
        if isinstance(summary, dict):
            checks.append(
                _check(
                    "backtest_excess_cash_nonnegative",
                    float(summary.get("excess_return_vs_cash", 0.0) or 0.0) >= 0.0,
                    f"excess_return_vs_cash={summary.get('excess_return_vs_cash')}",
                )
            )

    stress_tests = packet.get("stress_tests")
    checks.append(
        _check(
            "stress_tests_passed",
            isinstance(stress_tests, dict) and bool(stress_tests.get("ok")),
            f"worst_margin={stress_tests.get('worst_margin')}"
            if isinstance(stress_tests, dict)
            else "stress tests missing",
        )
    )

    risk_controls = packet.get("risk_controls", {})
    max_position = risk_controls.get("max_position_weight", 0.0)
    concentration_weight = risk_controls.get("concentration_weight", 0.50)
    min_cash_buffer = risk_controls.get("min_cash_buffer_when_concentrated", 0.20)
    action_plan = {}
    if isinstance(monte_run, dict):
        payload = monte_run.get("payload", {})
        if isinstance(payload, dict):
            action_plan = payload.get("action_plan", {}) if isinstance(payload.get("action_plan"), dict) else {}
    primary_pick = action_plan.get("primary_pick") if isinstance(action_plan, dict) else None
    weight = primary_pick.get("weight") if isinstance(primary_pick, dict) else None
    checks.append(
        _check(
            "position_cap_respected",
            weight is None or float(weight) <= float(max_position),
            f"primary weight {weight}, max {max_position}",
        )
    )
    cash_weight = action_plan.get("cash_weight") if isinstance(action_plan, dict) else None
    concentrated = weight is not None and float(weight) >= float(concentration_weight)
    checks.append(
        _check(
            "cash_buffer_respected",
            not concentrated or (cash_weight is not None and float(cash_weight) >= float(min_cash_buffer)),
            f"cash {cash_weight}, minimum {min_cash_buffer} when weight >= {concentration_weight}",
        )
    )

    adapters = packet.get("adapters") if isinstance(packet.get("adapters"), dict) else {}
    dirty_repos = [
        name
        for name, adapter in adapters.items()
        if isinstance(adapter, dict) and adapter.get("repo_dirty") is True
    ]
    checks.append(
        _check(
            "repo_fingerprints_present",
            all(
                isinstance(adapter, dict)
                and "repo_sha" in adapter
                and "repo_branch" in adapter
                and "repo_dirty" in adapter
                and "repo_status" in adapter
                and "repo_status_count" in adapter
                and "repo_status_truncated" in adapter
                for adapter in adapters.values()
            ),
            "all adapters include repo sha, branch, dirty flag, and status lines"
            if adapters
            else "no adapter fingerprints present",
        )
    )

    passed = sum(1 for check in checks if check["passed"])
    score = passed / len(checks) if checks else 0.0
    return {
        "ok": all(check["passed"] for check in checks),
        "score": round(score, 4),
        "passed": passed,
        "total": len(checks),
        "dirty_repos": dirty_repos,
        "checks": checks,
    }
