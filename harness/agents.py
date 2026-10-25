"""Agent backends.

One interface, three engines:

- ``claude``: Claude Code's own agent loop through the Claude Agent SDK.
- ``codex``: OpenAI's Codex agent loop through the Codex SDK.
- ``openrouter`` (and the offline ``deterministic`` demo): the harness's own
  small loop, driving the harness tools one model step at a time.

Every backend runs inside the isolated stage and reports each tool use back to
the run, so stop, path checks, and stage safety apply the same way to all of
them. Backends propose changes. Software verifies them.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from harness.authority import PathDenied, PermissionDenied, resolve_in_root
from harness.isolation import IsolationError, Stage
from harness.provider import SECRET_ENV, Completion, Provider
from harness.store import Store
from harness.tools import ToolError, execute, software_helper, tool_specs

SYSTEM = """You are a principal software agent working in an isolated copy of the user's repository.

Make the smallest change that fixes the objective, unblocks shipping, or removes a production risk.
No drive-by refactors and no work the user did not ask for.
Inspect the repository until you can name the change, then make it.
Never claim tests passed. Software runs the project's checks after you stop and decides the result.
Stay inside the working directory. Do not touch git internals. Do not request credentials.
"""

AGENT_NAMES = ("claude", "codex", "openrouter")


class RunStopped(Exception):
    """The user asked the run to stop."""


class UnsafeStage(Exception):
    def __init__(self, phase: str, error: IsolationError) -> None:
        super().__init__(str(error))
        self.phase = phase
        self.error = error


class AgentFailed(Exception):
    """The backend could not complete a turn."""


class StepLimit(Exception):
    """The backend used its whole step budget."""


@dataclass
class TurnContext:
    """What a backend may do to the run while it works."""

    stage: Stage
    store: Store
    run_id: str
    max_steps: int = 24
    check_timeout: int = 300
    helper: Callable[[str], str] | None = None
    quiet: bool = False

    def stop_requested(self) -> bool:
        current = self.store.get_run(self.run_id)
        return bool(current and current.stop_requested)

    def check(self, phase: str) -> None:
        """Raise before any further work if the run was stopped or the stage is unsafe."""
        if self.stop_requested():
            raise RunStopped()
        try:
            self.stage.assert_safe()
        except IsolationError as exc:
            raise UnsafeStage(phase, exc) from exc

    def note(self, name: str, arguments: dict | None) -> None:
        if self.quiet:
            return
        action = human_action(name, arguments)
        self.store.update_run(self.run_id, active_work=action)
        self.store.add_event(self.run_id, "action", action)

    def refresh(self) -> None:
        if self.quiet:
            return
        self.store.update_run(
            self.run_id,
            files_changed=self.stage.changed_files(),
            diff=self.stage.diff(),
        )

    def plan(self, arguments: dict) -> None:
        if self.quiet:
            return
        raw_steps = arguments.get("steps") or []
        steps = [str(step).strip() for step in raw_steps if str(step).strip()][:12]
        if not steps:
            return
        why = str(arguments.get("why") or "").strip()
        self.store.update_run(
            self.run_id,
            plan=steps,
            investigating=why or "Live plan recorded from evidence.",
            active_work=steps[0],
        )


class Agent(Protocol):
    name: str
    model: str | None

    def turn(self, prompt: str, ctx: TurnContext) -> str: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- loop


class LoopAgent:
    """The harness's own loop: one model step, then harness tools, until the model stops."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.name = provider.name
        self.model = getattr(provider, "model", None)
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM}]
        self.steps = 0
        self._bound: tuple[int, str] | None = None
        self._stored = 0

    @classmethod
    def restore(cls, provider: Provider, messages: list[dict], steps: int = 0) -> LoopAgent:
        """Rebuild an agent from a run's persisted conversation (the wake path)."""
        agent = cls(provider)
        if messages:
            agent.messages = [dict(message) for message in messages]
        agent.steps = steps
        return agent

    def _bind(self, ctx: TurnContext) -> None:
        """Attach this conversation to the run's session log on first use.

        The store holds a prefix of the conversation: a fresh agent starts
        at zero, a restored agent's messages are exactly the stored ones,
        so the count resumes where the log ends.
        """
        key = (id(ctx.store), ctx.run_id)
        if self._bound == key:
            return
        self._bound = key
        stored = ctx.store.get_messages(ctx.run_id)
        if stored == self.messages[: len(stored)]:
            self._stored = len(stored)
        else:
            self._stored = len(self.messages)

    def _persist(self, ctx: TurnContext) -> None:
        """Append any not-yet-stored messages to the run's session log."""
        if self._bound != (id(ctx.store), ctx.run_id):
            return
        while self._stored < len(self.messages):
            ctx.store.append_message(ctx.run_id, self.messages[self._stored])
            self._stored += 1
        ctx.store.save_session_state(ctx.run_id, {"steps": self.steps})

    def turn(self, prompt: str, ctx: TurnContext) -> str:
        self._bind(ctx)
        self.messages.append({"role": "user", "content": prompt})
        self._persist(ctx)
        tools = tool_specs("principal")
        while self.steps < ctx.max_steps:
            self.steps += 1
            self._persist(ctx)
            ctx.check("continuing the run")
            try:
                completion = self.provider.complete(self.messages, tools)
            except Exception as exc:
                raise AgentFailed(str(exc)) from exc
            if ctx.stop_requested():
                raise RunStopped()
            if not completion.tool_calls:
                return completion.text or ""
            self.messages.append(_assistant_message(completion))
            self._persist(ctx)
            for call in completion.tool_calls:
                if ctx.stop_requested():
                    skipped = f"{call.name} skipped because stop was requested"
                    ctx.store.add_event(ctx.run_id, "decision", skipped)
                    raise RunStopped()
                self.messages.append(self._run_tool(call.name, call.id, call.arguments or {}, ctx))
                self._persist(ctx)
                ctx.check("continuing after a tool call")
        raise StepLimit()

    def _run_tool(self, name: str, call_id: str, arguments: dict, ctx: TurnContext) -> dict:
        ctx.store.update_run(ctx.run_id, active_work=human_action(name, arguments))
        try:
            if name == "set_plan":
                ctx.plan(arguments)
            output = execute(
                name,
                arguments,
                stage=ctx.stage,
                role="principal",
                helper=ctx.helper,
                stopped=ctx.stop_requested(),
                timeout=ctx.check_timeout,
            )
            ctx.store.add_event(ctx.run_id, "action", human_action(name, arguments))
            if name in {"edit_file", "run_shell", "git_diff", "git_status"}:
                ctx.refresh()
            if name in {"git_diff", "git_status"}:
                ctx.store.add_event(ctx.run_id, "artifact", output[:2000])
            content = redact(output)[:8000]
        except (PathDenied, PermissionDenied, ToolError) as exc:
            content = f"{name} denied: {exc}"
            ctx.store.add_event(ctx.run_id, "decision", content)
        return {
            "role": "tool",
            "name": name,
            "tool_call_id": call_id,
            "arguments": arguments,
            "content": content,
        }

    def close(self) -> None:
        return None


