# OSROKBOT Agent Instructions

These instructions are for AI agents and developers changing OSROKBOT. Preserve
the explicit state-machine design and keep changes small, inspectable, and
recoverable.

## Architecture Boundaries

- `Classes/UI.py` owns the PyQt overlay and creates the per-run `Context`.
- `Classes/context.py` is the only shared runtime state object.
- `Classes/OS_ROKBOT.py` owns worker threads, pause/stop events, and global
  pre-action blocker checks.
- `Classes/state_machine.py` owns transitions, preconditions, and fallback
  routing.
- `Classes/action_sets.py` wires single-purpose actions into named workflows.
- `Classes/Actions/*.py` contains action wrappers. Actions do one job and
  return `True` or `False`.
- `Classes/image_finder.py` owns template matching, ROI scaling, alpha masks,
  non-maximum suppression, and SIFT world-object matching.
- `Classes/input_controller.py` is the only module allowed to import or call
  `pyautogui`.

## Non-Negotiable Rules

- Do not add process-wide mutable globals.
- Do not reintroduce `global_vars.py`.
- Pass shared runtime data through `Context`.
- Do not import `pyautogui` or `pydirectinput` outside
  `Classes/input_controller.py`.
- Do not bypass `InputController.validate_bounds(...)` for mouse movement or
  click execution.
- Do not use `time.sleep()` inside actions. Use `DelayPolicy.wait(...)` or the
  base `Action(delay=..., post_delay=...)` mechanism.
- Do not launch live automation in tests unless explicitly requested.
- Do not move active `Media/...` assets without updating `MEDIA_MAP.md` and
  running `python verify_integrity.py`.

## Centralized Input

All mouse, keyboard, and scroll operations go through `InputController`.
`InputController` enforces pause/stop interlocks, window bounds validation,
click settle timing, key hold timing, scroll pacing, and cursor movement
behavior. Actions may use wrappers such as `ManualClickAction`,
`ManualMoveAction`, `PressKeyAction`, or create an `InputController(context=...)`
directly, but they must not call lower-level input libraries.

## Action Protocol

State machines and parent actions must call `child.perform(context)`, not
`child.execute(context)`. `perform(...)` is the safety boundary: it emits UI
status, applies pre-action and post-action delays, and checks pause/stop state
before the action body runs.

Only implement behavior inside `execute(self, context=None)` after the action
has been entered through `perform(...)`. `execute(...)` must return `True` for
the success transition and `False` for the failure transition.

## State-First Logic

Verify the expected game state before actions that depend on a specific screen.
Reusable screen checks belong behind a named state-check abstraction, preferably
`GameStateMonitor` when present. Do not scatter one-off image checks across
actions when the check represents a reusable state such as Map View, City View,
modal-open, inventory, troop selection, or march screen.

Use `StateMachine.add_state(..., precondition=..., fallback_state=...)` for
workflow recovery. The fallback should move the bot to an explicit recovery
state instead of hiding recovery loops inside action code.

## Global Blockers

`OS_ROKBOT` clears known modal blockers such as `Media/confirm.png` and
`Media/escx.png` before each workflow action. Do not duplicate this logic in
individual actions. Add future blocker templates to the runner-level blocker
list and keep them documented in `MEDIA_MAP.md`.

## Adding A New Action

1. Create a file under `Classes/Actions/` named after the action.
2. Inherit from `Action`.
3. Call `super().__init__(delay=delay, post_delay=post_delay)` in `__init__()`.
4. Store action-specific configuration on `self`.
5. Implement `execute(self, context=None)`.
6. Read shared state from `context`.
7. Write shared outputs through `context`, for example
   `context.extracted["key"] = value`.
8. Use `InputController` for input and `ImageFinder` or `GameStateMonitor` for
   screen checks.
9. If the action calls another action, call `child.perform(context)`.
10. Wire the action in `Classes/action_sets.py`.
11. Add explicit success and failure transitions unless retrying the same state
    is intentional.
12. Add or update a workflow comment explaining the recovery path.

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
- Update `MEDIA_MAP.md` when adding, removing, or archiving templates.
- Run `python verify_integrity.py` after workflow or media changes.

## Verification

Run these before handing work back:

```powershell
python verify_integrity.py
python -m compileall Classes verify_integrity.py
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
```

If dependencies are only visible outside the sandbox, run the import check in
the same Python environment used to install `requirements.txt`.
