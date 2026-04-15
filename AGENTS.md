# OSROKBOT AI-Agent Guide

This repository is intentionally small and state-machine driven. Preserve that
shape. Prefer explicit behavior over clever abstractions.

## Architecture

- `Classes/UI.py` owns the PyQt overlay and creates the per-run `Context`.
- `Classes/OS_ROKBOT.py` owns worker threads plus pause/stop state.
- `Classes/context.py` is the only shared runtime state object.
- `Classes/state_machine.py` decides success/failure transitions.
- `Classes/action_sets.py` wires actions into named workflows.
- `Classes/Actions/*.py` contains single-purpose actions.
- `Media/` contains template images used by OpenCV matching.

## Non-Negotiable Rules

- Do not add new process-wide mutable globals.
- Do not reintroduce `global_vars.py`.
- Pass shared data through `Context`.
- Do not call `execute()` directly from a state machine. Use `perform(context)`.
- Do not launch live automation in tests unless explicitly requested.
- Do not change `Media/...` asset paths without updating the corresponding files.

## Adding A New Action

1. Create a file under `Classes/Actions/` named after the action.
2. Inherit from `Action`.
3. Call `super().__init__(delay=delay, post_delay=post_delay)` in `__init__()`.
4. Store action-specific configuration on `self`.
5. Implement `execute(self, context=None)`.
6. Return `True` for the success transition and `False` for the failure transition.
7. Read shared state from the provided `context` argument.
8. Write shared outputs through `context`, for example `context.extracted[...]`.
9. If the action calls another action, call `child.perform(context)`, not `child.execute()`.
10. Import and wire the action in `Classes/action_sets.py`.
11. Add an explicit success and failure transition unless retrying the same state is intentional.
12. Add or update a flow comment in `Classes/action_sets.py` explaining the recovery path.

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

- If the fourth argument is omitted, failure retries the same state.
- Before handing work back, confirm every literal transition target exists.

## Verification

Run these before handing work back:

```powershell
python -m compileall Classes
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
```

If dependencies are visible only outside the sandbox, run import checks in the
same Python environment used to install `requirements.txt`.
