"""Policy classification for dirty repository trust metadata."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


TRUST_POLICY_SCHEMA_VERSION = "agent-harness.trust-policy.v1"
DEFAULT_TRUST_POLICY_FILE = "agent-harness.trust.json"


def _list_field(payload: dict[str, Any], field: str, problems: list[str]) -> list[Any]:
    value = payload.get(field, [])
    if not isinstance(value, list):
        problems.append(f"{field} must be a list")
        return []
    return value


def _string_list_field(
    rule: dict[str, Any],
    field: str,
    *,
    default: list[str] | None = None,
) -> list[str]:
    value = rule.get(field, default or [])
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _validate_rule(
    rule: Any,
    *,
    field: str,
    index: int,
    require_expiration: bool,
) -> list[str]:
    prefix = f"{field}[{index}]"
    problems: list[str] = []
    if not isinstance(rule, dict):
        return [f"{prefix} must be a JSON object"]
    for required in ("id", "repo", "reason"):
        if not isinstance(rule.get(required), str) or not rule.get(required, "").strip():
            problems.append(f"{prefix}.{required} must be a non-empty string")
    patterns = _string_list_field(rule, "patterns", default=["*"])
    if not patterns:
        problems.append(f"{prefix}.patterns must contain at least one pattern")
    statuses = _string_list_field(rule, "statuses", default=["*"])
    if not statuses:
        problems.append(f"{prefix}.statuses must contain at least one status")
    for pattern in patterns:
        path = Path(pattern)
        if path.is_absolute() or ".." in path.parts:
            problems.append(f"{prefix}.patterns must stay repo-relative: {pattern}")
    expires_at = rule.get("expires_at")
    if require_expiration and not isinstance(expires_at, str):
        problems.append(f"{prefix}.expires_at is required for allowed dirty rules")
    if isinstance(expires_at, str):
        try:
            date.fromisoformat(expires_at)
        except ValueError:
            problems.append(f"{prefix}.expires_at must be YYYY-MM-DD")
    return problems


def validate_trust_policy(policy: dict[str, Any]) -> list[str]:
    """Return schema problems for a trust policy.

    The evaluator is fail-closed, but loaded policy files should also be precise:
    no anonymous rules, no path traversal, and no permanent dirty allow-list.
    """

    problems: list[str] = []
    if policy.get("schema_version") != TRUST_POLICY_SCHEMA_VERSION:
        problems.append("unsupported trust policy schema_version")
    allowed = _list_field(policy, "allowed_dirty", problems)
    blocked = _list_field(policy, "blocked_dirty", problems)
    for index, rule in enumerate(allowed):
        problems.extend(
            _validate_rule(
                rule,
                field="allowed_dirty",
                index=index,
                require_expiration=True,
            )
        )
    for index, rule in enumerate(blocked):
        problems.extend(
            _validate_rule(
                rule,
                field="blocked_dirty",
                index=index,
                require_expiration=False,
            )
        )
    return problems


def empty_trust_policy(*, source_path: Path | None = None) -> dict[str, Any]:
    """Return the default policy: every dirty change blocks promotion."""

    return {
        "schema_version": TRUST_POLICY_SCHEMA_VERSION,
        "source_path": str(source_path) if source_path else None,
        "loaded": False,
        "allowed_dirty": [],
        "blocked_dirty": [],
    }


def load_trust_policy(
    path: Path | None = None,
    *,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Load a trust policy.

    If ``path`` is omitted, ``agent-harness.trust.json`` in ``cwd`` is used when
    present. Missing default policy means no allow rules. Missing explicit policy
    is an operator error.
    """

    root = cwd or Path.cwd()
    source = path.expanduser() if path is not None else root / DEFAULT_TRUST_POLICY_FILE
    if not source.exists():
        if path is not None:
            raise FileNotFoundError(str(source))
        return empty_trust_policy(source_path=source)

    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("trust policy must be a JSON object")
    if payload.get("schema_version") != TRUST_POLICY_SCHEMA_VERSION:
        raise ValueError("unsupported trust policy schema_version")
    payload = dict(payload)
    payload["source_path"] = str(source.expanduser().resolve())
    payload["loaded"] = True
    payload.setdefault("allowed_dirty", [])
    payload.setdefault("blocked_dirty", [])
    problems = validate_trust_policy(payload)
    if problems:
        raise ValueError("; ".join(problems))
    return payload


