from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from harness.agents import RunStopped, UnsafeStage
from harness.isolation import Stage, _git
from harness.verification import Verification

TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec"}
TEST_NAME_MARKERS = ("_test.", ".test.", ".spec.", "_spec.")
SKIP_DIRS = {".git", ".harness", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}


def _is_test_path(rel: str) -> bool:
    """True for test files in the common Python, JavaScript, Go, and Ruby layouts."""
    path = Path(rel)
    name = path.name
    if name.startswith("test_") or name.startswith("conftest."):
        return True
    if any(marker in name for marker in TEST_NAME_MARKERS):
        return True
    return any(part in TEST_DIR_NAMES for part in path.parts[:-1])


def _test_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.is_file() and _is_test_path(str(rel)):
            found.append(path)
    return found


def inspect_change(stage: Stage) -> list[str]:
    files = stage.changed_files()
    diff = (stage.diff() or "").strip()
    findings: list[str] = []
    if not _test_files(stage.root):
        findings.append("no test files remain in the isolated worktree")
    impl_changed = [name for name in files if not _is_test_path(name)]
    tests_changed = [name for name in files if _is_test_path(name)]
    if not files and not diff:
        findings.append("review found no isolated change")
    if tests_changed and not impl_changed:
        findings.append("tests changed without an implementation change")
    return findings


MAX_REVIEW_DIFF = 60_000
MAX_NEW_FILE = 20_000


def review_diff(stage: Stage) -> str:
    """Everything that would publish: tracked edits plus every new file, in full or marked as cut."""
    parts = [stage.diff() or ""]
    untracked = _git(["ls-files", "--others", "--exclude-standard", "-z", "--", "."], stage.root)
    for rel in sorted(name for name in (untracked.stdout or "").split("\0") if name):
        path = stage.root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            parts.append(f"new file {rel} (binary or unreadable; read it if it matters)\n")
            continue
        body = text[:MAX_NEW_FILE]
        cut = "" if len(text) <= MAX_NEW_FILE else f"\n[... {len(text) - MAX_NEW_FILE} more characters]"
        parts.append(f"new file {rel}\n+++ b/{rel}\n{body}{cut}\n")
    diff = "\n".join(part for part in parts if part)
    if len(diff) > MAX_REVIEW_DIFF:
        omitted = len(diff) - MAX_REVIEW_DIFF
        diff = (
            diff[:MAX_REVIEW_DIFF]
            + f"\n[DIFF TRUNCATED: {omitted} characters not shown. Read the changed files before approving.]"
        )
    return diff


def run_review(
    stage: Stage,
    verification: Verification,
    review_with: Callable[[str], dict] | None = None,
) -> dict:
    """Software-gated review, plus an independent model when one is configured.

    Software findings block on their own. The model reviewer only sees changes
    software already accepts, and an unreadable verdict blocks.
    """
    files = stage.changed_files()
    findings = inspect_change(stage)
    reviewer = "software"
    if not findings and review_with is not None:
        try:
            verdict = review_with(review_diff(stage))
        except (RunStopped, UnsafeStage):
            raise
        except Exception as exc:  # a reviewer that cannot answer must not approve
            verdict = {"ship": False, "findings": [f"reviewer failed: {exc}"]}
        reviewer = "software + model"
        if not verdict.get("ship"):
            findings = list(verdict.get("findings") or ["reviewer blocked the change"])
    passed = not findings
    if passed:
        summary = "Implementation changed. Tests remain. No blocking findings."
        if reviewer != "software":
            summary += " Independent model approved."
    else:
        summary = "Review blocked: " + "; ".join(findings)
    return {
        "role": "reviewer",
        "passed": passed,
        "summary": summary,
        "findings": findings,
        "files_reviewed": files,
        "checks_passed": bool(verification.passed),
        "reviewer": reviewer,
    }
