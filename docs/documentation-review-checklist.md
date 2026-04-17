# Documentation Review Checklist

Use this checklist before handing back changes that affect behavior,
architecture, safety rules, configuration, runtime data paths, or operator
workflow.

## Required Checks

- `README.md` describes the current user-facing behavior.
- `AGENTS.md` describes the current maintainer contract.
- `SKILLS.md` describes the current capability surface.
- `MEDIA_MAP.md` is updated when protected media paths or media policy change.
- README Mermaid diagrams are updated when runtime flow, approval gating, or
  recovery behavior changes.
- Operator runbooks are updated when setup, watchdog, CAPTCHA, emergency-stop,
  secrets, or triage steps change.
- ADRs are added or amended when architectural decisions change.
- New active runtime modules, classes, and non-trivial public methods use
  Google-style docstrings.

## Local Gate

Run:

```powershell
python verify_docs.py
```

This checks that required documentation files exist, README diagrams are still
present, and the maintained docs link to the runbooks and ADRs.
