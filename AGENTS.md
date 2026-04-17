# OSROKBOT Maintainer Contract

This file is the short-form contract for AI agents and developers modifying
OSROKBOT. The user-facing guide is `README.md`; keep this file concise.

## Architecture Position

OSROKBOT is agentic-first. The supported runtime path is:

```text
UI mission
  -> Context
  -> StateMachine(plan_next)
  -> DynamicPlannerAction
  -> TaskGraph focused goal
  -> screenshot + YOLO + OCR + resource/stuck context
  -> VisionMemory or DynamicPlanner
  -> validation + autonomy gate
  -> InputController
```

Legacy gameplay templates are not a supported runtime path. `Media/Legacy/`
and loose root-level `Media/*.png` files are deprecated and are purged by
`cleanup_media.py`.

## Ownership Boundaries

| Module | Owns |
| --- | --- |
| `Classes/UI.py` | Overlay, settings, mission input, autonomy selector, approval controls, mission history, session logger setup, and per-run `Context`. |
| `Classes/context.py` | Shared runtime state, planner approval payloads, state history, resource cache, UI anchors, and UI signal access. |
| `Classes/action_sets.py` | Supported workflow factory. New runtime work should use `dynamic_planner()`. |
| `Classes/state_machine.py` | Deterministic state execution, preconditions, transition history, diagnostics, and tiered global recovery. |
| `Classes/OS_ROKBOT.py` | Executor-backed run loop, pause/stop events, foreground guard, CAPTCHA pause, heartbeat scheduling, and emergency-stop startup. |
| `Classes/Actions/dynamic_planner_action.py` | Observation, detector/OCR calls, task focus, resource context, approval flow, correction recording, memory updates, and guarded execution. |
| `Classes/dynamic_planner.py` | Side-effect-free OpenAI planning, strict JSON schema validation, target resolution, retries, and memory-first decision selection. |
| `Classes/task_graph.py` | Mission decomposition, sub-goal cache, focused-goal text, and label/OCR completion checks. |
| `Classes/vision_memory.py` | CLIP embeddings, FAISS/NumPy similarity search, success/failure memory, corrections, and trusted-label checks. |
| `Classes/screen_change_detector.py` | Stuck-screen and repeated-action warnings for the planner prompt. |
| `Classes/state_monitor.py` | Coarse game-state checks, blocker clearing, march-slot OCR, action-point OCR, and explicit client restart support. |
| `Classes/input_controller.py` | All mouse, keyboard, and scroll execution. No other module should call lower-level input APIs. |
| `Classes/model_manager.py` | Local YOLO weight discovery and optional HTTPS download. |
| `Classes/emergency_stop.py` | F12 emergency termination. |
| `watchdog.py` | Heartbeat monitoring and conservative tracked-process restart behavior. |

## Non-Negotiable Rules

- Do not reintroduce `global_vars.py` or process-wide mutable runtime state.
- Pass shared runtime data through `Context`.
- Do not import or call lower-level input libraries outside `Classes/input_controller.py`.
- Do not bypass `InputController.validate_bounds(...)` for pointer actions.
- Do not solve, bypass, or automate CAPTCHAs. Detection must pause for human review.
- Do not add new root-level gameplay templates under `Media/`.
- Do not move protected media under `Media/UI/` or `Media/Readme/` without updating `MEDIA_MAP.md`.
- Keep `dynamic_planner.py` side-effect free. It may propose JSON decisions; it must not execute input.
- Keep agentic input execution behind `DynamicPlannerAction` and `InputController`.
- When code changes behavior, architecture, configuration, runtime data paths, safety rules, or operator workflow, update the affected documentation in the same change. At minimum, review `README.md`, `AGENTS.md`, `SKILLS.md`, and `MEDIA_MAP.md`.
- Do not launch live automation in tests unless explicitly requested.
- Use `DelayPolicy.wait(...)` or action-level delays for waits inside action flows.

## Planner Decision Contract

`dynamic_planner.py` accepts screenshot context, local YOLO/OCR target IDs, OCR
text, recent state history, optional resource context, stuck-screen warnings,
and a natural-language focused mission. It returns a validated
`PlannerDecision`.

Strict model-facing JSON fields:

```json
{
  "thought_process": "short debug note",
  "action_type": "click",
  "target_id": "det_3",
  "label": "target label",
  "confidence": 0.9,
  "delay_seconds": 1.0,
  "reason": "short user-facing reason",
  "end_target_id": "",
  "key_name": "",
  "text_content": "",
  "drag_direction": ""
}
```

Allowed action types are `click`, `drag`, `long_press`, `key`, `type`, `wait`,
and `stop`.

- `click` and `long_press` require a current local `target_id`.
- `drag` requires a current local `target_id` and either `end_target_id` or
  `drag_direction`.
- `key` requires `key_name`.
- `type` requires `text_content`.
- `wait` and `stop` do not require coordinates.

The model must not return raw coordinates. `dynamic_planner.py` resolves
target IDs to normalized coordinates before validation. Missing or unknown
target IDs, low confidence, non-finite resolved coordinates, out-of-window
targets, and unsupported actions must be rejected before input execution.

Current human approval UI covers pointer-target actions: `click`, `drag`, and
`long_press`. `key` and `type` are still validated and routed through
`InputController`, but they do not use the target approval prompt.

## Human-In-The-Loop Safety

The UI autonomy levels are part of the safety model:

- `L1 approve`: pointer-target actions wait for `OK`.
- `L2 trusted`: locally trusted labels can auto-execute pointer actions after enough clean successes.
- `L3 auto`: validated pointer actions can execute without approval.

Default to L1 when testing changes, new prompts, new weights, new action types,
new missions, or new memory.

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

Before handoff, also confirm that any code changes are reflected in the
relevant Markdown docs and examples. Do not leave `README.md`, `AGENTS.md`,
`SKILLS.md`, or `MEDIA_MAP.md` describing behavior that no longer matches the
code.

Use these for static cleanup work:

```powershell
python -m ruff check Classes verify_integrity.py cleanup_media.py watchdog.py --select I,UP,RET,SIM,B,F,PTH
python -m vulture Classes verify_integrity.py watchdog.py cleanup_media.py --min-confidence 80
```

If `verify_integrity.py` fails only because the Rise of Kingdoms window is not
open, report that explicitly instead of weakening the check.
