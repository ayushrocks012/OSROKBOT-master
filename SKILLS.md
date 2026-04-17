# OSROKBOT Capability Index

This index names the capabilities in the current agentic architecture. It
intentionally excludes deprecated root-level gameplay-template workflows.

For setup, safety, and user operation, read `README.md`. For maintainer rules,
read `AGENTS.md`.

This file is part of the maintained documentation set. Whenever code changes
add, remove, rename, or materially change a capability, update this file in the
same change and review the matching sections in `README.md`, `AGENTS.md`, and
`MEDIA_MAP.md`.

## Dynamic Vision Planning

- Owner: `Classes/dynamic_planner.py`
- Runtime bridge: `Classes/Actions/dynamic_planner_action.py`
- OpenAI API: Responses API with strict JSON schema output.
- Inputs: screenshot, natural-language focused goal, local detector/OCR target
  IDs, OCR text, recent state history, optional resource context, and
  stuck-screen warnings.
- Output: one validated `PlannerDecision`.
- Supported actions: `click`, `drag`, `long_press`, `key`, `type`, `wait`,
  and `stop`.
- Safety: planner output is schema validated, target-resolved, confidence
  checked, delay bounded, and routed through the guarded action layer.

## Task Graph Decomposition

- Owner: `Classes/task_graph.py`
- Purpose: decompose complex missions into 2-8 concrete sub-goals with
  expected labels/OCR keywords.
- Runtime use: `DynamicPlannerAction` initializes the graph once per mission
  and sends the current focused sub-goal to the planner.
- Fallback: if decomposition is unavailable, the full mission becomes a single
  sub-goal.

## YOLO UI Perception

- Owner: `Classes/object_detector.py`
- Model management: `Classes/model_manager.py`
- Configuration: `ROK_YOLO_WEIGHTS` or `ROK_YOLO_WEIGHTS_URL`
- Behavior: loads local YOLO weights when available; otherwise uses a no-op
  detector that returns no labels.
- Purpose: provide structured visible UI labels and target boxes to planner,
  memory, state monitor, and CAPTCHA detection.

## OCR Perception

- Owner: `Classes/ocr_service.py`
- Primary engine: EasyOCR
- Fallback engine: Tesseract through `TESSERACT_PATH`
- Outputs: plain screen text and normalized OCR regions that can become local
  planner targets.

## Resource And State Awareness

- Owner: `Classes/state_monitor.py`
- Capabilities: coarse CITY/MAP/BLOCKED/UNKNOWN classification, blocker
  clearing, idle march-slot OCR, action-point OCR, and explicit client restart.
- Runtime use: `DynamicPlannerAction` adds resource context to the planner
  prompt when readable.

## Stuck-Screen Detection

- Owner: `Classes/screen_change_detector.py`
- Signals: perceptual screenshot hash changes and repeated action patterns.
- Runtime use: warning text is included in planner prompts so repeated
  no-effect actions can be avoided.

## Local Visual Memory

- Owner: `Classes/vision_memory.py`
- Storage: `data/vision_memory.json`
- Search: FAISS when available, NumPy fallback otherwise.
- Embeddings: CLIP via `sentence-transformers`.
- Purpose: reuse successful local decisions before OpenAI calls and support L2
  trusted-label autonomy.

## Fix-Based Human Correction

- Owner: `Classes/UI.py` and `Classes/Actions/dynamic_planner_action.py`
- Memory writer: `Classes/vision_memory.py`
- Dataset export: `Classes/detection_dataset.py`
- Workflow: user presses `Fix`, moves the cursor to the correct target, and
  the bot records a corrected normalized point.
- Purpose: teach local memory and generate correction data when the planner
  chooses the wrong pointer target.

## Autonomy Gate

- Owner: `Classes/Actions/dynamic_planner_action.py`
- L1: pointer-target actions require approval.
- L2: trusted labels can auto-execute pointer actions after enough clean local
  successes.
- L3: validated pointer actions can execute without approval.
- Note: current target approval UI covers `click`, `drag`, and `long_press`.
  `key` and `type` are validation-gated and still route through
  `InputController`, but do not use the target approval prompt.

## Centralized Hardware Input

- Owner: `Classes/input_controller.py`
- Backend: Oblita Interception driver through `interception-python`
- Responsibilities: Interception readiness, pause/stop checks, foreground
  checks, pointer bounds validation, mouse movement, click execution, key
  presses, scroll pacing, and delay policy.
- Rule: no other module should call lower-level mouse or keyboard libraries.

## CAPTCHA Pause

- Owner: `Classes/OS_ROKBOT.py`
- Detector source: YOLO labels from `object_detector.py`
- Policy: CAPTCHA detection pauses automation and waits for human handling.
- Rule: do not add CAPTCHA solving or bypass behavior.

## Emergency Stop

- Owner: `Classes/emergency_stop.py`
- Trigger: F12
- Behavior: immediate process termination.
- Purpose: stop hardware-level input even if the overlay is unavailable.

## Session Logging

- Owner: `Classes/session_logger.py`
- Runtime setup: `Classes/UI.py`
- Storage: `data/session_logs/`
- Purpose: local run summaries, planner decisions, approvals, corrections,
  CAPTCHA events, and errors.

## Watchdog Heartbeat

- Owner: `watchdog.py`
- Heartbeat file: `data/heartbeat.json` by default
- Writer: `Classes/OS_ROKBOT.py`
- Behavior: watches tracked bot/game PIDs from heartbeat data, restarts only
  those tracked processes, and relaunches the game only when `ROK_CLIENT_PATH`
  is configured and restart is enabled.

## Media Cleanup

- Owner: `cleanup_media.py`
- Protected paths: `Media/UI/`, `Media/Readme/`
- Deprecated paths: `Media/Legacy/`, loose root-level `Media/*.png`
- Purpose: keep repository assets aligned with the screenshot/YOLO/OCR/VLM
  runtime.

## Integrity Verification

- Owner: `verify_integrity.py`
- Checks: media references, state-machine transitions, UI coordinate ranges,
  required environment values, runtime imports, Interception availability,
  watchdog configuration, optional YOLO weight accessibility, and game-window
  health.

## Documentation Sync

- Trigger: any code change that affects architecture, capability surface,
  operator workflow, safety behavior, configuration, runtime storage, or media
  policy.
- Required action: update this file in the same change and review the matching
  sections of `README.md`, `AGENTS.md`, and `MEDIA_MAP.md`.
- Rule: do not leave capability docs describing code paths that are no longer
  supported or omit new guarded runtime behavior that users or maintainers need
  to understand.
