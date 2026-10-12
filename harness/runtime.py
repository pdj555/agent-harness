from __future__ import annotations

from harness.agents import (
    AGENT_NAMES,
    Agent,
    AgentFailed,
    RunStopped,
    StepLimit,
    TurnContext,
    UnsafeStage,
    describe,
    loop_helper,
    make_agent,
    make_reviewer,
    redact,
    review_prompt,
)
from harness.config import Config
from harness.isolation import IsolationError, create_stage
from harness.provider import Provider
from harness.review import run_review
from harness.store import TERMINAL_STATUSES, Store
from harness.triage import scan as triage_scan
from harness.verification import run_checks

DEFAULT_PLAN = [
    "Name the change",
    "Inspect the repository",
    "Make the smallest change that does it",
    "Prove it with the project's tests",
    "Independent review",
]

SUGGESTED_OBJECTIVE = (
    "Find the most important broken or missing behavior in this repository, "
    "fix it in isolation, and prove it with the project's tests."
)


def configuration_answer(objective: str, engine: str) -> str | None:
    text = " ".join((objective or "").lower().split())
    if not any(
        phrase in text
        for phrase in ("what model", "which model", "what provider", "which provider", "what llm")
    ):
        return None
    info = describe(engine)
    if engine in {"deterministic", "scripted"}:
        return (
            f"This run uses the {engine} provider: {info['engine']}. It is not a live model. "
            "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY for a real model."
        )
    model = f" with model {info['model']}" if info["model"] else ""
    return f"This run uses the {engine} provider ({info['engine']}){model}."


def execute_run(
    run_id: str,
    *,
    store: Store,
    config: Config,
    provider: Provider | Agent | None = None,
) -> None:
    """Drive one run to a terminal status.

    Every exit path lands in ``completed``, ``failed``, or ``stopped``. An
    unexpected exception is recorded as a failed run instead of dying inside the
    background thread and leaving the row in ``running`` forever.
    """
    try:
        _execute_run(run_id, store=store, config=config, provider=provider)
    except Exception as exc:
        current = store.get_run(run_id)
        if current is None or current.status in TERMINAL_STATUSES:
            raise
        store.update_run(
            run_id,
            status="failed",
            result=f"Harness error: {type(exc).__name__}: {exc}",
            blockers=["internal error"],
            active_work="",
        )


def _execute_run(
    run_id: str, *, store: Store, config: Config, provider: Provider | Agent | None
) -> None:
    run = store.get_run(run_id)
    if run is None:
        return
    engine = getattr(provider, "name", None) or config.provider_name
    answer = configuration_answer(run.objective, engine)
    if answer:
        store.update_run(
            run_id,
            status="completed",
            plan=["Answer from runtime configuration"],
            investigating="",
            active_work="",
            result=answer,
            files_changed=[],
            diff="",
            verification={
                "passed": True,
                "command": "not applicable",
                "exit_code": 0,
                "output": "No repository checks; no code change was requested.",
            },
            review={
                "role": "reviewer",
                "passed": True,
                "summary": "No repository change requested.",
                "findings": [],
                "files_reviewed": [],
            },
        )
        store.add_event(run_id, "result", answer)
        return
    repo = store.repo_by_id(run.repo_id, config.workspace_roots)
    if repo is None:
        store.update_run(
            run_id,
            status="failed",
            result="Repository is not in the allowlist.",
            blockers=["repository not allowlisted"],
        )
        return
    try:
        stage = create_stage(repo.path, config.data_dir / "stages", run_id)
    except IsolationError as exc:
        store.update_run(run_id, status="failed", result=str(exc), blockers=["isolation"])
        return

    scan_text = triage_scan(stage.root)
    store.update_run(
        run_id,
        status="running",
        stage_path=str(stage.root),
        plan=list(DEFAULT_PLAN),
        investigating="Reading tests, open markers, and recent commits.",
        active_work="Software scan",
    )
    store.add_event(run_id, "evidence", scan_text[:2000])

    ctx = TurnContext(
        stage=stage,
        store=store,
        run_id=run_id,
        max_steps=config.max_steps,
        check_timeout=config.check_timeout,
        helper=loop_helper(store, run_id, stage),
    )
    try:
        agent = make_agent(engine, provider)
        reviewer = make_reviewer(engine) if engine in AGENT_NAMES else None
    except Exception as exc:
        store.update_run(run_id, status="failed", result=f"Provider failed: {exc}", blockers=["provider"])
        return
    store.add_event(run_id, "decision", _engine_line(agent, reviewer))
    try:
        _drive(run_id, store=store, config=config, stage=stage, ctx=ctx, agent=agent, reviewer=reviewer,
               objective=run.objective, scan_text=scan_text)
    except RunStopped:
        store.update_run(run_id, status="stopped", result="Stopped by the user.", active_work="")
    except UnsafeStage as exc:
        _fail_unsafe_stage(store, run_id, exc.phase, exc.error)
    except AgentFailed as exc:
        store.update_run(run_id, status="failed", result=f"Provider failed: {exc}", blockers=["provider"])
    except StepLimit:
        store.update_run(
            run_id,
            status="failed",
            result="Reached the step limit without verification evidence.",
            blockers=["step limit"],
        )
    finally:
        for backend in (agent, reviewer):
            if backend is not None:
                backend.close()