def _assistant_message(completion: Completion) -> dict:
    return {
        "role": "assistant",
        "content": completion.text,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in completion.tool_calls
        ],
    }


# --------------------------------------------------------------------------- claude

CLAUDE_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "TodoWrite"]
CLAUDE_READ_TOOLS = ["Read", "Glob", "Grep"]
CLAUDE_PATH_KEYS = ("file_path", "notebook_path", "path")


class ClaudeAgent:
    """Claude Code's agent loop, confined to the stage by a PreToolUse hook."""

    name = "claude"

    def __init__(self, model: str | None = None, *, reviewer: bool = False) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AgentFailed("Claude needs ANTHROPIC_API_KEY")
        self.model = model if reviewer else (model or os.environ.get("HARNESS_MODEL") or None)
        self.session_id: str | None = None
        self.cost_usd = 0.0

    def turn(self, prompt: str, ctx: TurnContext) -> str:
        ctx.check("continuing the run")
        return asyncio.run(self._turn(prompt, ctx, tools=CLAUDE_TOOLS, max_turns=ctx.max_steps))

    async def _turn(self, prompt: str, ctx: TurnContext, *, tools: list[str], max_turns: int) -> str:
        from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock, query

        options = self._options(ctx, tools=tools, max_turns=max_turns)
        result: Any = None
        async for message in query(prompt=prompt, options=options):
            if ctx.stop_requested():
                raise RunStopped()
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        ctx.note(block.name, block.input)
                        if block.name == "TodoWrite":
                            todos = (block.input or {}).get("todos") or []
                            ctx.plan({"steps": [t.get("content", "") for t in todos if isinstance(t, dict)]})
            elif isinstance(message, ResultMessage):
                result = message
        if ctx.stop_requested():
            raise RunStopped()
        if result is None:
            raise AgentFailed("Claude Code ended without a result")
        self.session_id = result.session_id
        self._record_session(ctx)
        self.cost_usd += result.total_cost_usd or 0.0
        if result.subtype == "error_max_turns":
            raise StepLimit()
        if result.is_error:
            detail = "; ".join(result.errors or []) or result.result or result.subtype
            raise AgentFailed(f"Claude Code failed: {detail}")
        return result.result or ""

    def _record_session(self, ctx: TurnContext) -> None:
        """Write the vendor session id onto the run record.

        The SDK resumes from this id; keeping it only in this process's
        memory would lose the session the moment the harness restarts.
        """
        if self.session_id:
            ctx.store.update_run(ctx.run_id, vendor_session_id=self.session_id)

    def _options(self, ctx: TurnContext, *, tools: list[str], max_turns: int):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

        guard = stage_guard(ctx, writable=any(tool in tools for tool in ("Edit", "Write", "Bash")))

        async def pre_tool_use(hook_input, _tool_use_id, _context):
            reason = guard(hook_input.get("tool_name", ""), hook_input.get("tool_input") or {})
            if reason is None:
                return {}
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }

        async def post_tool_use(_hook_input, _tool_use_id, _context):
            ctx.refresh()
            return {}

        return ClaudeAgentOptions(
            cwd=str(ctx.stage.root),
            model=self.model,
            system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM},
            tools=tools,
            allowed_tools=tools,
            permission_mode="bypassPermissions",
            setting_sources=[],
            max_turns=max_turns,
            resume=self.session_id,
            hooks={
                "PreToolUse": [HookMatcher(hooks=[pre_tool_use])],
                "PostToolUse": [HookMatcher(hooks=[post_tool_use])],
            },
            env=agent_env(ctx.stage, keep="ANTHROPIC_API_KEY"),
        )

    def review(self, prompt: str, ctx: TurnContext) -> dict:
        reviewer = ClaudeAgent(self.model, reviewer=True)
        quiet = replace(ctx, quiet=True)
        text = asyncio.run(reviewer._turn(prompt, quiet, tools=CLAUDE_READ_TOOLS, max_turns=12))
        return parse_verdict(text)

    def close(self) -> None:
        return None


