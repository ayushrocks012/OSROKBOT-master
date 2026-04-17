# OSROKBOT

OSROKBOT is an agentic Windows automation system for **Rise of Kingdoms**. It
uses a PyQt control overlay, YOLO object detection, OCR, OpenAI vision planning,
local FAISS-backed visual memory, and the Oblita Interception driver for
hardware-level input.

The current architecture is **agentic-first**. A user gives the bot a natural
English mission, the planner observes the current game screen, and
`dynamic_planner.py` returns one validated JSON action at a time. Legacy
gameplay templates and root-level `Media/*.png` assets are no longer part of the
runtime design.

> [!WARNING]
> OSROKBOT can move your real mouse and press real keys through a kernel-level
> input driver. Read the setup and safety sections before running it.

## Table Of Contents

- [Agentic Capabilities](#agentic-capabilities)
- [Architecture](#architecture)
- [Safety Model](#safety-model)
- [Requirements](#requirements)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running OSROKBOT](#running-osrokbot)
- [Correcting The AI With Fix](#correcting-the-ai-with-fix)
- [Watchdog For Overnight Runs](#watchdog-for-overnight-runs)
- [Media Policy](#media-policy)
- [Development Rules](#development-rules)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

## Agentic Capabilities

OSROKBOT is built around a guarded observe-plan-act loop:

1. `Classes/Actions/dynamic_planner_action.py` captures the current game window.
2. `Classes/object_detector.py` adds YOLO labels when `ROK_YOLO_WEIGHTS` is configured.
3. `Classes/ocr_service.py` extracts screen text with EasyOCR and Tesseract fallback.
4. `Classes/vision_memory.py` searches local visual memory before spending an OpenAI call.
5. `Classes/dynamic_planner.py` asks the configured OpenAI vision model for one strict JSON action that references a local target ID.
6. `Classes/Actions/dynamic_planner_action.py` resolves that ID to local geometry, validates the decision, applies the autonomy policy, and only then allows input through `InputController`.

The planner accepts natural English missions such as:

```text
Farm the nearest useful resource safely. Avoid spending action points.
```

It produces a JSON decision with this shape:

```json
{
  "thought_process": "The map view is open and a gather button is visible.",
  "action_type": "click",
  "target_id": "det_3",
  "label": "gather button",
  "confidence": 0.91,
  "delay_seconds": 1.0,
  "reason": "Continue the selected resource gathering flow."
}
```

Supported `action_type` values are:

| Action | Meaning |
| --- | --- |
| `click` | Click a local YOLO/OCR target ID after resolving it to a bounded screen point. |
| `wait` | Pause briefly and observe again. |
| `stop` | Stop the current automation run. |

Invalid action types, low-confidence click decisions, missing or unknown
`target_id` values, and out-of-window targets are rejected before hardware
input. The model is not trusted to author click coordinates directly.

## Architecture

### Core Modules

| Module | Responsibility |
| --- | --- |
| `Classes/UI.py` | PyQt overlay, mission input, autonomy selector, approval buttons, and per-run `Context` creation. |
| `Classes/context.py` | Shared runtime state, planner decisions, state history, UI signal access, and human correction payloads. |
| `Classes/OS_ROKBOT.py` | Executor-backed runner, pause/stop events, foreground checks, CAPTCHA detection, and heartbeat scheduling. |
| `Classes/Actions/dynamic_planner_action.py` | The guarded bridge between observation, planner output, human approval, memory, and input. |
| `Classes/dynamic_planner.py` | OpenAI vision request construction, JSON schema enforcement, memory-first planning, and decision validation. |
| `Classes/object_detector.py` | YOLO adapter plus no-op detector fallback when weights are absent. |
| `Classes/ocr_service.py` | EasyOCR-first text extraction with Tesseract fallback. |
| `Classes/vision_memory.py` | CLIP embeddings, FAISS or NumPy similarity search, success/failure memory, and trusted-label support. |
| `Classes/input_controller.py` | The only allowed input path. It owns bounds validation, Interception calls, pause/stop interlocks, pacing, and cursor behavior. |
| `Classes/emergency_stop.py` | Process-level F12 emergency termination. |
| `watchdog.py` | Heartbeat health monitor and conservative restart utility. |

### Data Flow

```text
Mission text
  -> PyQt overlay
  -> Context
  -> screenshot + YOLO labels + OCR text
  -> local detector/OCR target IDs
  -> VisionMemory lookup
  -> OpenAI JSON planner decision when memory has no safe match
  -> target ID resolution
  -> autonomy gate
  -> InputController bounds check
  -> Interception hardware input
  -> memory update
```

### Design Boundaries

- `Context` is the shared runtime state object. Do not add process-wide mutable globals.
- `InputController` is the only module allowed to execute mouse, keyboard, or scroll input.
- `dynamic_planner.py` is side-effect free: it proposes a decision but never clicks.
- `DynamicPlannerAction` owns human approval, correction recording, memory writes, and guarded click execution.
- CAPTCHA handling is intentionally pause-only. The bot must not solve or bypass CAPTCHAs.

## Safety Model

### Autonomy Levels

The UI exposes three autonomy levels:

| Level | UI Label | Behavior |
| --- | --- | --- |
| L1 | `L1 approve` | Every click decision waits for the human to press `OK`. This is the recommended starting mode. |
| L2 | `L2 trusted` | Labels with enough clean local successes can auto-click. New or untrusted labels still require approval. |
| L3 | `L3 auto` | Valid planner decisions can execute without approval. Use only after testing the mission and memory behavior. |

Use L1 while training local memory or testing new YOLO weights. Use L2 only
after repeated correct approvals. Reserve L3 for stable, supervised workflows.

### F12 Emergency Kill Switch

`Classes/emergency_stop.py` arms a process-level F12 kill switch. It uses the
`keyboard` package and a polling fallback. Pressing F12 calls the configured
exit function immediately.

> [!WARNING]
> F12 is not a pause button. It terminates OSROKBOT immediately so hardware
> input stops even if the UI is unresponsive.

### CAPTCHA Policy

OSROKBOT detects CAPTCHA-like labels through the configured detector. When a
CAPTCHA is detected, it pauses automation and emits a UI state asking for manual
review.

> [!IMPORTANT]
> OSROKBOT intentionally does not solve CAPTCHAs. A human must solve them
> manually before automation resumes.

### Input Guardrails

Before input is sent, OSROKBOT checks:

- The bot is not paused or stopping.
- The target game window is foreground.
- The click point is inside the game client rectangle.
- The Interception backend is available.
- The planner decision is validated and, depending on autonomy level, approved.

## Requirements

- Windows 10 or Windows 11.
- **Python 3.13**. Other versions are not the supported runtime target.
- Rise of Kingdoms installed and runnable in a window titled `Rise of Kingdoms`, unless overridden with `ROK_WINDOW_TITLE`.
- Administrator access for the Oblita Interception kernel driver installation.
- OpenAI API access for planner decisions.
- Optional YOLO `.pt` weights trained for relevant Rise of Kingdoms UI labels.
- Optional Tesseract installation for OCR fallback.

Confirm Python:

```powershell
python --version
```

The expected major/minor version is:

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

### Install The Oblita Interception Driver

The `interception-python` package is only the Python binding. It does not
install the Windows kernel driver.

> [!WARNING]
> Installing dependencies is not enough. You must install the Oblita
> Interception driver from an Administrator PowerShell session and reboot
> Windows before OSROKBOT can send hardware-level input.

Example driver install flow:

```powershell
cd C:\Tools\Interception\command line installer
.\install-interception.exe /install
Restart-Computer
```

After reboot, run the import check:

```powershell
python -c "import interception; interception.auto_capture_devices(); print('interception ok')"
```

## Configuration

Create `.env` in the project root. Keep secrets out of Git.

Minimum recommended configuration:

```powershell
@'
OPENAI_KEY=your-openai-api-key
OPENAI_VISION_MODEL=gpt-5.4-mini
ROK_YOLO_WEIGHTS=C:\Users\hp\OneDrive\Desktop\OSROKBOT-master\models\rok-ui.pt
ROK_WINDOW_TITLE=Rise of Kingdoms
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
'@ | Set-Content -Path .env -Encoding UTF8
```

Core variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_KEY` | Yes | API key used by OpenAI vision planning. `OPENAI_API_KEY` is also accepted. |
| `OPENAI_VISION_MODEL` | Recommended | Vision model used by `dynamic_planner.py`. Defaults to `gpt-5.4-mini`. |
| `ROK_YOLO_WEIGHTS` | Recommended | Local YOLO `.pt` weights for game UI detection. Without this, detector output safely falls back to empty labels. |
| `ROK_WINDOW_TITLE` | Recommended | Target game window title. Defaults to `Rise of Kingdoms`. |
| `TESSERACT_PATH` | Recommended | Tesseract executable path for OCR fallback. |

The default `OPENAI_VISION_MODEL=gpt-5.4-mini` is intentional for current
vision-capable Responses API planning. Override it only if your OpenAI project
does not have access to that model or you want to test another vision-capable
model.

Optional watchdog variables:

```powershell
@'
ROK_CLIENT_PATH=C:\Path\To\RiseOfKingdoms.exe
WATCHDOG_HEARTBEAT_PATH=C:\Users\hp\OneDrive\Desktop\OSROKBOT-master\data\heartbeat.json
WATCHDOG_TIMEOUT_SECONDS=30
WATCHDOG_GAME_RESTART_WAIT_SECONDS=20
WATCHDOG_RESTART_ENABLED=1
'@ | Add-Content -Path .env -Encoding UTF8
```

Settings changed through the overlay gear button are saved to local
`config.json`, which is ignored by Git.

## Running OSROKBOT

Start Rise of Kingdoms first, then run:

```powershell
python Classes\UI.py
```

Basic run flow:

1. Type a mission in plain English.
2. Choose autonomy level.
3. Press Play.
4. In L1, inspect each proposed target and press `OK`, `No`, or `Fix`.

Example missions:

```text
Farm the nearest level 4 wood node without spending action points.
```

```text
Continue the current gathering flow safely. Stop if a CAPTCHA appears.
```

```text
Navigate visible prompts conservatively and wait whenever the safe next action is unclear.
```

## Correcting The AI With Fix

The `Fix` button teaches local visual memory when the planner targets the wrong
UI element.

Workflow:

1. Run in `L1 approve`.
2. Wait for the planner to propose a click.
3. If the target is wrong, press `Fix`.
4. Move the cursor to the correct target inside the game window.
5. Wait for the overlay to capture the corrected normalized position.
6. The correction is recorded by `vision_memory.py` and exported by the dataset helper.

Memory behavior:

- Successful decisions and corrections are stored in `data/vision_memory.json`.
- Screens are embedded with CLIP through `sentence-transformers`.
- FAISS is used for fast similarity search when available.
- If FAISS is unavailable, OSROKBOT falls back to NumPy similarity.
- L2 trusted mode can auto-click labels that have enough successful local memory and no failures.

> [!TIP]
> Train memory in short L1 sessions before using L2. Corrections are local to
> your machine and should reflect your window size, UI language, and account
> state.

## Watchdog For Overnight Runs

`watchdog.py` monitors `data/heartbeat.json`, which is written by
`OS_ROKBOT.write_heartbeat(...)` while automation is running. The heartbeat
contains the bot PID, game PID, window title, mission, repository root, UI
entry point, and Python executable.

Run the watchdog in a second PowerShell window:

```powershell
python watchdog.py
```

Run a one-time check:

```powershell
python watchdog.py --once
```

What the watchdog does:

- Reads only the configured heartbeat file.
- Treats stale heartbeats as a restart condition.
- Terminates only PIDs recorded in the heartbeat.
- Relaunches the game only when `ROK_CLIENT_PATH` is configured.
- Restarts the UI using the Python executable and UI entry point recorded in the heartbeat.

What it does not do:

- It does not kill arbitrary processes by name.
- It does not guess the game install path.
- It does not override CAPTCHA pauses or human approval requirements.

## Media Policy

OSROKBOT no longer uses root-level gameplay template images. The protected media
surface is intentionally small:

| Path | Status | Purpose |
| --- | --- | --- |
| `Media/UI/` | Protected | Overlay icons and local UI assets. |
| `Media/Readme/` | Protected | Documentation images and GIFs. |
| `Media/Legacy/` | Deprecated | Purged by `cleanup_media.py`. |
| `Media/*.png` | Deprecated | Loose root-level gameplay templates purged by `cleanup_media.py`. |

Preview cleanup:

```powershell
python cleanup_media.py --dry-run
```

Delete deprecated media:

```powershell
python cleanup_media.py
```

Delete without prompt:

```powershell
python cleanup_media.py --yes
```

> [!IMPORTANT]
> `cleanup_media.py` deletes only `Media/Legacy/` and loose `Media/*.png`
> files. It does not touch `Media/UI/`, `Media/Readme/`, or files nested under
> other media subdirectories.

## Development Rules

The agentic architecture depends on strict boundaries:

- Do not reintroduce root-level gameplay templates.
- Do not bypass `DynamicPlannerAction` for planner decisions that can click.
- Do not import or call lower-level input libraries outside `Classes/input_controller.py`.
- Do not bypass `InputController.validate_bounds(...)`.
- Do not add process-wide mutable globals. Pass runtime data through `Context`.
- Do not solve or bypass CAPTCHAs.
- Keep generated screenshots, memory, recovery datasets, and logs under `data/`,
  `datasets/`, or `diagnostics/`, not `Media/`.
- Keep `dynamic_planner.py` side-effect free.
- Use `DelayPolicy.wait(...)` or action delays for waits inside action flows.

## Verification

Run these before handing off changes:

```powershell
python verify_integrity.py
python -m compileall Classes verify_integrity.py cleanup_media.py watchdog.py
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
python -m pytest --basetemp .pytest_tmp -o cache_dir=.pytest_cache
```

Optional static checks:

```powershell
python -m ruff check Classes verify_integrity.py cleanup_media.py watchdog.py --select I,UP,RET,SIM,B,F,PTH
python -m vulture Classes verify_integrity.py watchdog.py cleanup_media.py --min-confidence 80
python -m mypy Classes verify_integrity.py cleanup_media.py watchdog.py
```

Notes:

- `verify_integrity.py` checks static structure, required environment values,
  runtime imports, Interception availability, optional YOLO configuration, and
  target game window health.
- The runtime health check expects the game window to be reachable.
- Full `ANN` Ruff enforcement and strict mypy may expose existing annotation
  debt; treat those as type-hardening work, not runtime failures.

## Troubleshooting

### Interception Is Unavailable

Install the Oblita Interception driver as Administrator and reboot. Installing
`interception-python` alone is not sufficient.

### The Bot Does Not Click

Check:

- Rise of Kingdoms is open.
- The configured window title matches the actual game window.
- The game is foreground.
- The bot is not paused.
- Interception is installed and working after reboot.
- L1 approval has been granted, or the current label is trusted in L2.

### The Planner Chooses The Wrong Target

Use `Fix` in L1 mode. Move the cursor to the correct target and let
`vision_memory.py` record the correction.

### YOLO Labels Are Empty

Set `ROK_YOLO_WEIGHTS` to a valid local `.pt` file. If weights are not
configured, OSROKBOT still runs, but detector labels are empty and the planner
relies more heavily on screenshots, OCR, and memory.

### The Watchdog Does Not Relaunch The Game

Set `ROK_CLIENT_PATH` to the game executable. The watchdog will not guess or
search for a game binary.

### CAPTCHA Appears

The bot pauses intentionally. Solve the CAPTCHA manually, then resume only when
it is safe to continue.