def trust_policy_digest(policy: dict[str, Any]) -> str:
    """Return a stable digest for policy content."""

    scoped = {
        key: value
        for key, value in policy.items()
        if key not in {"source_path", "loaded"}
    }
    encoded = json.dumps(scoped, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _status_and_path(status_line: str) -> tuple[str, str]:
    status = status_line[:2].strip() or status_line[:2]
    raw_path = (
        status_line[3:].strip()
        if len(status_line) > 3 and status_line[2] == " "
        else status_line[2:].strip()
    )
    if " -> " in raw_path:
        raw_path = raw_path.rsplit(" -> ", 1)[1].strip()
    return status, raw_path.strip('"')


def _rule_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _patterns(rule: dict[str, Any]) -> list[str]:
    value = rule.get("patterns", ["*"])
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return ["*"]


def _statuses(rule: dict[str, Any]) -> list[str]:
    value = rule.get("statuses", ["*"])
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return ["*"]


def _repo_matches(rule: dict[str, Any], repo: str) -> bool:
    pattern = str(rule.get("repo") or "*")
    return fnmatch.fnmatchcase(repo, pattern)


def _status_matches(rule: dict[str, Any], status: str) -> bool:
    allowed = _statuses(rule)
    return (
        "*" in allowed
        or status in allowed
        or any(len(item) == 1 and item in status for item in allowed)
    )


def _path_matches(rule: dict[str, Any], path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in _patterns(rule))


def _expires_at(rule: dict[str, Any]) -> date | None:
    value = rule.get("expires_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.min


def _rule_expired(rule: dict[str, Any]) -> bool:
    expires_at = _expires_at(rule)
    if expires_at is None:
        return False
    return expires_at < datetime.now(timezone.utc).date()


def _match_rule(
    rules: list[dict[str, Any]],
    *,
    repo: str,
    path: str,
    status: str,
) -> dict[str, Any] | None:
    for rule in rules:
        if _rule_expired(rule):
            continue
        if (
            _repo_matches(rule, repo)
            and _status_matches(rule, status)
            and _path_matches(rule, path)
        ):
            return rule
    return None


def _policy_summary(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": policy.get("schema_version", TRUST_POLICY_SCHEMA_VERSION),
        "source_path": policy.get("source_path"),
        "loaded": bool(policy.get("loaded")),
        "digest": trust_policy_digest(policy),
        "allowed_rule_count": len(_rule_list(policy.get("allowed_dirty"))),
        "blocked_rule_count": len(_rule_list(policy.get("blocked_dirty"))),
    }


def evaluate_repo_trust(
    repo_trust: dict[str, Any],
    *,
    trust_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify dirty repo metadata under a trust policy."""

    policy = trust_policy or empty_trust_policy()
    allowed_rules = _rule_list(policy.get("allowed_dirty"))
    blocked_rules = _rule_list(policy.get("blocked_dirty"))
    adapters = repo_trust.get("adapters", []) if isinstance(repo_trust, dict) else []
    adapters = adapters if isinstance(adapters, list) else []

    allowed_changes: list[dict[str, Any]] = []
    blocking_changes: list[dict[str, Any]] = []
    repo_summaries: list[dict[str, Any]] = []

    for adapter in adapters:
        if not isinstance(adapter, dict):
            continue
        repo = str(adapter.get("name") or "unknown")
        status_lines = adapter.get("repo_status", [])
        status_lines = status_lines if isinstance(status_lines, list) else []
        repo_allowed = 0
        repo_blocking = 0

        if adapter.get("repo_dirty") is True and not status_lines:
            change = {
                "repo": repo,
                "repo_path": adapter.get("repo_path"),
                "repo_branch": adapter.get("repo_branch"),
                "repo_sha": adapter.get("repo_sha"),
                "status_line": None,
                "status": None,
                "path": None,
                "reason": "dirty repo has no status lines",
                "rule": None,
            }
            blocking_changes.append(change)
            repo_blocking += 1

        for raw_line in status_lines:
            if not isinstance(raw_line, str) or not raw_line.strip():
                continue
            status, path = _status_and_path(raw_line)
            base = {
                "repo": repo,
                "repo_path": adapter.get("repo_path"),
                "repo_branch": adapter.get("repo_branch"),
                "repo_sha": adapter.get("repo_sha"),
                "status_line": raw_line,
                "status": status,
                "path": path,
            }
            blocked_rule = _match_rule(
                blocked_rules,
                repo=repo,
                path=path,
                status=status,
            )
            if blocked_rule is not None:
                blocking_changes.append(
                    {
                        **base,
                        "reason": str(blocked_rule.get("reason") or "blocked by trust policy"),
                        "rule": blocked_rule.get("id"),
                    }
                )
                repo_blocking += 1
                continue

            allowed_rule = _match_rule(
                allowed_rules,
                repo=repo,
                path=path,
                status=status,
            )
            if allowed_rule is not None:
                allowed_changes.append(
                    {
                        **base,
                        "reason": str(allowed_rule.get("reason") or "allowed by trust policy"),
                        "rule": allowed_rule.get("id"),
                    }
                )
                repo_allowed += 1
                continue

            blocking_changes.append(
                {
                    **base,
                    "reason": "no allow rule matched",
                    "rule": None,
                }
            )
            repo_blocking += 1

        repo_summaries.append(
            {
                "repo": repo,
                "repo_dirty": adapter.get("repo_dirty"),
                "repo_branch": adapter.get("repo_branch"),
                "repo_sha": adapter.get("repo_sha"),
                "status_count": adapter.get("repo_status_count", len(status_lines)),
                "allowed_change_count": repo_allowed,
                "blocking_change_count": repo_blocking,
            }
        )

    return {
        "ok": not blocking_changes,
        "policy": _policy_summary(policy),
        "repo_summaries": repo_summaries,
        "allowed_change_count": len(allowed_changes),
        "blocking_change_count": len(blocking_changes),
        "allowed_changes": allowed_changes,
        "blocking_changes": blocking_changes,
    }
