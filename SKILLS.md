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
- OpenAI API: Responses API with strict JSON schema output, sent through a
  dedicated async planner transport while the runtime-facing planner API stays
  synchronous.
- Inputs: screenshot, natural-language focused goal, local detector/OCR target
  IDs, OCR text, recent state history, optional resource context, and
  stuck-screen warnings.
- Output: one validated `PlannerDecision`.
- Supported actions: `click`, `drag`, `long_press`, `key`, `type`, `wait`,
  and `stop`.
- Safety: planner output is schema validated, target-resolved, confidence
  checked, delay bounded, and routed through the guarded action layer.

## Runtime Action Surface

- Owner: `Classes/Actions/__init__.py`
- Active actions: `Action` and `DynamicPlannerAction`.
- Removed actions: old template-driven actions have been retired from the
  repository.
- Rule: new runtime work must use `ActionSets.dynamic_planner()` and must not
  reintroduce legacy action modules.

## Runtime Composition Root

- Owner: `Classes/runtime_composition.py`
- Purpose: keep startup wiring explicit by owning shared detector, window,
  input, and memory collaborators plus per-run `Context` factory wiring.
- Runtime use: `UI.py` creates the composition root, `UIController.py`
  consumes it, and `OSROKBOT`, `GameStateMonitor`, and guarded recovery reuse
  its injected factories.

## Planner Decision Policy

- Owner: `Classes/planner_decision_policy.py`
- Purpose: derive one canonical verdict for planner execution readiness,
  Fix-required review, and rejection reasons.
- Runtime use: `dynamic_planner.py`, `UIController.py`, and
  `dynamic_planner_services.py` all use the same decision policy so low-
  confidence pointer actions do not drift across modules.

## Typed Runtime Contracts

- Owner: `Classes/runtime_contracts.py`
- Purpose: keep the state-machine action contract, detector/OCR providers, and
  window-capture boundary explicit without coupling active modules to concrete
  implementations.
- Runtime use: `StateMachine`, `ActionSets`, and `DynamicPlannerAction` use
  these contracts to make active Phase 1 paths statically inspectable.

## Typed Runtime Payloads

- Owner: `Classes/runtime_payloads.py`
- Purpose: keep heartbeat, planner approval, recovery handoff, resource
  context, and state-history payload shapes explicit without spreading
  untyped ad-hoc dictionaries through the active runtime.
- Runtime use: `Context`, `OS_ROKBOT`, `DynamicPlannerAction`, and
  `AIRecoveryExecutor` share these payload shapes for the active planner-first
  path.

## Planner Action Services

- Owners: `Classes/Actions/dynamic_planner_action.py` and
  `Classes/Actions/dynamic_planner_services.py`
- Split: observation capture, human approval, guarded execution, and planner
  feedback/memory updates are separate services composed by the action.
- Startup wiring: `UI.py` provides the production detector/window/memory
  dependencies through the `ActionSets` dynamic planner factory.
- Purpose: reduce orchestration coupling and keep `DynamicPlannerAction`
  focused on one planner-step flow.

## Phase 1 Type Gate

- Owner: `pyproject.toml`
- Command: `python -m mypy`
- Scope: `runtime_contracts`, `runtime_payloads`, `context`, `OS_ROKBOT`,
  `action_sets`, `ai_recovery_executor`, `planner_decision_policy`, and
  `runtime_composition`.
- Intent: keep the typed boundary/runtime handoff clean now, while heavier
  orchestration modules are refactored before they join the strict gate.

## Task Graph Decomposition

- Owner: `Classes/task_graph.py`
- Purpose: decompose complex missions into 2-8 concrete sub-goals with
  expected labels/OCR keywords.
- Runtime use: `DynamicPlannerAction` initializes the graph once per mission
  and sends the current focused sub-goal to the planner. When teaching mode is
  active, the decomposition prompt also receives the gameplay teaching brief.
- Fallback: if decomposition is unavailable, the full mission becomes a single
  sub-goal.

## Gameplay Teaching Mode

