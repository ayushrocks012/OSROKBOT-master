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
templates have been removed from the repository. `Media/Legacy/` and loose
root-level `Media/*.png` files are deprecated and are purged by
`cleanup_media.py`.

## Ownership Boundaries

| Module | Owns |
| --- | --- |
| `Classes/UI.py` | Agent Supervisor Console view, shell layout, tabs, tray notifications, state-responsive collapse/expand behavior, and overlay presentation. |
| `Classes/UIController.py` | Mission history, autonomy selection, teaching-mode selections, pending-action evaluation, YOLO warmup, session logging, and per-run `Context` creation for the supervisor console. |
| `Classes/runtime_composition.py` | Explicit startup composition root for the supervisor console, shared detector/window/input collaborators, and per-run `Context` factory wiring plus gameplay teaching-brief injection. |
| `Classes/click_overlay.py` | Non-blocking planner preview overlay plus the blocking crosshair correction overlay used by `Fix`. |
| `Classes/context.py` | Thread-guarded shared runtime state, per-step observation cache, planner approval payloads, state history, resource cache, UI anchors, UI signal access, gameplay teaching selections, and per-run runtime collaborator factories. |
| `Classes/action_sets.py` | Supported workflow factory. New runtime work should use `dynamic_planner()`. |
| `Classes/state_machine.py` | Deterministic state execution, preconditions, transition history, diagnostics, and tiered global recovery. |
| `Classes/runtime_contracts.py` | Shared typed Protocols and aliases for active state/action, detector/OCR, input/state-monitor factories, and window-capture boundaries. |
| `Classes/runtime_payloads.py` | Shared TypedDict payloads for heartbeat, planner approval, thread-local step scope, recovery handoff, state history, and resource context. |
| `Classes/runtime_journal.py` | HMAC-chained runtime journal, committed-state checkpoint writes, and interrupted-run journal reconciliation. |
| `Classes/artifact_retention.py` | Shared retention policies for diagnostics, session logs, and recovery dataset exports. |
| `Classes/run_handoff.py` | Canonical run-record builder, latest-run handoff refresh, incomplete-run reconciliation, and centralized test-artifact cleanup helpers. |
| `Classes/OS_ROKBOT.py` | Executor-backed run loop, injectable runtime services, pause/stop events, foreground guard, shared observation reuse, CAPTCHA pause, heartbeat scheduling, state-machine cleanup, and emergency-stop startup. |
| `Classes/Actions/dynamic_planner_action.py` | Planner-step orchestration that composes observation, approval, feedback, and execution services. |
| `Classes/Actions/dynamic_planner_services.py` | Planner observation, approval, execution, and feedback services used by `DynamicPlannerAction`. |
| `Classes/dynamic_planner.py` | Side-effect-free OpenAI planning, dedicated async transport, jittered retries, circuit-breaker fallback, strict JSON schema validation, deterministic city-to-map and map-to-search fallbacks for gather workflows, gameplay teaching-brief prompt injection, target resolution, and memory-first decision selection. |
| `Classes/gameplay_teaching.py` | Central teaching-mode gameplay profile catalog, operator question prompts, mission-specific focus hints, and planner/task-graph teaching briefs. |
| `Classes/planner_decision_policy.py` | Canonical decision verdict for execution readiness, Fix-required review, rejection reasons, and pointer safety rules shared by planner, UI, and approval services. |
| `Classes/task_graph.py` | Mission decomposition through the shared planner transport, sub-goal cache keyed by mission plus teaching brief, focused-goal text, and label/OCR completion checks. |
| `Classes/vision_memory.py` | CLIP embeddings, FAISS/NumPy similarity search, bounded atomic persistence, duplicate-success merging, success/failure memory, corrections, and trusted-label checks. |
| `Classes/ocr_service.py` | Configurable OCR engine order, bounded Tesseract planner text/region reads, and normalized OCR target extraction. |
| `Classes/recovery_memory.py` | Bounded atomic persistence for guarded recovery outcomes keyed by state/action/screen signatures. |
| `Classes/screen_change_detector.py` | Stuck-screen and repeated-action warnings for the planner prompt. |
| `Classes/state_monitor.py` | Coarse game-state checks, blocker clearing, march-slot OCR, action-point OCR, explicit client restart support, and injectable window/input/detector collaborators. |
| `Classes/input_controller.py` | All mouse, keyboard, and scroll execution, including bounded humanization for pointer actions. No other module should call lower-level input APIs. |
| `Classes/window_handler.py` | Foreground enforcement, client-rect discovery, and named window capture backends. |
| `Classes/model_manager.py` | Local YOLO weight discovery and optional HTTPS download with timeout, streaming, and size-cap enforcement. |
| `Classes/security_utils.py` | Secret redaction, dotenv parsing/updates, and atomic text writes for sensitive local configuration. |
| `Classes/secret_providers.py` | Secret-provider chain, `.env` fallback, and Windows DPAPI-backed encrypted local secret storage. |
| `Classes/session_logger.py` | Runtime-session wrapper over the shared run-handoff contract. |
| `Classes/maintainer_run.py` | Canonical maintainer command runner for documented PowerShell workflows, stdout/stderr capture, and centralized pytest artifact layout. |
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
- Do not reintroduce `Actions.legacy` imports or a new legacy action package.
- Do not move protected media under `Media/UI/` or `Media/Readme/` without updating `MEDIA_MAP.md`.
- Keep `dynamic_planner.py` side-effect free. It may propose JSON decisions; it must not execute input.
- Keep agentic input execution behind `DynamicPlannerAction` and `InputController`.
- Keep gameplay teaching doctrine centralized in `Classes/gameplay_teaching.py` or the teaching-mode UI/config flow instead of scattering operator workflow prose across unrelated modules.
- Keep runner-owned dependencies injectable and close planner/action resources during run teardown.
- Prefer per-run collaborator factories on `Context` for runtime seams that must stay mockable in CI without live Windows dependencies.
- Keep startup/runtime composition in `UI.py` or another explicit composition root; `OS_ROKBOT` should stay orchestration-focused.
- Store sensitive configuration in the configured secret provider or process environment, never in `config.json`.
- Treat `.env` as workstation-grade fallback storage. On Windows, prefer the DPAPI provider when you need local encrypted at-rest secrets without an external vault.
- Do not log API keys, passwords, tokens, or full secret assignment values.
- Keep long-running downloads and warmups off the PyQt UI thread.
- Keep generated diagnostics, session logs, and recovery exports bounded with the shared artifact retention policies.
- Keep `data/handoff/latest_run.json` and `data/handoff/latest_run.txt` as the canonical AI/operator entrypoint when modifying runtime or maintainer logging behavior.
- Keep `data/logs/osrokbot.log` machine-ingestable; structured JSON is the default file-log contract unless maintainers intentionally document a replacement.
- Keep `latest_run.json` and `latest_run.txt` refreshed during active runtime or maintainer sessions when changing handoff/session logging behavior; do not regress them to finalize-only snapshots.
- Keep the runtime journal checkpoint aligned to the last committed logical transition. Do not advance it on approval waits, raw input start, or other uncommitted side effects.
- Resume after crashes or F12 from the journal checkpoint only, and re-observe the game window before any new hardware input.
- Keep runtime timing telemetry for capture, detector, OCR, planner, and input phases flowing into the current grouped session artifacts and latest-run handoff when modifying those paths.
- When code changes behavior, architecture, configuration, runtime data paths, safety rules, or operator workflow, update the affected documentation in the same change. At minimum, review `README.md`, `AGENTS.md`, `SKILLS.md`, and `MEDIA_MAP.md`.
- Update operator runbooks under `docs/runbooks/` when watchdog, CAPTCHA, emergency-stop, secret provisioning, telemetry, OCR degradation, planner transport, YOLO warmup, startup health-check, or failure-triage behavior changes.
- Keep documented maintainer verification commands routed through `tools/run_maintainer_command.ps1` so stdout/stderr capture, latest-run refresh, and centralized pytest artifact cleanup stay consistent.
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
`PlannerDecision`. Planner decisions and task-graph decomposition share a
dedicated async transport with jittered exponential backoff and a circuit
breaker, but the planner surface used by the runtime remains synchronous.

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

