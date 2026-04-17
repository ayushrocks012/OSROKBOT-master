# OSROKBOT

OSROKBOT is a guarded, agentic Windows automation system for **Rise of
Kingdoms**. The current runtime is built around a PyQt control overlay,
window screenshots, optional YOLO UI detection, OCR, OpenAI Responses API
planning, local visual memory, and Oblita Interception hardware input.

This is no longer a root-level image-template bot. The supported runtime path is:

```text
UI mission
  -> Context
  -> StateMachine(plan_next)
  -> DynamicPlannerAction
  -> TaskGraph focused goal
  -> screenshot + YOLO + OCR + resource context + stuck-screen context
  -> VisionMemory lookup
  -> DynamicPlanner JSON decision
  -> validation + autonomy/approval
  -> InputController
```

> [!WARNING]
> OSROKBOT can move the real mouse and press real keys through a
> hardware-level input driver. Install and test it conservatively. Start with
> `L1 approve` for new missions, new YOLO weights, new memory, or any code
> change that can affect input.

## Contents

- [Current Runtime](#current-runtime)
- [Planner Contract](#planner-contract)
- [Architecture Map](#architecture-map)
- [Safety Model](#safety-model)
- [Requirements](#requirements)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running OSROKBOT](#running-osrokbot)
- [Corrections And Memory](#corrections-and-memory)
- [Watchdog](#watchdog)
- [Runtime Data](#runtime-data)
- [Media Policy](#media-policy)
- [Development Rules](#development-rules)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

## Current Runtime

OSROKBOT executes one guarded planner step at a time.

1. `Classes/UI.py` collects a plain-English mission and selected autonomy
   level, then creates a per-run `Context`.
2. `Classes/action_sets.py` builds the supported `dynamic_planner()` state
   machine.
3. `Classes/OS_ROKBOT.py` runs the machine through an executor-backed loop,
   writes heartbeat data, checks foreground state, and pauses on CAPTCHA-like
   detector labels.
4. `Classes/Actions/dynamic_planner_action.py` captures the game window,
   gathers detector/OCR/resource/stuck-screen context, asks `TaskGraph` for the
   current focused sub-goal, then requests one planner decision.
5. `Classes/vision_memory.py` tries to reuse a successful local visual match
   before spending an OpenAI call.
6. `Classes/dynamic_planner.py` calls the OpenAI Responses API when memory has
   no safe match and validates the strict JSON response.
7. `DynamicPlannerAction` resolves target IDs to current screen geometry,
   applies the autonomy policy, records corrections or failures, and sends any
   hardware input through `Classes/input_controller.py`.

The historical action classes and `StateMachine` infrastructure still exist,
but new runtime behavior should enter through `ActionSets.dynamic_planner()`.
Legacy root-level gameplay templates under `Media/` are deprecated.

## Planner Contract

`dynamic_planner.py` is side-effect free. It can propose a decision; it must
not move the mouse, press keys, write memory, or change game state.

The model-facing schema accepts these fields:

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

The model may reference current detector/OCR target IDs, but it must not return
raw `x` or `y` coordinates. `DynamicPlanner.resolve_target_decision(...)`
resolves target IDs to normalized coordinates after schema validation.

Supported actions:

| Action | Required Fields | Runtime Behavior |
| --- | --- | --- |
| `click` | `target_id` | Click the current detector/OCR target center after bounds validation. |
| `drag` | `target_id` plus `end_target_id` or `drag_direction` | Drag from a current target to another target or in a named direction. |
| `long_press` | `target_id` | Hold the current target for a short randomized duration. |
| `key` | `key_name` | Press a supported keyboard key through `InputController`. |
| `type` | `text_content` | Type text one character at a time through `InputController`. |
| `wait` | none | Wait for `delay_seconds`, then observe again. |
| `stop` | none | Stop the current automation run. |

Safety validation rejects unsupported action types, unknown target IDs,
low-confidence input actions, non-finite coordinates, coordinates outside
`0.0..1.0`, and planner delays outside the bounded range.

Current approval behavior is implemented for pointer-target actions:
`click`, `drag`, and `long_press`. `key` and `type` decisions still pass
planner validation and `InputController` pause/foreground/backend guards, but
they do not use the target approval prompt.

## Architecture Map

| Module | Responsibility |
| --- | --- |
| `Classes/UI.py` | PyQt overlay, mission input, settings, autonomy selector, approval controls, mission history, session logging, and per-run `Context` creation. |
| `Classes/context.py` | Shared runtime state, planner approval payloads, state history, resource cache, UI anchors, and signal access. |
| `Classes/action_sets.py` | Supported workflow factory. `dynamic_planner()` is the current runtime entry point. |
| `Classes/state_machine.py` | Deterministic action runner, preconditions, transition history, diagnostics, and tiered global recovery. |
| `Classes/OS_ROKBOT.py` | Executor-backed run loop, pause/stop events, foreground guard, CAPTCHA pause, heartbeat writing, and emergency-stop startup. |
| `Classes/Actions/dynamic_planner_action.py` | Observation, OCR/detector/resource context, task graph focus, approval flow, memory updates, correction export, and guarded execution. |
| `Classes/dynamic_planner.py` | OpenAI Responses API request construction, strict JSON schema validation, target resolution, retry handling, memory-first decision selection, and decision validation. |
| `Classes/task_graph.py` | One-time mission decomposition into sub-goals, cached per mission, with label/OCR post-condition tracking. |
| `Classes/object_detector.py` | YOLO detector adapter and no-op fallback when weights are absent or unavailable. |
| `Classes/ocr_service.py` | EasyOCR-first OCR with Tesseract text and region fallback. |
| `Classes/state_monitor.py` | Coarse game-state classification, blocker clearing, idle march-slot OCR, action-point OCR, and explicit client restart support. |
| `Classes/screen_change_detector.py` | Perceptual-hash screen change checks and repeated-action warnings for the planner prompt. |
| `Classes/vision_memory.py` | CLIP embeddings, FAISS or NumPy similarity search, success/failure memory, corrections, and trusted-label checks. |
| `Classes/input_controller.py` | The only allowed hardware input path. It owns Interception readiness, pause/stop checks, foreground checks, bounds validation, mouse movement, clicks, keys, scrolls, and waits. |
| `Classes/model_manager.py` | Local YOLO weight discovery and optional HTTPS download from `ROK_YOLO_WEIGHTS_URL`. |
| `Classes/detection_dataset.py` | Planner no-decision stubs and correction export for detector training data. |
| `Classes/session_logger.py` | Local session summary and event logging. |
| `Classes/emergency_stop.py` | F12 process-level emergency termination. |
| `watchdog.py` | Conservative heartbeat monitor and tracked-process restart helper. |

## Safety Model

### Autonomy Levels

| Level | UI Label | Behavior |
| --- | --- | --- |
| L1 | `L1 approve` | `click`, `drag`, and `long_press` wait for human approval. Use this by default. |
| L2 | `L2 trusted` | Pointer actions with locally trusted labels can execute after enough clean successes. New or failed labels still require approval. |
| L3 | `L3 auto` | Validated pointer actions can execute without approval. Use only for stable, supervised workflows. |

The trusted-label threshold defaults to `3` clean successes and can be adjusted
with `PLANNER_TRUSTED_SUCCESS_COUNT`.

### Input Guardrails

All hardware input must go through `InputController`. Before input is sent,
the code checks:

- The Interception backend is installed and hooked.
- The bot is not paused or stopping.
- The configured game window is foreground.
- Pointer coordinates are inside the current game window.
- Pointer-target planner decisions are validated and gated by the selected
  autonomy level.

### F12 Emergency Stop

`Classes/emergency_stop.py` arms a process-level F12 kill switch. It uses the
`keyboard` package and a polling fallback. Pressing F12 terminates OSROKBOT so
hardware input stops even if the overlay is unresponsive.

### CAPTCHA Policy

`Classes/OS_ROKBOT.py` pauses automation when detector labels match
`captcha`, `captchachest`, or `captcha_chest`.

> [!IMPORTANT]
> OSROKBOT intentionally does not solve, bypass, or automate CAPTCHAs. A human
> must handle them manually before resuming.

## Requirements

- Windows 10 or Windows 11.
- Python 3.13, matching `pyproject.toml` and `requirements.txt`.
- Rise of Kingdoms running in a window titled `Rise of Kingdoms`, unless
  overridden with `ROK_WINDOW_TITLE`.
- Administrator access to install the Oblita Interception kernel driver.
- OpenAI API access for planner decisions and task decomposition.
- Optional YOLO `.pt` weights for Rise of Kingdoms UI labels.
- Optional Tesseract installation for OCR fallback.

Confirm Python:

```powershell
python --version
```

Expected:

```text
Python 3.13.x
```

## Setup

Open PowerShell in the project root:

```powershell
cd C:\Users\hp\OneDrive\Desktop\OSROKBOT-master
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Install Interception

The `interception-python` package is only the Python binding. The Windows
driver must be installed separately from an Administrator PowerShell session.

Example:

```powershell
cd C:\Tools\Interception\command line installer
.\install-interception.exe /install
Restart-Computer
```

After reboot:

```powershell
python -c "import interception; interception.auto_capture_devices(); print('interception ok')"
```

## Configuration

Configuration is read in this order:

1. Local `config.json`, written by the overlay settings UI.
2. Project `.env`.
3. Process environment variables.

Create `.env` in the project root for secrets and local paths:

```powershell
@'
OPENAI_KEY=your-openai-api-key
OPENAI_VISION_MODEL=gpt-5.4-mini
ROK_WINDOW_TITLE=Rise of Kingdoms
ROK_YOLO_WEIGHTS=C:\Users\hp\OneDrive\Desktop\OSROKBOT-master\models\rok-ui.pt
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
'@ | Set-Content -Path .env -Encoding UTF8
```

Core variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_KEY` or `OPENAI_API_KEY` | Yes | API key for OpenAI Responses API planning. |
| `OPENAI_VISION_MODEL` | Recommended | Planner and task-graph model. Defaults to `gpt-5.4-mini`. |
| `ROK_WINDOW_TITLE` or `WINDOW_TITLE` | Recommended | Target game window title. Defaults to `Rise of Kingdoms`. |
| `ROK_YOLO_WEIGHTS` | Optional | Local YOLO `.pt` file. Without it, detector output safely falls back to empty labels. |
| `ROK_YOLO_WEIGHTS_URL` | Optional | HTTPS URL used by `ModelManager` to download YOLO weights into `models/`. |
| `TESSERACT_PATH` | Optional | Tesseract executable path for OCR fallback and resource OCR. |
| `PLANNER_AUTONOMY_LEVEL` | Optional | Default UI autonomy level, `1` to `3`. |
| `PLANNER_TRUSTED_SUCCESS_COUNT` | Optional | Clean local successes needed for L2 trusted labels. Defaults to `3`. |
| `ROK_CLIENT_PATH` | Optional | Game executable used by watchdog or state recovery when explicit restart is enabled. |
| `WATCHDOG_HEARTBEAT_PATH` | Optional | Heartbeat file path. Defaults to `data/heartbeat.json`. |
| `WATCHDOG_TIMEOUT_SECONDS` | Optional | Heartbeat staleness threshold. Defaults to `30`. |
| `WATCHDOG_GAME_RESTART_WAIT_SECONDS` | Optional | Delay after relaunching the game client. Defaults to `20`. |
| `WATCHDOG_RESTART_ENABLED` | Optional | Set to `0` to make watchdog stale-heartbeat handling report-only. |

## Running OSROKBOT

Start Rise of Kingdoms first, then run:

```powershell
python Classes\UI.py
```

Recommended first run:

1. Choose `L1 approve`.
2. Enter a narrow mission in plain English.
3. Press Play.
4. Review each pointer action.
5. Use `OK`, `No`, or `Fix` from the approval controls.

Example missions:

```text
Farm the nearest level 4 wood node without spending action points.
```

```text
Continue the current gathering flow safely. Stop if a CAPTCHA appears.
```

```text
Navigate visible prompts conservatively. Wait whenever the safe next action is unclear.
```

## Corrections And Memory

Use `Fix` when the planner chooses the wrong pointer target.

1. Run in `L1 approve`.
2. Wait for a `click`, `drag`, or `long_press` proposal.
3. Press `Fix`.
4. Move the cursor to the corrected target inside the game window.
5. Let the overlay capture the corrected normalized point.

Correction data is written through:

- `Classes/vision_memory.py` to `data/vision_memory.json`.
- `Classes/detection_dataset.py` to local training/export data.

Memory behavior:

- Screens are embedded with CLIP via `sentence-transformers`.
- FAISS is used when available.
- NumPy similarity is used as a fallback.
- Successes, failures, and manual corrections influence future planner
  decisions and L2 trusted-label behavior.

## Watchdog

`watchdog.py` monitors the heartbeat written by `OS_ROKBOT.write_heartbeat(...)`.
The heartbeat records the bot PID, game PID, window title, mission, autonomy
level, repository root, UI entry point, and Python executable.

Run the watchdog in a second PowerShell window:

```powershell
python watchdog.py
```

Run one check and exit:

```powershell
python watchdog.py --once
```

The watchdog is intentionally conservative:

- It reads only the configured heartbeat file.
- It terminates only PIDs recorded in the heartbeat.
- It relaunches the game only when `ROK_CLIENT_PATH` is configured and restart
  is enabled.
- It restarts the UI using the Python executable and UI entry point from the
  heartbeat.
- It does not override CAPTCHA pauses or human approval rules.

## Runtime Data

| Path | Purpose |
| --- | --- |
| `config.json` | Local settings saved by the overlay. Ignored by Git. |
| `.env` | Local secrets and machine-specific paths. Ignored by Git. |
| `data/vision_memory.json` | Local planner successes, failures, and corrections. |
| `data/heartbeat.json` | Watchdog heartbeat. |
| `data/session_logs/` | Local session logs and summaries. |
| `data/planner_latest.png` | Most recent planner screenshot. |
| `datasets/` | Exported correction/training data. |
| `diagnostics/` | Failure, CAPTCHA, and recovery screenshots/logs. |
| `models/` | Optional local YOLO weights. |

## Media Policy

Protected media directories:

- `Media/UI/`
- `Media/Readme/`

Deprecated media:

- `Media/Legacy/`
- Loose root-level `Media/*.png`

Preview cleanup:

```powershell
python cleanup_media.py --dry-run
```

Delete deprecated media:

```powershell
python cleanup_media.py --yes
```

`cleanup_media.py` does not delete `Media/UI/`, `Media/Readme/`, or files
nested under other `Media/` subdirectories.

## Development Rules

- Keep shared runtime state in `Context`; do not reintroduce `global_vars.py`
  or process-wide mutable runtime state.
- Route all hardware input through `InputController`.
- Do not bypass `InputController.validate_bounds(...)` for pointer actions.
- Keep `dynamic_planner.py` side-effect free.
- Keep agentic input execution behind `DynamicPlannerAction`.
- Do not solve, bypass, or automate CAPTCHAs.
- Do not add root-level gameplay templates under `Media/`.
- Store generated screenshots, memory, recovery datasets, and logs under
  `data/`, `datasets/`, `diagnostics/`, or `models/` as appropriate.
- Use `DelayPolicy.wait(...)` or action-level delays for waits inside action
  flows.
- Do not launch live automation in tests unless explicitly requested.

## Verification

Run these before handing off changes:

```powershell
python verify_integrity.py
python -m compileall Classes verify_integrity.py cleanup_media.py watchdog.py
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
python -m pytest --basetemp .pytest_tmp -o cache_dir=.pytest_cache
```

Useful static checks:

```powershell
python -m ruff check Classes verify_integrity.py cleanup_media.py watchdog.py --select I,UP,RET,SIM,B,F,PTH
python -m vulture Classes verify_integrity.py watchdog.py cleanup_media.py --min-confidence 80
```

`verify_integrity.py` checks project structure, media references, required
configuration, runtime imports, Interception availability, optional YOLO
weights, watchdog configuration, and target game-window health. If it fails
only because the Rise of Kingdoms window is not open, report that directly
instead of weakening the check.

## Troubleshooting

### Interception Is Unavailable

Install the Oblita Interception driver as Administrator and reboot. Installing
`interception-python` alone is not enough.

### The Bot Does Not Click

Check:

- Rise of Kingdoms is open.
- The configured window title matches the actual game window.
- The game window is foreground.
- The bot is not paused.
- Interception is installed and hooked after reboot.
- L1 approval was granted, or the label is trusted in L2, or L3 is selected.

### The Planner Chooses The Wrong Target

Run in `L1 approve` and use `Fix` so visual memory records the corrected target.

### YOLO Labels Are Empty

Set `ROK_YOLO_WEIGHTS` to a valid local `.pt` file or set
`ROK_YOLO_WEIGHTS_URL` to an HTTPS URL that `ModelManager` can download.
Without weights, OSROKBOT still runs with empty detector labels and relies more
on screenshots, OCR, and memory.

### OCR Is Weak

Install Tesseract and set `TESSERACT_PATH`. EasyOCR is tried first, but
Tesseract is used for fallback text/region reads and resource counters.

### The Watchdog Does Not Relaunch The Game

Set `ROK_CLIENT_PATH` to the game executable and keep
`WATCHDOG_RESTART_ENABLED` unset or nonzero. The watchdog will not guess a game
install path.

### CAPTCHA Appears

The bot pauses intentionally. Solve the CAPTCHA manually, then resume only when
it is safe to continue.
