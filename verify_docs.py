"""Validate required OSROKBOT documentation artifacts.

The check is intentionally lightweight: it enforces the maintained documentation
surface without requiring network access or a full Markdown parser.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = [
    "README.md",
    "AGENTS.md",
    "SKILLS.md",
    "MEDIA_MAP.md",
    "docs/documentation-review-checklist.md",
    "docs/runbooks/README.md",
    "docs/runbooks/watchdog-restart.md",
    "docs/runbooks/captcha-manual-recovery.md",
    "docs/runbooks/emergency-stop.md",
    "docs/runbooks/secret-provisioning.md",
    "docs/runbooks/failure-triage.md",
    "docs/runbooks/run-handoff.md",
    "docs/adr/0001-planner-first-runtime.md",
    "docs/adr/0002-human-in-the-loop-safety.md",
]

RUNBOOK_REQUIRED_SECTIONS = [
    "## Trigger",
    "## Immediate Actions",
    "## Verification",
    "## Escalation",
]

REQUIRED_DOC_REFERENCES = [
    "docs/runbooks/watchdog-restart.md",
    "docs/runbooks/captcha-manual-recovery.md",
    "docs/runbooks/emergency-stop.md",
    "docs/runbooks/secret-provisioning.md",
    "docs/runbooks/failure-triage.md",
    "docs/runbooks/run-handoff.md",
    "docs/adr/0001-planner-first-runtime.md",
    "docs/adr/0002-human-in-the-loop-safety.md",
    "docs/documentation-review-checklist.md",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _missing_required_files() -> list[str]:
    missing: list[str] = []
    for relative_path in REQUIRED_FILES:
        if not (PROJECT_ROOT / relative_path).is_file():
            missing.append(f"Missing required documentation file: {relative_path}")
    return missing


def _check_runbook_sections() -> list[str]:
    failures: list[str] = []
    runbook_paths = sorted((PROJECT_ROOT / "docs" / "runbooks").glob("*.md"))
    for path in runbook_paths:
        if path.name == "README.md":
            continue
        text = _read(path)
        for section in RUNBOOK_REQUIRED_SECTIONS:
            if section not in text:
                failures.append(f"{path.relative_to(PROJECT_ROOT)} is missing {section}")
    return failures


def _check_adr_status() -> list[str]:
    failures: list[str] = []
    for path in sorted((PROJECT_ROOT / "docs" / "adr").glob("*.md")):
        if "Status: Accepted" not in _read(path):
            failures.append(f"{path.relative_to(PROJECT_ROOT)} must declare Status: Accepted")
    return failures


def _check_readme_diagrams() -> list[str]:
    readme = _read(PROJECT_ROOT / "README.md")
    if readme.count("```mermaid") < 2:
        return ["README.md must contain the planner workflow and recovery Mermaid diagrams"]
    return []


def _check_doc_references() -> list[str]:
    combined = "\n".join(
        _read(PROJECT_ROOT / name)
        for name in ["README.md", "AGENTS.md", "SKILLS.md"]
    )
    failures: list[str] = []
    for reference in REQUIRED_DOC_REFERENCES:
        if reference not in combined:
            failures.append(f"Maintained docs must reference {reference}")
    return failures


def check_docs() -> list[str]:
    """Return documentation verification failures."""
    failures: list[str] = []
    failures.extend(_missing_required_files())
    if failures:
        return failures
    failures.extend(_check_runbook_sections())
    failures.extend(_check_adr_status())
    failures.extend(_check_readme_diagrams())
    failures.extend(_check_doc_references())
    return failures


def main() -> int:
    failures = check_docs()
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1
    print("[OK] documentation artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
