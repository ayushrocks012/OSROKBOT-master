# Legacy Actions

These actions are retained only for historical reference while Phase 1 static
cleanup continues. The supported runtime enters `DynamicPlannerAction` through
`Classes/action_sets.py`; modules in this package must not be imported by new
production code.

If a behavior from this folder is still required, reimplement it behind the
planner-first path with explicit `Context` dependencies and `InputController`
execution.
