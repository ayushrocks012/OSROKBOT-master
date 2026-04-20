# ADR 0004: Runtime Composition Root And Legacy Retirement

Status: Accepted

## Context

The planner-first runtime had already removed production dependencies on legacy
template actions, but the repository still carried the archived legacy package
and UIController still built most runtime collaborators directly. That left the
supervisor controller too close to startup wiring and kept dead code, unused
dependencies, and stale configuration surface area in the active tree.

## Decision

OSROKBOT now uses an explicit runtime composition root in
`Classes/runtime_composition.py` for the supervisor console, and the archived
legacy action package has been removed from the repository.

The composition root now owns:

- shared detector, window-handler, and memory collaborators,
- `OS_ROKBOT` construction,
- per-run `Context` factory wiring, and
- injected recovery/state/input/config factories for deterministic seams.

The shared planner decision verdict now lives in
`Classes/planner_decision_policy.py` so execution readiness, Fix-required
review, and rejection reasons are derived once across planner, UI, and
approval services.

## Consequences

Positive:

- `UIController` is no longer the implicit composition root.
- Recovery, state-monitor, and input seams reuse the same startup-owned
  collaborators in runtime code and tests.
- Dead legacy actions, legacy-only dependencies, and email settings no longer
  distort the supported runtime surface.
- Planner safety policy is less likely to drift between UI and execution code.

Tradeoffs:

- Startup wiring is more explicit and spans one additional module.
- Documentation and repo-hygiene checks must now enforce that removed legacy
  paths are not reintroduced.
