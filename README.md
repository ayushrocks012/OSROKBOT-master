# OSROKBOT

OSROKBOT is a Windows desktop automation bot for Rise of Kingdoms. It uses a
PyQt control panel, OpenCV screenshot analysis, OCR, and explicit state-machine
workflows to run repeatable in-game tasks.

## Requirements

- Windows 10 or Windows 11.
- Python 3.13.
- Rise of Kingdoms running in a visible window named `Rise of Kingdoms`.
- Tesseract OCR installed locally for OCR-based workflows.
- A `1280x720` game window is recommended because the templates in `Media/`
  were captured around that resolution. The bot can scale templates, but stable
  window sizing improves accuracy.

## Install

Open PowerShell in the project root and install the canonical dependency file:

```powershell
cd C:\path\to\OSROKBOT-master
python -m pip install -r requirements.txt
```

There is intentionally only one requirements file: [requirements.txt](requirements.txt).
Do not add nested requirements files under `Classes/`.

## Configure

Create or update `.env` in the project root:

```dotenv
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
ANTIALIAS_METHOD=LANCZOS
EMAIL=your-email@example.com
OPENAI_KEY=your-openai-api-key
```

Configuration notes:

- `TESSERACT_PATH` is required for Lyceum/OCR workflows.
- `EMAIL` is used by the captcha notification workflow.
- `OPENAI_KEY` is only needed when Lyceum falls back to ChatGPT.
- Keep `.env` private. It is intentionally ignored by Git.

## Run

Start Rise of Kingdoms first, then run:

```powershell
python Classes\UI.py
```

The overlay appears near the game window. Choose a workflow from the dropdown,
optionally keep captcha detection enabled, and press the play button.

## Core Model

OSROKBOT is built around a small set of explicit runtime services:

- `Context` carries shared per-run state such as the bot instance, UI signal
  emitter, target window title, OCR fields, and extracted values.
- `StateMachine` executes one action at a time and moves to the configured
  success, failure, or precondition-fallback state.
- `Action` is the base class for workflow steps. Actions should implement
  `execute(context)` and be run through `perform(context)` so delays, UI status,
  and stop/pause checks are applied consistently.
- `ImageFinder` is the hybrid image engine. It supports grayscale template
  matching, multi-scale search, alpha masking for 4-channel PNG templates, ROI
  search regions, non-maximum suppression, and SIFT-based world-object matching.
- `InputController` is the only layer that executes mouse and keyboard events.
  It owns click bounds validation, movement pacing, coordinate sampling, key
  presses, scrolls, and pause/abort interlocks.
- `OS_ROKBOT` owns worker threads and performs a pre-action blocker check for
  common UI blockers such as `confirm.png` and `escx.png`.

## Safety

The input system is centralized so workflow actions cannot accidentally bypass
runtime safety checks.

- `DelayPolicy` applies consistent waits and jittered pacing for click settle,
  key hold, scroll settle, and action delays.
- `InputController.validate_bounds(x, y, window_rect)` blocks clicks and moves
  outside the active game client area.
- `InputController.is_allowed(context)` checks pause/stop state before input
  and during waits, so the bot can stop between movement steps and action delays.
- Actions should not call `pyautogui` directly. Use `InputController` or an
  existing action wrapper such as `ManualClickAction`, `ManualMoveAction`, or
  `PressKeyAction`.
- Live automation tests should only run when the game window is open and you are
  ready for mouse/keyboard control.

## Available Workflows

- `farm_rss_new`: OCR-aware resource gathering.
- `farm_rss`: basic resource gathering.
- `farm_food`, `farm_wood`, `farm_stone`, `farm_gold`: single-resource gathering.
- `farm_barb`, `farm_barb_all`: barbarian farming flows.
- `farm_gems`: continuous gem-deposit scanner.
- `lyceum`, `lyceumMid`: Lyceum quiz helpers.

## Project Layout

```text
Classes/
  Actions/              Individual bot actions.
  UI.py                 PyQt control overlay.
  OS_ROKBOT.py          Run loop, pause/stop state, worker threads, blocker checks.
  action_sets.py        Workflow state-machine definitions.
  context.py            Shared runtime state for one bot run.
  state_machine.py      State transition and precondition engine.
  image_finder.py       Hybrid OpenCV matching engine.
  input_controller.py   Centralized input, bounds checks, and pacing.
  window_handler.py     Game-window lookup and screenshots.
Media/                  Active image templates and UI icons.
Media/Legacy/           Archived templates not referenced by current workflows.
roklyceum.csv           Lyceum question/answer database.
requirements.txt        Canonical Python dependencies.
verify_integrity.py     Static project health checks.
```

## Verification

Run these checks after code or asset changes:

```powershell
python verify_integrity.py
python -m compileall Classes verify_integrity.py
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
```

`verify_integrity.py` checks that workflow image paths exist, state-machine
transitions target valid states, and `.env` contains an accessible
`TESSERACT_PATH`.
