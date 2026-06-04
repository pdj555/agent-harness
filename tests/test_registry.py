from __future__ import annotations

from pathlib import Path

from agent_harness.registry import discover_repositories, known_repo_specs


def test_discover_repositories_finds_immediate_repo_markers(tmp_path: Path) -> None:
    repo = tmp_path / "monte-carlo"
    repo.mkdir()
    (repo / "README.md").write_text("# Monte Carlo\n", encoding="utf-8")
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    (ignored / "package.json").write_text("{}", encoding="utf-8")

    discovered = discover_repositories(tmp_path)

    assert discovered == {"monte-carlo": repo}


def test_known_repo_specs_bind_to_namespace_root(tmp_path: Path) -> None:
    specs = known_repo_specs(tmp_path)

    assert [spec.name for spec in specs][:3] == [
        "agent-harness",
        "monte-carlo",
        "stock-sentiment-analysis",
    ]
    assert specs[1].path == tmp_path / "monte-carlo"
