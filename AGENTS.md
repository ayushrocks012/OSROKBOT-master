# OSROKBOT Maintenance Guide

This project automates Rise of Kingdoms by running one or more state machines.
Keep changes small, explicit, and behavior-preserving unless a bug is called out.

## Architecture

- `Classes/UI.py` owns the PyQt overlay and starts/stops automation runs.
- `Classes/OS_ROKBOT.py` owns run threads, pause/stop flags, and Qt signals.
- `Classes/context.py` owns runtime state shared by all machines in one run.
- `Classes/state_machine.py` owns state transitions.
- `Classes/action_sets.py` defines named workflows by wiring actions into state machines.
- `Classes/Actions/*.py` contains single-purpose side-effect actions.
- `Classes/image_finder.py`, `Classes/window_handler.py`, and `Classes/input_controller.py` isolate screen, window, and input operations.

## Refactor Rules

- Do not add new process-wide mutable globals. Pass shared run data through `Context`.
- Keep `Action.perform()` as the only place that emits UI state and applies base `delay` and `post_delay`.
- Concrete action classes should implement only `execute(context=None)`.
- If an action invokes another action, call `child_action.perform(context)` so context and UI state stay intact.
- Every state-machine transition target must be an existing state name or a callable returning one.
- Prefer adding explicit failure transitions for actions that can return `False`.
- Keep `Media/...` paths stable unless the image assets are changed at the same time.

## Verification

Run these before handing work back:

```powershell
python -m compileall Classes
```

If dependencies are installed in the active Python environment, also run an import smoke test:

```powershell
python - <<'PY'
import sys
sys.path.insert(0, "Classes")
import action_sets
import context
import state_machine
import OS_ROKBOT
print("imports ok")
PY
```

Do not launch the UI or interact with the game window during automated checks unless the user explicitly asks for a live run.