def stage_guard(ctx: TurnContext, *, writable: bool) -> Callable[[str, dict], str | None]:
    """Return a check that names why a tool call must be denied, or None to allow it."""

    def guard(tool_name: str, tool_input: dict) -> str | None:
        if ctx.stop_requested():
            return "stop was requested; no further work is allowed"
        try:
            ctx.stage.assert_safe()
        except IsolationError as exc:
            return f"the isolated stage failed a safety check: {exc}"
        if not writable and tool_name in {"Edit", "Write", "Bash", "NotebookEdit"}:
            return f"{tool_name} is not allowed for a reviewer"
        for key in CLAUDE_PATH_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                if value.startswith("~") or "$" in value:
                    return f"path is not allowed: {value}"
                try:
                    resolve_in_root(ctx.stage.root, value)
                except PathDenied as exc:
                    return str(exc)
        return None

    return guard


# --------------------------------------------------------------------------- codex


class CodexAgent:
    """OpenAI's Codex agent loop with its sandbox limited to the stage."""

    name = "codex"

    def __init__(self, model: str | None = None, *, reviewer: bool = False) -> None:
        if not codex_available():
            raise AgentFailed("Codex needs OPENAI_API_KEY or CODEX_API_KEY")
        self.model = model if reviewer else (model or os.environ.get("HARNESS_MODEL") or None)
        self._codex: Any = None
        self._thread: Any = None

    def _client(self, stage: Stage):
        from openai_codex import Codex, CodexConfig

        if self._codex is None:
            # CODEX_HOME beside the stage: the login below never touches the user's own ~/.codex.
            env = agent_env(stage, keep=None)
            env["CODEX_HOME"] = str(stage.home() / ".codex")
            Path(env["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
            self._codex = Codex(CodexConfig(cwd=str(stage.root), env=env))
            self._codex.login_api_key(os.environ.get("CODEX_API_KEY") or os.environ["OPENAI_API_KEY"])
        return self._codex

    def turn(self, prompt: str, ctx: TurnContext) -> str:
        from openai_codex import ApprovalMode, Sandbox

        ctx.check("continuing the run")
        try:
            codex = self._client(ctx.stage)
            if self._thread is None:
                self._thread = codex.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(ctx.stage.root),
                    developer_instructions=SYSTEM,
                    ephemeral=True,
                    model=self.model,
                    sandbox=Sandbox.workspace_write,
                )
                thread_id = getattr(self._thread, "id", None)
                if thread_id:
                    ctx.store.update_run(ctx.run_id, vendor_session_id=str(thread_id))
            return self._drive(self._thread.turn(prompt), ctx)
        except (RunStopped, UnsafeStage, AgentFailed):
            raise
        except Exception as exc:
            raise AgentFailed(f"Codex failed: {exc}") from exc

    def _drive(self, handle: Any, ctx: TurnContext) -> str:
        text = ""
        for event in handle.stream():
            if ctx.stop_requested():
                handle.interrupt()
                raise RunStopped()
            item = _codex_item(event)
            kind = getattr(item, "type", None)
            if event.method == "item/started" and kind == "commandExecution":
                ctx.note("Bash", {"command": getattr(item, "command", "")})
            elif event.method == "item/completed":
                if kind == "fileChange":
                    for change in getattr(item, "changes", None) or []:
                        ctx.note("Edit", {"file_path": getattr(change, "path", "a file")})
                    ctx.refresh()
                    ctx.check("continuing after a file change")
                elif kind == "commandExecution":
                    ctx.refresh()
                    ctx.check("continuing after a command")
                elif kind == "agentMessage":
                    text = getattr(item, "text", "") or text
            elif event.method == "turn/completed":
                turn = getattr(event.payload, "turn", None)
                error = getattr(turn, "error", None)
                status = getattr(getattr(turn, "status", None), "value", getattr(turn, "status", None))
                if error is not None or (status is not None and status != "completed"):
                    detail = getattr(error, "message", None) or error or status
                    raise AgentFailed(f"Codex turn ended {status or 'with an error'}: {detail}")
        return text

    def review(self, prompt: str, ctx: TurnContext) -> dict:
        from openai_codex import ApprovalMode, Sandbox

        try:
            codex = self._client(ctx.stage)
            thread = codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=str(ctx.stage.root),
                ephemeral=True,
                model=self.model,
                sandbox=Sandbox.read_only,
            )
            result = thread.run(prompt, output_schema=VERDICT_SCHEMA)
        except Exception as exc:
            raise AgentFailed(f"Codex review failed: {exc}") from exc
        return parse_verdict(result.final_response or "")

    def close(self) -> None:
        if self._codex is not None:
            try:
                self._codex.close()
            finally:
                self._codex = None
                self._thread = None