- Owner: `Classes/gameplay_teaching.py`
- Runtime bridge: `Classes/UI.py`, `Classes/UIController.py`,
  `Classes/runtime_composition.py`, `Classes/task_graph.py`, and
  `Classes/dynamic_planner.py`
- Purpose: let operators teach gameplay doctrine for early supervised runs by
  selecting a workflow profile and writing the real button/key sequence they
  use.
- Profiles: guided general, resource gathering, gem gathering, barbarian
  farming, and map navigation.
- Runtime use: the selected profile and notes are carried on `Context` as a
  teaching brief that informs both mission decomposition and next-step
  planning.

## YOLO UI Perception

- Owner: `Classes/object_detector.py`
- Model management: `Classes/model_manager.py`
- Configuration: `ROK_YOLO_WEIGHTS` or `ROK_YOLO_WEIGHTS_URL`
- Behavior: loads local YOLO weights when available; otherwise uses a no-op
  detector that returns no labels. In `L1 approve`, gather/resource missions
  can fall back to an OCR-only `Fix required` review target before stopping.
- Download safety: configured downloads must use HTTPS, stream with a timeout,
  write through a temporary file, and stay below `ROK_YOLO_MAX_BYTES`.
- Purpose: provide structured visible UI labels and target boxes to planner,
  memory, state monitor, and CAPTCHA detection.

## Shared Observation Reuse

- Owners: `Classes/OS_ROKBOT.py`, `Classes/context.py`, and
  `Classes/Actions/dynamic_planner_action.py`
- Behavior: capture the game window and run YOLO once per guarded planner step,
  then reuse that observation for CAPTCHA checks and planner execution.
- Purpose: reduce duplicate capture and detector work without changing the
  supported synchronous runtime path.
- Concurrency: planner pending decisions and shared observations are guarded
  by `Context` locks because they are touched from both workflow and UI code.

## Configuration Security

- Owners: `Classes/config_manager.py`, `Classes/security_utils.py`, and
  `Classes/logging_config.py`
- Secret storage: `OPENAI_KEY`, `OPENAI_API_KEY`, and
  `RUNTIME_JOURNAL_HMAC_KEY` persist through the configured secret provider
  instead of `config.json`.
- Logging: OpenAI-style keys and known secret assignments are redacted before
  console or file handlers emit records.
- Operator note: `.env` is workstation-grade local secret storage, not an
  enterprise vault or central secret-rotation boundary.
- Rule: new sensitive settings must be added to `SENSITIVE_CONFIG_KEYS` and
  must not be written to `config.json`.

## Window Capture Pipeline

- Owner: `Classes/window_handler.py`
- Default backend: Win32 `PrintWindow` with `BitBlt` compatibility fallback.
- Behavior: keeps exact-window, overlay-free client capture semantics while
  exposing a named backend boundary for future capture implementations.
- Runtime diagnostics: capture timing is logged so detector and OCR costs can
  be compared against window-read overhead.

## OCR Perception

- Owner: `Classes/ocr_service.py`
- Engine order: configurable with `OCR_ENGINE`; a configured
  `TESSERACT_PATH` makes bounded Tesseract reads the default live-runtime path.
- EasyOCR remains available when selected and Torch imports cleanly.
- Tesseract calls use `TESSERACT_TIMEOUT_SECONDS` and `OCR_MAX_IMAGE_SIDE` to
  avoid stalling a guarded planner step.
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
- Retention: bounded entry count, atomic JSON replacement, and equivalent
  success merging to limit local memory growth.
- Purpose: reuse successful local decisions before OpenAI calls and support L2
  trusted-label autonomy.

## Recovery Memory

- Owner: `Classes/recovery_memory.py`
- Storage: `data/recovery_memory.json`
- Retention: bounded entry count and atomic JSON replacement.
- Purpose: remember guarded recovery outcomes by state/action/screen signature
  without letting stale failure data grow indefinitely.

## Fix-Based Human Correction