def _engine_line(agent: Agent, reviewer: object | None) -> str:
    model = f" ({agent.model})" if getattr(agent, "model", None) else ""
    review = f"; {reviewer.name} reviews the diff" if reviewer is not None else ""
    return f"Principal agent: {agent.name}{model}{review}."


def _drive(
    run_id: str,
    *,
    store: Store,
    config: Config,
    stage,
    ctx: TurnContext,
    agent: Agent,
    reviewer,
    objective: str,
    scan_text: str,
) -> None:
    prompt = (
        f"{objective}\n\n"
        "SOFTWARE SCAN (not model output):\n"
        f"{scan_text}\n"
        "Act on the most important item. Record a live plan before editing."
    )
    for attempt in range(config.max_repairs + 1):
        final_text = agent.turn(prompt, ctx)
        ctx.check("verification")
        store.update_run(run_id, active_work="Running verification.")
        checked_digest = stage.content_digest()
        evidence = run_checks(
            stage.root,
            python=stage.python(),
            timeout=config.check_timeout,
            command=config.check_command,
        )
        store.update_run(
            run_id,
            verification=evidence.as_dict(),
            files_changed=stage.changed_files(),
            diff=stage.diff(),
            checks=[evidence.as_dict()],
        )
        store.add_event(
            run_id,
            "evidence",
            f"verification passed={evidence.passed} exit={evidence.exit_code}",
        )
        ctx.check("independent review")
        if stage.content_digest() != checked_digest:
            _fail_changed(store, run_id, "The isolated stage changed during verification; run it again.",
                          "stage changed during verification")
            return
        verification = {**evidence.as_dict(), "stage_digest": checked_digest}
        store.update_run(run_id, verification=verification, checks=[verification])
        review_with = None
        if reviewer is not None and evidence.passed:
            store.update_run(run_id, active_work=f"Independent review by {reviewer.name}.")

            def review_with(diff: str, _reviewer=reviewer, _evidence=evidence) -> dict:
                return _reviewer.review(
                    review_prompt(objective, diff, _evidence.exit_code, _evidence.output), ctx
                )

        review = run_review(stage, evidence, review_with)
        store.update_run(run_id, review=review)
        store.add_event(run_id, "decision", review["summary"])

        if evidence.passed and review.get("passed"):
            if stage.content_digest() != checked_digest:
                _fail_changed(store, run_id, "The isolated stage changed since verification; run it again.",
                              "stage changed since verification")
                return
            if config.auto_publish:
                ctx.check("publish")
                try:
                    stage.publish(expected_digest=checked_digest)
                except IsolationError as exc:
                    _fail_unsafe_stage(store, run_id, "publish", exc)
                    return
                store.mark_published(run_id)
                store.add_event(run_id, "result", "Published verified files into the selected repository.")
            store.update_run(
                run_id,
                status="completed",
                result=final_text.strip() or "Verified changes are in the isolated worktree.",
                active_work="",
                investigating="Checks passed. Independent review recorded.",
            )
            return

        if attempt == config.max_repairs:
            break
        store.update_run(run_id, active_work="Repairing after failed checks.")
        if not evidence.passed:
            prompt = (
                "Software verification failed. The model cannot mark this passed. "
                f"Output:\n{evidence.output[-4000:]}\nRepair the defect."
            )
        else:
            prompt = (
                "An independent reviewer blocked the change:\n- "
                + "\n- ".join(review.get("findings") or ["no reason given"])
                + "\nFix these defects."
            )

    store.update_run(
        run_id,
        status="failed",
        result="Checks did not pass." if not evidence.passed else "Independent review blocked the change.",
        blockers=["verification failed"] if not evidence.passed else ["review blocked"],
        active_work="",
    )


def _fail_changed(store: Store, run_id: str, result: str, blocker: str) -> None:
    store.update_run(run_id, status="failed", result=result, blockers=[blocker], active_work="")


def _fail_unsafe_stage(store: Store, run_id: str, phase: str, exc: IsolationError) -> None:
    store.add_event(run_id, "decision", f"Stage safety check failed before {phase}: {exc}")
    current = store.get_run(run_id)
    blockers = list(current.blockers) if current else []
    if "unsafe stage" not in blockers:
        blockers.append("unsafe stage")
    store.update_run(
        run_id,
        status="failed",
        result="Run stopped because the isolated stage failed safety checks.",
        blockers=blockers,
        active_work="",
        investigating="",
        stage_path=None,
    )


_redact = redact