def _codex_item(event: Any) -> Any:
    item = getattr(event.payload, "item", None)
    return getattr(item, "root", item)


# --------------------------------------------------------------------------- review

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "ship": {"type": "boolean"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ship", "findings"],
    "additionalProperties": False,
}

REVIEW_PROMPT = """You are reviewing a change another agent made. You did not write it.

Decide whether it should ship. Block it only for a concrete defect: wrong behavior, a weakened or
deleted test, a security problem, or a change unrelated to the objective. Style is not a blocker.

Objective:
{objective}

Project checks (run by software, exit {exit_code}):
{checks}

Diff:
{diff}

Reply with JSON only: {{"ship": true or false, "findings": ["one line per blocking defect"]}}
"""


def review_prompt(objective: str, diff: str, exit_code: int | None, checks: str) -> str:
    return REVIEW_PROMPT.format(
        objective=objective.strip()[:4000],
        exit_code=exit_code,
        checks=checks[-4000:],
        diff=diff or "(no diff)",
    )


def parse_verdict(text: str) -> dict:
    """Read a reviewer verdict. Anything unreadable blocks the change."""
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    try:
        data = json.loads(raw[start : end + 1]) if start != -1 and end > start else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("ship"), bool):
        return {"ship": False, "findings": ["reviewer returned no readable verdict"]}
    findings = [str(item) for item in data.get("findings") or [] if str(item).strip()]
    return {"ship": data["ship"], "findings": findings[:20]}


# --------------------------------------------------------------------------- selection


@dataclass
class Backends:
    principal: Agent
    reviewer: Any | None = field(default=None)


