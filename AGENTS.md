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

Legacy gameplay templates are not a supported runtime path. Deprecated action
templates live under `Classes/Actions/legacy/` for reference only. `Media/Legacy/`
and loose root-level `Media/*.png` files are deprecated and are purged by
`cleanup_media.py`.

## Ownership Boundaries

| Module | Owns |
| --- | --- |
| `Classes/UI.py` | Overlay, settings, mission input, autonomy selector, approval controls, detector-box approval overlay, mission history, background YOLO warmup, session logger setup, and per-run `Context`. |
| `Classes/context.py` | Thread-guarded shared runtime state, per-step observation cache, planner approval payloads, state history, resource cache, UI anchors, and UI signal access. |
| `Classes/action_sets.py` | Supported workflow factory. New runtime work should use `dynamic_planner()`. |
| `Classes/state_machine.py` | Deterministic state execution, preconditions, transition history, diagnostics, and tiered global recovery. |
| `Classes/runtime_contracts.py` | Shared typed Protocols and aliases for active state/action, detector/OCR, and window-capture boundaries. |
| `Classes/runtime_payloads.py` | Shared TypedDict payloads for heartbeat, planner approval, recovery handoff, state history, and resource context. |
| `Classes/artifact_retention.py` | Shared retention policies for diagnostics, session logs, and recovery dataset exports. |
| `Classes/OS_ROKBOT.py` | Executor-backed run loop, injectable runtime services, pause/stop events, foreground guard, shared observation reuse, CAPTCHA pause, heartbeat scheduling, state-machine cleanup, and emergency-stop startup. |
| `Classes/Actions/dynamic_planner_action.py` | Planner-step orchestration that composes observation, approval, feedback, and execution services. |
| `Classes/Actions/dynamic_planner_services.py` | Planner observation, approval, execution, and feedback services used by `DynamicPlannerAction`. |
| `Classes/Actions/legacy/` | Deprecated action templates retained outside the supported runtime. Do not import them from production paths. |
| `Classes/dynamic_planner.py` | Side-effect-free OpenAI planning, dedicated async transport, strict JSON schema validation, target resolution, retries, and memory-first decision selection. |
| `Classes/task_graph.py` | Mission decomposition, sub-goal cache, focused-goal text, and label/OCR completion checks. |
| `Classes/vision_memory.py` | CLIP embeddings, FAISS/NumPy similarity search, bounded atomic persistence, duplicate-success merging, success/failure memory, corrections, and trusted-label checks. |
| `Classes/recovery_memory.py` | Bounded atomic persistence for guarded recovery outcomes keyed by state/action/screen signatures. |
| `Classes/screen_change_detector.py` | Stuck-screen and repeated-action warnings for the planner prompt. |
| `Classes/state_monitor.py` | Coarse game-state checks, blocker clearing, march-slot OCR, action-point OCR, and explicit client restart support. |
| `Classes/input_controller.py` | All mouse, keyboard, and scroll execution, including bounded humanization for pointer actions. No other module should call lower-level input APIs. |
| `Classes/window_handler.py` | Foreground enforcement, client-rect discovery, and named window capture backends. |
| `Classes/model_manager.py` | Local YOLO weight discovery and optional HTTPS download with timeout, streaming, and size-cap enforcement. |
| `Classes/security_utils.py` | Secret redaction, atomic text writes, and dotenv updates for sensitive local configuration. |
| `Classes/emergency_stop.py` | F12 emergency termination. |
| `watchdog.py` | Heartbeat monitoring and conservative tracked-process restart behavior. |
| `verify_docs.py` | Lightweight check that required runbooks, ADRs, diagrams, and maintained documentation links exist. |

## Non-Negotiable Rules

