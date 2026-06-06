from __future__ import annotations

from pathlib import Path

from agent_harness.trust_policy import (
    DEFAULT_TRUST_POLICY_FILE,
    TRUST_POLICY_SCHEMA_VERSION,
    evaluate_repo_trust,
    load_trust_policy,
    validate_trust_policy,
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
  "allowed_dirty": [
    {
      "id": "docs",
      "repo": "*",
      "patterns": ["README.md"],
      "statuses": ["M"],
      "reason": "docs only",
      "expires_at": "2027-01-01"
    }
  ]
}
""",
        encoding="utf-8",
    )

    policy = load_trust_policy(policy_path)

    assert policy["loaded"]
    assert policy["source_path"] == str(policy_path.resolve())
    assert policy["allowed_dirty"][0]["id"] == "docs"


def test_load_trust_policy_uses_default_file(tmp_path: Path) -> None:
    policy_path = tmp_path / DEFAULT_TRUST_POLICY_FILE
    policy_path.write_text(
        """{
  "schema_version": "agent-harness.trust-policy.v1",
  "allowed_dirty": [],
  "blocked_dirty": [
    {
      "id": "code",
      "repo": "*",
      "patterns": ["*.py"],
      "statuses": ["*"],
      "reason": "code must be committed"
    }
  ]
}
""",
        encoding="utf-8",
    )

    policy = load_trust_policy(cwd=tmp_path)

    assert policy["loaded"]
    assert policy["source_path"] == str(policy_path.resolve())
    assert policy["blocked_dirty"][0]["id"] == "code"


def test_load_trust_policy_rejects_permanent_allow_rule(tmp_path: Path) -> None:
    policy_path = tmp_path / DEFAULT_TRUST_POLICY_FILE
    policy_path.write_text(
        """{
  "schema_version": "agent-harness.trust-policy.v1",
  "allowed_dirty": [
    {
      "id": "broad",
      "repo": "*",
      "patterns": ["*"],
      "statuses": ["*"],
      "reason": "too broad"
    }
  ],
  "blocked_dirty": []
}
""",
        encoding="utf-8",
    )

    try:
        load_trust_policy(cwd=tmp_path)
    except ValueError as exc:
        assert "expires_at is required" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected invalid policy to fail")


def test_validate_trust_policy_rejects_path_escape_pattern() -> None:
    problems = validate_trust_policy(
        {
            "schema_version": TRUST_POLICY_SCHEMA_VERSION,
            "allowed_dirty": [
                {
                    "id": "escape",
                    "repo": "*",
                    "patterns": ["../*.py"],
                    "statuses": ["M"],
                    "reason": "bad",
                    "expires_at": "2027-01-01",
                }
            ],
            "blocked_dirty": [],
        }
    )

    assert any("repo-relative" in problem for problem in problems)


def test_tracked_production_policy_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    policy = load_trust_policy(root / DEFAULT_TRUST_POLICY_FILE)

    assert policy["loaded"]
    assert policy["allowed_dirty"][0]["id"] == "agent-harness-docs-only"
    assert {
        rule["id"] for rule in policy["blocked_dirty"]
    } >= {
        "agent-harness-code-tests-config",
        "monte-carlo-capital-engine",
        "stock-sentiment-signal-engine",
    }