In `L1 approve`, planner decisions wait for human OK/No review before input
execution. Pointer-target actions (`click`, `drag`, and `long_press`) also
support the `Fix` correction overlay. `key`, `type`, `wait`, and `stop` remain
validated and routed through their existing guarded execution paths after
manual approval. `L2 trusted` and `L3 auto` keep the existing automatic path for
validated non-pointer decisions.

## Human-In-The-Loop Safety

The UI autonomy levels are part of the safety model:

- `L1 approve`: planner decisions wait for `OK`.
- `L2 trusted`: locally trusted labels can auto-execute pointer actions after enough clean successes.
- `L3 auto`: validated pointer actions can execute without approval.

In `L1 approve`, the overlay highlights the selected target and the current
YOLO detector boxes for faster human verification and shows an intent tooltip
next to the selected target. `Fix` must open the blocking crosshair overlay
over the game client and wait indefinitely for one operator click. When a
run has no pending approval, the supervisor console may collapse out of the
topmost window layer, but approval, pause, CAPTCHA, and operator-action states
must restore the console before human input is needed. Planner trace UI should
show the latest focused goal, visible detector/OCR context, planner debug note,
selected action, reason, and confidence without bypassing validation. When a
human rejects a planner decision with a typed correction note, that note should
be routed into bounded planner memory for future prompts in the same run; it
must not directly execute hardware input or bypass action validation. In `L1
approve`, non-pointer planner actions may also require manual OK/No review.
When a gather/resource mission has no detector boxes, the planner may surface one
OCR-only `Fix required` target instead of stopping, but only when the current
OCR text also looks like a true resource/map screen; digit-only OCR targets
must not be surfaced for review. Focused `Open the world map` steps on
city-looking screens should use a deterministic ladder: guarded `space`,
then the fixed map-toggle button if city view persists, then guarded `f`
once city markers disappear and the resource-search interface is still not
open. No-progress/failure feedback should be fed back into bounded planner
memory so repeated bad actions are discouraged in later prompts. Teaching mode
should stay paired with supervised `L1 approve` runs and must feed its
gameplay doctrine through `Context.teaching_brief` into task decomposition and
planner prompts.

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
.\tools\run_maintainer_command.ps1 verify-integrity
.\tools\run_maintainer_command.ps1 verify-docs
.\tools\run_maintainer_command.ps1 repo-hygiene
python -m compileall Classes verify_integrity.py verify_docs.py cleanup_media.py watchdog.py
python -c "import numpy; from PyQt5.QtCore import QObject; print('imports ok')"
.\tools\run_maintainer_command.ps1 mypy
.\tools\run_maintainer_command.ps1 pytest
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

The maintainer wrapper centralizes pytest temp/cache output under
`.artifacts/test_runs/<run_id>/`, copies the latest run handoff into that
folder, and provides `cleanup-test-artifacts` for historical `.pytest_tmp*`,
`.pytest_cache*`, `pytest-cache-files-*`, and `data/smoke_config_tests`
cleanup after migration.

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
