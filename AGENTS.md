# OSROKBOT Maintainer Contract

This file is the short-form contract for AI agents and developers modifying
OSROKBOT. The main user-facing system guide is `README.md`; do not duplicate it
here.

## Architecture Position

OSROKBOT is agentic-first. The primary runtime path is:

```text
UI mission -> Context -> DynamicPlannerAction -> DynamicPlanner -> autonomy gate -> InputController
```

Legacy gameplay templates are not a supported runtime path. `Media/Legacy/` and
loose root-level `Media/*.png` files are deprecated and are purged by
`cleanup_media.py`.

## Ownership Boundaries

| Module | Owns |
| --- | --- |
| `Classes/UI.py` | Overlay, mission input, autonomy selector, approval controls, and per-run `Context`. |
| `Classes/context.py` | Shared runtime state, planner approval payloads, state history, and UI signal access. |
| `Classes/OS_ROKBOT.py` | Executor-backed run loop, pause/stop events, foreground guard, CAPTCHA pause, and heartbeat scheduling. |
| `Classes/Actions/dynamic_planner_action.py` | Observation, detector/OCR calls, approval flow, correction recording, memory updates, and guarded click execution. |
| `Classes/dynamic_planner.py` | Side-effect-free OpenAI planning, JSON schema validation, and memory-first decision selection. |
| `Classes/vision_memory.py` | CLIP embeddings, FAISS/NumPy similarity search, success/failure memory, and trusted-label checks. |
| `Classes/input_controller.py` | All mouse, keyboard, and scroll execution. No other module should call lower-level input APIs. |
| `Classes/emergency_stop.py` | F12 emergency termination. |
| `watchdog.py` | Heartbeat monitoring and conservative restart behavior. |

## Non-Negotiable Rules

- Do not reintroduce `global_vars.py` or process-wide mutable runtime state.
- Pass shared runtime data through `Context`.
- Do not import or call lower-level input libraries outside `Classes/input_controller.py`.
- Do not bypass `InputController.validate_bounds(...)`.
- Do not solve, bypass, or automate CAPTCHAs. Detection must pause for human review.
- Do not add new root-level gameplay templates under `Media/`.
- Do not move protected media under `Media/UI/` or `Media/Readme/` without updating `MEDIA_MAP.md`.
- Keep `dynamic_planner.py` side-effect free. It may propose JSON decisions; it must not click.
- Keep click-capable agentic behavior behind `DynamicPlannerAction` and the autonomy gate.
- Do not launch live automation in tests unless explicitly requested.
- Use `DelayPolicy.wait(...)` or action-level delays for waits inside action flows.

## Planner Decision Contract

`dynamic_planner.py` accepts screenshot context, local YOLO/OCR target IDs, OCR
text, recent state history, and a natural-language mission. It returns a
validated `PlannerDecision` with strict JSON fields:

```json
{
  "thought_process": "short debug note",
  "action_type": "click",
  "target_id": "det_3",
  "label": "target label",
  "confidence": 0.9,
  "delay_seconds": 1.0,
  "reason": "short user-facing reason"
}
```

Allowed action types are `click`, `wait`, and `stop`. Click decisions must
reference a current local detector/OCR `target_id`; `dynamic_planner.py`
resolves that ID to normalized coordinates before validation. Missing or
unknown target IDs, low confidence, non-finite resolved coordinates, and
out-of-window targets must be rejected before input execution.

## Human-In-The-Loop Safety

The UI autonomy levels are part of the safety model:

- `L1 approve`: every click waits for `OK`.
- `L2 trusted`: locally trusted labels can auto-click after enough clean successes.
- `L3 auto`: validated planner clicks can execute without approval.

Default to L1 when testing changes, new prompts, new weights, or new memory.

## Media Contract

The only protected media folders are:

- `Media/UI/`
- `Media/Readme/`

`cleanup_media.py` is the source of truth for deprecated media cleanup. It
deletes:

- `Media/Legacy/`
- loose root-level `Media/*.png`

It does not delete protected folders or nested files under other media
subdirectories.

## Verification

Run these before handing work back:

```powershell
python verify_integrity.py
python -m compileall Classes verify_integrity.py cleanup_media.py watchdog.py
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
python -m pytest --basetemp .pytest_tmp -o cache_dir=.pytest_cache
```

Use these for static cleanup work:

```powershell
python -m ruff check Classes verify_integrity.py cleanup_media.py watchdog.py --select I,UP,RET,SIM,B,F,PTH
python -m vulture Classes verify_integrity.py watchdog.py cleanup_media.py --min-confidence 80
```

If `verify_integrity.py` fails only because the Rise of Kingdoms window is not
open, report that explicitly instead of weakening the check.
