# OSROKBOT Developer Guidelines

These rules are for AI agents and developers maintaining this repository.
Preserve the explicit state-machine architecture. Favor small, inspectable
changes over broad rewrites.

## Architecture Boundaries

- `Classes/UI.py` owns the PyQt overlay and creates the per-run `Context`.
- `Classes/context.py` is the only shared runtime state object.
- `Classes/OS_ROKBOT.py` owns worker threads, pause/stop events, and global
  pre-action blocker checks.
- `Classes/state_machine.py` owns transitions and precondition fallback logic.
- `Classes/action_sets.py` wires actions into named workflows.
- `Classes/Actions/*.py` contains single-purpose action wrappers.
- `Classes/image_finder.py` owns image matching and world-object detection.
- `Classes/input_controller.py` is the only module allowed to import or call
  `pyautogui`.

## Non-Negotiable Rules

- Do not add new process-wide mutable globals.
- Do not reintroduce `global_vars.py`.
- Pass shared data through `Context`.
- Do not call `execute()` directly from a state machine. Use `perform(context)`.
- Do not import `pyautogui` outside `Classes/input_controller.py`.
- Do not use `time.sleep()` inside actions. Use `DelayPolicy.wait(...)` or the
  base `Action(delay=..., post_delay=...)` mechanism.
- Do not bypass `InputController.validate_bounds(...)` for mouse movement or
  click execution.
- Do not launch live automation in tests unless explicitly requested.
- Do not move active `Media/...` assets without updating workflow references
  and running `python verify_integrity.py`.

## State Checks

- Use `GameStateMonitor` as the required abstraction for future state checks.
- Do not scatter ad hoc image checks across actions when the check represents a
  reusable game state such as Map View, City View, modal-open, or troop screen.
- If `GameStateMonitor` is not present in the branch, add or extend that
  abstraction before adding new reusable state checks.
- State checks should call `ImageFinder` with ROI where possible and should
  return explicit booleans or typed state names.
- Workflow recovery should be expressed as `StateMachine` preconditions with
  `fallback_state`, not hidden inside an action loop.

## Adding A New Action

1. Create a file under `Classes/Actions/` named after the action.
2. Inherit from `Action`.
3. Call `super().__init__(delay=delay, post_delay=post_delay)` in `__init__()`.
4. Store action-specific configuration on `self`.
5. Implement `execute(self, context=None)`.
6. Return `True` for the success transition and `False` for the failure transition.
7. Read shared state from the provided `context` argument.
8. Write shared outputs through `context`, for example `context.extracted[...]`.
9. If the action calls another action, call `child.perform(context)`, not
   `child.execute(context)`.
10. Use `InputController` for input and `ImageFinder` or `GameStateMonitor` for
    screen checks.
11. Import and wire the action in `Classes/action_sets.py`.
12. Add explicit success and failure transitions unless retrying the same state
    is intentional.
13. Add or update a flow comment explaining the recovery path.

Minimal action template:

```python
from Actions.action import Action


class ExampleAction(Action):
    def __init__(self, value, delay=0, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.value = value

    def execute(self, context=None):
        if context:
            context.extracted["example"] = self.value
        return True
```

## Updating A Workflow

- Read the workflow comment first.
- Keep transition names descriptive.
- Use this transition form:

```python
machine.add_state("state_name", SomeAction(), "success_state", "failure_state")
```

- Use `precondition=...` and `fallback_state=...` for required screen states.
- If the fourth argument is omitted, failure retries the same state.
- Confirm every literal transition target exists before handing work back.
- Run `python verify_integrity.py` after modifying workflows or media assets.

## Verification

Run these before handing work back:

```powershell
python verify_integrity.py
python -m compileall Classes verify_integrity.py
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
```

If dependencies are visible only outside the sandbox, run import checks in the
same Python environment used to install `requirements.txt`.
