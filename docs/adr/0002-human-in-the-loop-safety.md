# ADR 0002: Human-In-The-Loop Safety Model

Status: Accepted

## Context

OSROKBOT can move a real mouse and press real keys through a hardware input
driver. Fully automatic behavior is unsafe for new missions, new model
weights, changed UI layouts, and uncertain planner output.

## Decision

The runtime uses explicit autonomy levels:

- `L1 approve`: pointer-target actions require operator approval.
- `L2 trusted`: labels with enough local clean successes may auto-execute.
- `L3 auto`: validated pointer-target actions may execute without approval.

CAPTCHA detection pauses automation and requires a human. The system must not
solve, bypass, or automate CAPTCHA prompts.

## Consequences

- New or changed workflows default to `L1 approve`.
- Pointer actions remain visible and reviewable before execution.
- Local visual memory can improve throughput only after repeated clean
  successes.
- Safety-critical behavior is documented in README diagrams, runbooks, and the
  maintainer contract.

## Rejected Alternatives

- Default to full automation: rejected because it increases risk after prompt,
  weight, UI, or memory changes.
- Automate CAPTCHA handling: rejected because the correct policy is to pause
  for human review.