- Owner: `Classes/UI.py`, `Classes/UIController.py`, and `Classes/Actions/dynamic_planner_action.py`
- Memory writer: `Classes/vision_memory.py`
- Dataset export: `Classes/detection_dataset.py`
- Workflow: user presses `Fix`, the console opens a blocking crosshair overlay
  over the game window, the user clicks the corrected target, and the bot
  records a corrected normalized point.
- Purpose: teach local memory and generate correction data when the planner
  chooses the wrong pointer target.

## Autonomy Gate

- Owner: `Classes/Actions/dynamic_planner_action.py`
- L1: pointer-target actions require approval.
- L1 correction review: OCR-only pointer proposals below the normal confidence
  threshold can be shown for manual `Fix` correction when they meet
  `PLANNER_L1_REVIEW_MIN_CONFIDENCE`; uncorrected low-confidence proposals do
  not execute from `OK`. When YOLO returns no boxes on a gather/resource
  mission, the planner can also raise one OCR-only `Fix required` target after
  a detector-less `stop` decision.
- L2: trusted labels can auto-execute pointer actions after enough clean local
  successes.
- L3: validated pointer actions can execute without approval.
- Note: current target approval UI covers `click`, `drag`, and `long_press`,
  and `L1 approve` now draws the current YOLO detector boxes, the selected
  target, and an intent tooltip.
  `key` and `type` are validation-gated and still route through
  `InputController`, but do not use the target approval prompt.

## Centralized Hardware Input

- Owner: `Classes/input_controller.py`
- Backend: Oblita Interception driver through `interception-python`
- Responsibilities: Interception readiness, pause/stop checks, foreground
  checks, pointer bounds validation, bounded humanization, mouse movement,
  click execution, drag execution, long-press execution, key presses, scroll
  pacing, and delay policy.
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
- Runtime gate: `OS_ROKBOT.start(...)` refuses live automation if the emergency
  stop cannot be armed.

## Session Logging

- Owner: `Classes/session_logger.py`, `Classes/run_handoff.py`
- Runtime setup: `Classes/UI.py`
- AI entrypoint: `data/handoff/latest_run.json`, `data/handoff/latest_run.txt`
- History storage: `data/session_logs/`
- Per-run files: `.json`, `.txt`, `.log`, `.err`, and runtime `.ndjson`
- Purpose: one canonical run handoff that points to what ran, why it ended,
  what failed, what to inspect next, and the matching runtime/test artifacts.
- Runtime coverage: planner decisions, approvals, corrections, planner
  rejections, CAPTCHA pauses, warnings, errors, final status, and bounded
  timing samples for capture, OCR, resource-context, planner, and guarded
  input phases.

## Maintainer Command Wrapper

- Owner: `Classes/maintainer_run.py`
- PowerShell entrypoint: `tools/run_maintainer_command.ps1`
- Supported presets: `verify-integrity`, `verify-docs`, `repo-hygiene`,
  `mypy`, `pytest`, `watchdog-once`, `ui`, and `cleanup-test-artifacts`
- Console milestones: `RUN START`, `RUN EVENT`, `RUN ERROR`, `RUN END`
- Purpose: keep documented maintainer commands on the same run-handoff
  contract as runtime sessions.

## Artifact Retention

- Owner: `Classes/artifact_retention.py`, `Classes/run_handoff.py`
- Applies to: `data/session_logs/`, `diagnostics/`, `datasets/recovery/`, and
  `.artifacts/test_runs/`
- Grouping: files with the same stem are retained or deleted together so
  `.png`, `.log`, `.meta`, and `.point` sidecars stay consistent.
- Environment overrides:
  `ROK_SESSION_LOG_MAX_FILES`, `ROK_SESSION_LOG_MAX_AGE_DAYS`,
  `ROK_DIAGNOSTIC_MAX_FILES`, `ROK_DIAGNOSTIC_MAX_AGE_DAYS`,
  `ROK_RECOVERY_DATASET_MAX_SAMPLES`, `ROK_RECOVERY_DATASET_MAX_AGE_DAYS`,
  `ROK_TEST_RUN_SUCCESS_MAX_FILES`, `ROK_TEST_RUN_SUCCESS_MAX_AGE_DAYS`,
  `ROK_TEST_RUN_FAILURE_MAX_FILES`, and `ROK_TEST_RUN_FAILURE_MAX_AGE_DAYS`.

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