def make_agent(name: str, provider: Provider | None = None) -> Agent:
    """Build the principal agent for a run."""
    if provider is not None and not hasattr(provider, "turn"):
        return LoopAgent(provider)
    if provider is not None:
        return provider  # an Agent supplied directly
    if name == "claude":
        return ClaudeAgent()
    if name == "codex":
        return CodexAgent()
    from harness.provider import get_provider

    return LoopAgent(get_provider(name))


def make_reviewer(principal: str) -> Any | None:
    """Pick the other vendor to review the change. HARNESS_REVIEWER overrides; "none" turns it off."""
    choice = (os.environ.get("HARNESS_REVIEWER") or "").strip().lower()
    if choice == "none":
        return None
    if not choice:
        if principal == "claude" and codex_available():
            choice = "codex"
        elif principal in {"codex", "openrouter"} and os.environ.get("ANTHROPIC_API_KEY"):
            choice = "claude"
        elif principal == "openrouter" and codex_available():
            choice = "codex"
        else:
            return None
    model = os.environ.get("HARNESS_REVIEWER_MODEL") or None
    if choice == "claude":
        return ClaudeAgent(model, reviewer=True)
    if choice == "codex":
        return CodexAgent(model, reviewer=True)
    raise AgentFailed(f"unknown HARNESS_REVIEWER: {choice}")


def codex_available() -> bool:
    return bool(os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def default_agent_name() -> str:
    """Explicit HARNESS_PROVIDER wins; otherwise the first configured key, in a fixed order."""
    pinned = (os.environ.get("HARNESS_PROVIDER") or "").strip().lower()
    if pinned:
        return pinned
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if codex_available():
        return "codex"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    return "deterministic"


def describe(name: str) -> dict:
    """Public description of the configured engine for the UI and the model question."""
    model = os.environ.get("HARNESS_MODEL") or None
    engines = {
        "claude": "Claude Code via the Claude Agent SDK",
        "codex": "Codex via the Codex SDK",
        "openrouter": "the harness loop on OpenRouter",
        "deterministic": "a local scripted agent for tests and the sample demo",
        "scripted": "a scripted test agent",
    }
    return {"name": name, "model": model, "engine": engines.get(name, name)}


# --------------------------------------------------------------------------- helpers


def human_action(name: str, arguments: dict | None) -> str:
    args = arguments or {}
    path = args.get("path") or args.get("file_path") or args.get("notebook_path")
    if name in {"list_files", "Glob"}:
        return f"Listed files ({args.get('pattern') or '*'})"
    if name in {"read_file", "Read"}:
        return f"Read {path or 'a file'}"
    if name in {"search", "Grep"}:
        return f"Searched for {args.get('query') or args.get('pattern') or 'a pattern'}"
    if name in {"edit_file", "Edit", "Write", "NotebookEdit"}:
        return f"Edited {path or 'a file'}"
    if name in {"run_shell", "Bash"}:
        return f"Ran {str(args.get('command') or 'a command')[:80]}"
    if name == "git_status":
        return "Inspected git status"
    if name == "git_diff":
        return "Inspected git diff"
    if name in {"set_plan", "TodoWrite"}:
        return "Updated the live plan"
    if name in {"delegate", "Task", "Agent"}:
        return f"Delegated: {str(args.get('objective') or args.get('description') or '')[:80]}"
    return name


PASS_ENV = (
    "PATH", "LANG", "LC_ALL", "TERM", "TZ", "TMPDIR",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
)


def agent_env(stage: Stage, keep: str | None) -> dict[str, str]:
    """A full environment for a vendor agent's process.

    Both SDKs merge this over the host environment, so every other inherited
    variable is blanked: host secrets and the real HOME never reach the agent's
    shell. Only networking basics, a HOME beside the stage, and the one vendor
    key the agent needs to call its own model remain.
    """
    env = {name: "" for name in os.environ}
    for name in PASS_ENV:
        if name in os.environ:
            env[name] = os.environ[name]
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)  # the SDK sets its own
    env["HOME"] = str(stage.home())
    if keep:
        env[keep] = os.environ.get(keep, "")
    return env


def redact(text: str) -> str:
    for name in SECRET_ENV:
        key = os.environ.get(name)
        if key:
            text = text.replace(key, "[redacted]")
    return text


def loop_helper(ctx_store: Store, run_id: str, stage: Stage) -> Callable[[str], str]:
    def helper(objective: str) -> str:
        ctx_store.add_event(run_id, "action", f"delegate: {objective}")
        return software_helper(objective, stage)

    return helper
