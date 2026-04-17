# ADR 0001: Planner-First Runtime

Status: Accepted

## Context

The original project shape allowed root-level media templates and gameplay
actions to drive automation directly. That design produced duplicated actions,
stale image references, and weak boundaries between perception, planning, and
input execution.

## Decision

OSROKBOT uses a planner-first runtime:

```text
UI mission -> Context -> StateMachine -> DynamicPlannerAction -> DynamicPlanner -> InputController
```

Legacy gameplay templates remain quarantined under `Classes/Actions/legacy/`
for reference only. New production behavior must enter through
`ActionSets.dynamic_planner()` and the guarded planner action services.

## Consequences

- Perception, planning, approval, execution, memory feedback, and retention are
  explicit boundaries.
- `dynamic_planner.py` remains side-effect free and cannot send input.
- `InputController` remains the only hardware input path.
- Tests and static checks focus on the supported planner-first runtime instead
  of legacy template actions.

## Rejected Alternatives

- Keep expanding template actions: rejected because it increases duplicated
  state-specific behavior and hides safety differences between actions.
- Let the planner execute coordinates directly: rejected because raw
  coordinates bypass target validation, autonomy gates, and input guardrails.
