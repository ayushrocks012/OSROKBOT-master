# OSROKBOT Agentic Capability Index

This index names the capabilities that exist in the current agentic
architecture. It intentionally excludes deprecated root-level gameplay template
workflows.

For setup, safety, and user operation, read `README.md`. For maintainer rules,
read `AGENTS.md`.

## Dynamic Vision Planning

- Owner: `Classes/dynamic_planner.py`
- Runtime bridge: `Classes/Actions/dynamic_planner_action.py`
- Inputs: screenshot, natural-language mission, YOLO labels, OCR text, and
  recent state history.
- Output: one validated JSON planner decision: `click`, `wait`, or `stop`.
- Safety: planner output is schema validated, confidence checked, bounds
  checked, and gated by the selected autonomy level before input execution.

## YOLO UI Perception

- Owner: `Classes/object_detector.py`
- Configuration: `ROK_YOLO_WEIGHTS`
- Behavior: loads local YOLO weights when configured; otherwise uses a no-op
  detector that returns no labels.
- Purpose: provide structured visible UI labels to the planner and memory
  filters.

## OCR Perception

- Owner: `Classes/ocr_service.py`
- Primary engine: EasyOCR
- Fallback engine: Tesseract through `TESSERACT_PATH`
- Purpose: give the planner text visible on the current game screen.

## Local Visual Memory

- Owner: `Classes/vision_memory.py`
- Storage: `data/vision_memory.json`
- Search: FAISS when available, NumPy fallback otherwise.
- Embeddings: CLIP via `sentence-transformers`.
- Purpose: reuse successful local decisions and support L2 trusted autonomy.

## Fix-Based Human Correction

- Owner: `Classes/UI.py` and `Classes/Actions/dynamic_planner_action.py`
- Memory writer: `Classes/vision_memory.py`
- Dataset export: `Classes/detection_dataset.py`
- Workflow: user presses `Fix`, moves the cursor to the correct target, and the
  bot records a corrected normalized point.
- Purpose: teach local memory when the planner chooses the wrong UI element.

## Autonomy Gate

- Owner: `Classes/Actions/dynamic_planner_action.py`
- Levels:
  - L1: every click requires approval.
  - L2: trusted labels can auto-click after enough clean local successes.
  - L3: validated clicks can execute without approval.
- Purpose: let users scale from supervised operation to trusted local autonomy.

## Centralized Hardware Input

- Owner: `Classes/input_controller.py`
- Backend: Oblita Interception driver through `interception-python`
- Responsibilities: pause/stop checks, foreground checks, bounds validation,
  click execution, mouse movement, key presses, scroll pacing, and delay policy.
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

## Watchdog Heartbeat

- Owner: `watchdog.py`
- Heartbeat file: `data/heartbeat.json`
- Writer: `Classes/OS_ROKBOT.py`
- Behavior: watches tracked bot/game PIDs from heartbeat data, restarts only
  those tracked processes, and relaunches the game only when `ROK_CLIENT_PATH`
  is configured.

## Media Cleanup

- Owner: `cleanup_media.py`
- Protected paths: `Media/UI/`, `Media/Readme/`
- Deprecated paths: `Media/Legacy/`, loose root-level `Media/*.png`
- Purpose: keep repository assets aligned with the VLM/YOLO architecture.

## Integrity Verification

- Owner: `verify_integrity.py`
- Checks: media references, state-machine transitions, UI coordinate ranges,
  required environment values, runtime imports, Interception availability,
  watchdog configuration, and optional YOLO weight accessibility.