## Coverage Gate

- Owner: `pytest.ini`
- Threshold: `>=80%`
- Scope: deterministic planner/runtime modules only:
  `ai_fallback`, `ai_recovery_executor`, `config_manager`, `context`,
  `dynamic_planner`, `health_check`, `maintainer_run`, `model_manager`,
  `OS_ROKBOT`, `planner_decision_policy`, `run_handoff`, `runtime_composition`,
  `runtime_journal`, `security_utils`, and `state_machine`.
- Reason: these modules are stable enough for hard unit-test enforcement;
  Windows/UI/hardware-bound modules remain regression tested but are outside
  the hard fail-under gate.

## Test Tiers

- Owner: `pytest.ini`
- Unit/default tier: runs all normal deterministic tests and the hard coverage
  gate.
- `integration` marker: safe seam tests for watchdog, window capture, OCR,
  object detector adapters, state monitor, and other OS-facing boundaries.
  These tests must not launch live automation.
- `supervised` marker: opt-in workstation/hardware acceptance checks. These
  stay skipped unless an operator sets `OSROKBOT_RUN_SUPERVISED_TESTS=1`.
- Recommended command path: `.\tools\run_maintainer_command.ps1 pytest ...`
  so pytest temp/cache output stays under `.artifacts/test_runs/<run_id>/`.

## Documentation Standard

- Canonical style: Google-style docstrings.
- Required on orchestration-heavy modules, public classes, and non-trivial
  public methods in the supported runtime.
- Capture ownership, collaborators, invariants, side effects, threading, and
  error boundaries where relevant.
- Keep the Mermaid workflow and recovery diagrams in `README.md` synchronized
  with runtime changes.
- `Classes/dynamic_planner.py` and `Classes/state_machine.py` are the
  repository examples for planner and workflow documentation.

## Operator Runbooks

- Owner: `docs/runbooks/`
- Index: `docs/runbooks/README.md`
- Watchdog operations: `docs/runbooks/watchdog-restart.md`
- CAPTCHA handling: `docs/runbooks/captcha-manual-recovery.md`
- Emergency stop: `docs/runbooks/emergency-stop.md`
- Startup readiness: `docs/runbooks/startup-health-check.md`
- YOLO warmup: `docs/runbooks/yolo-warmup-and-download.md`
- OCR degradation: `docs/runbooks/ocr-degradation.md`
- Planner transport: `docs/runbooks/planner-transport-outage.md`
- Secret provisioning: `docs/runbooks/secret-provisioning.md`
- Failure triage: `docs/runbooks/failure-triage.md`
- Run handoff: `docs/runbooks/run-handoff.md`
- Purpose: give supervised operators repeatable steps for high-risk runtime
  situations without changing the safety model.

## Architecture Decision Records

- Owner: `docs/adr/`
- Planner-first runtime: `docs/adr/0001-planner-first-runtime.md`
- Human-in-the-loop safety: `docs/adr/0002-human-in-the-loop-safety.md`
- Composition root and legacy retirement:
  `docs/adr/0004-runtime-composition-and-legacy-retirement.md`
- Purpose: record production architecture decisions that should not be
  rediscovered through code archaeology.

## Documentation Verification

- Owner: `verify_docs.py`
- Checklist: `docs/documentation-review-checklist.md`
- Command: `python verify_docs.py`
- Checks: required documentation artifacts exist, README Mermaid diagrams are
  present, runbooks contain the required operator sections, ADRs declare an
  accepted status, and maintained docs reference the runbooks and ADRs.

## Documentation Sync

- Trigger: any code change that affects architecture, capability surface,
  operator workflow, safety behavior, configuration, runtime storage, or media
  policy.
- Required action: update this file in the same change and review the matching
  sections of `README.md`, `AGENTS.md`, and `MEDIA_MAP.md`.
- Rule: do not leave capability docs describing code paths that are no longer
  supported or omit new guarded runtime behavior that users or maintainers need
  to understand.