- Do not reintroduce `global_vars.py` or process-wide mutable runtime state.
- Pass shared runtime data through `Context`.
- Do not import or call lower-level input libraries outside `Classes/input_controller.py`.
- Do not bypass `InputController.validate_bounds(...)` for pointer actions.
- Do not solve, bypass, or automate CAPTCHAs. Detection must pause for human review.
- Do not add new root-level gameplay templates under `Media/`.
- Do not import from `Actions.legacy` in production runtime code.
- Do not move protected media under `Media/UI/` or `Media/Readme/` without updating `MEDIA_MAP.md`.
- Keep `dynamic_planner.py` side-effect free. It may propose JSON decisions; it must not execute input.
- Keep agentic input execution behind `DynamicPlannerAction` and `InputController`.
- Keep runner-owned dependencies injectable and close planner/action resources during run teardown.
- Keep startup/runtime composition in `UI.py` or another explicit composition root; `OS_ROKBOT` should stay orchestration-focused.
- Store sensitive configuration in `.env` or process environment, never in `config.json`.
- Treat `.env` as workstation-grade local secret storage, not as an enterprise vault or central rotation/audit system.
- Do not log API keys, passwords, tokens, or full secret assignment values.
- Keep long-running downloads and warmups off the PyQt UI thread.
- Keep generated diagnostics, session logs, and recovery exports bounded with the shared artifact retention policies.
- Keep runtime timing telemetry for capture, detector, OCR, planner, and input phases flowing into the current session log when modifying those paths.
- When code changes behavior, architecture, configuration, runtime data paths, safety rules, or operator workflow, update the affected documentation in the same change. At minimum, review `README.md`, `AGENTS.md`, `SKILLS.md`, and `MEDIA_MAP.md`.
- Update operator runbooks under `docs/runbooks/` when watchdog, CAPTCHA, emergency-stop, secret provisioning, telemetry, or failure-triage behavior changes.
- Add or amend ADRs under `docs/adr/` when changing the planner-first runtime path, HITL safety model, input boundary, or other architecture-level contracts.
- Do not launch live automation in tests unless explicitly requested.
- Use `integration` for safe OS/service seam tests and `supervised` for opt-in operator/hardware acceptance tests.
- Keep supervised tests skipped unless `OSROKBOT_RUN_SUPERVISED_TESTS=1` is intentionally set.
- Use `DelayPolicy.wait(...)` or action-level delays for waits inside action flows.

## Documentation Standard

- Use Google-style docstrings for active runtime modules, classes, and
  non-trivial public methods.
- Module docstrings should describe ownership, side effects, and threading
  boundaries when relevant.
- Public class docstrings should describe collaborators and invariants.
- When runtime flow, approval gating, or recovery behavior changes, update the
  Mermaid diagrams in `README.md` in the same change.
- Use `docs/documentation-review-checklist.md` before handoff for changes that
  affect user behavior, maintainer rules, runbooks, ADRs, configuration, or
  runtime data paths.
- Treat `Classes/dynamic_planner.py` and `Classes/state_machine.py` as the
  canonical examples for planner and workflow documentation.

## Planner Decision Contract

`dynamic_planner.py` accepts screenshot context, local YOLO/OCR target IDs, OCR
text, recent state history, optional resource context, stuck-screen warnings,
and a natural-language focused mission. It returns a validated
`PlannerDecision`. Network I/O is handled behind a dedicated async transport,
but the planner surface used by the runtime remains synchronous.

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

In `L1 approve`, the overlay highlights the selected target and the current
YOLO detector boxes for faster human verification.

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
python verify_docs.py
python -m compileall Classes verify_integrity.py verify_docs.py cleanup_media.py watchdog.py
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
python -m mypy
python -m pytest --basetemp .pytest_tmp -o cache_dir=.pytest_cache
```

The Phase 1 mypy gate is intentionally scoped to the typed boundary/runtime
modules listed in `pyproject.toml`: `runtime_contracts`, `runtime_payloads`,
`context`, `OS_ROKBOT`, `action_sets`, and `ai_recovery_executor`.

`pytest.ini` is the source of truth for the coverage gate. It enforces
`>=80%` coverage across the deterministic planner/runtime modules:
`ai_fallback`, `ai_recovery_executor`, `config_manager`, `context`,
`dynamic_planner`, `model_manager`, `OS_ROKBOT`, `security_utils`, and
`state_machine`.

`pytest.ini` also defines strict test markers. `integration` tests are safe
OS/service seam tests that run by default. `supervised` tests are skipped
unless an operator explicitly sets `OSROKBOT_RUN_SUPERVISED_TESTS=1`.

Before handoff, also confirm that any code changes are reflected in the
relevant Markdown docs and examples. Do not leave `README.md`, `AGENTS.md`,
`SKILLS.md`, or `MEDIA_MAP.md` describing behavior that no longer matches the
code.

Use these for static cleanup work:

```powershell
python -m ruff check Classes verify_integrity.py verify_docs.py cleanup_media.py watchdog.py --select I,UP,RET,SIM,B,F,PTH
python -m vulture Classes verify_integrity.py watchdog.py cleanup_media.py --min-confidence 80
```

If `verify_integrity.py` fails only because the Rise of Kingdoms window is not
open, report that explicitly instead of weakening the check.
