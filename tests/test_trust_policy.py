from __future__ import annotations

from pathlib import Path

from agent_harness.trust_policy import (
    TRUST_POLICY_SCHEMA_VERSION,
    evaluate_repo_trust,
    load_trust_policy,
)


def test_evaluate_repo_trust_blocks_dirty_changes_without_policy() -> None:
    result = evaluate_repo_trust(
        {
            "adapters": [
                {
                    "name": "monte-carlo",
                    "repo_dirty": True,
                    "repo_branch": "main",
                    "repo_sha": "abc",
                    "repo_status": [" M decision.py"],
                    "repo_status_count": 1,
                }
            ]
        }
    )

    assert not result["ok"]
    assert result["blocking_change_count"] == 1
    assert result["blocking_changes"][0]["path"] == "decision.py"
    assert result["blocking_changes"][0]["reason"] == "no allow rule matched"


def test_evaluate_repo_trust_allows_explicit_policy_rules() -> None:
    policy = {
        "schema_version": TRUST_POLICY_SCHEMA_VERSION,
        "loaded": True,
        "allowed_dirty": [
            {
                "id": "docs-only",
                "repo": "agent-harness-ledger",
                "patterns": ["README.md", "docs/*.md"],
                "statuses": ["M"],
                "reason": "documentation-only runbook update",
            }
        ],
        "blocked_dirty": [],
    }

    result = evaluate_repo_trust(
        {
            "adapters": [
                {
                    "name": "agent-harness-ledger",
                    "repo_dirty": True,
                    "repo_branch": "main",
                    "repo_sha": "abc",
                    "repo_status": ["M README.md", " M docs/production.md"],
                    "repo_status_count": 2,
                }
            ]
        },
        trust_policy=policy,
    )

    assert result["ok"]
    assert result["allowed_change_count"] == 2
    assert result["blocking_change_count"] == 0
    assert {change["path"] for change in result["allowed_changes"]} == {
        "README.md",
        "docs/production.md",
    }
    assert {change["rule"] for change in result["allowed_changes"]} == {"docs-only"}


def test_block_rules_override_allow_rules() -> None:
    policy = {
        "schema_version": TRUST_POLICY_SCHEMA_VERSION,
        "loaded": True,
        "allowed_dirty": [{"id": "broad", "repo": "*", "patterns": ["*"]}],
        "blocked_dirty": [
            {
                "id": "capital-engine-code",
                "repo": "monte-carlo",
                "patterns": ["decision.py"],
                "reason": "capital-engine code must be committed before promotion",
            }
        ],
    }

    result = evaluate_repo_trust(
        {
            "adapters": [
                {
                    "name": "monte-carlo",
                    "repo_dirty": True,
                    "repo_status": [" M decision.py"],
                    "repo_status_count": 1,
                }
            ]
        },
        trust_policy=policy,
    )

    assert not result["ok"]
    assert result["blocking_changes"][0]["rule"] == "capital-engine-code"


def test_load_trust_policy_reads_json(tmp_path: Path) -> None:
    policy_path = tmp_path / "agent-harness.trust.json"
    policy_path.write_text(
        """{
  "schema_version": "agent-harness.trust-policy.v1",
  "allowed_dirty": [{"id": "docs", "repo": "*", "patterns": ["README.md"]}]
}
""",
        encoding="utf-8",
    )

    policy = load_trust_policy(policy_path)

    assert policy["loaded"]
    assert policy["source_path"] == str(policy_path.resolve())
    assert policy["allowed_dirty"][0]["id"] == "docs"
