# OSROKBOT

OSROKBOT is a Windows desktop automation bot for Rise of Kingdoms. It uses a
PyQt control panel, OpenCV screenshot analysis, OCR, centralized input control,
and explicit state-machine workflows to run repeatable in-game tasks.

## Requirements

- Windows 10 or Windows 11.
- Python 3.13.
- Rise of Kingdoms running in a visible window named `Rise of Kingdoms`.
- Tesseract OCR installed locally for OCR-based workflows.
- A `1280x720` game window is recommended because the templates in `Media/`
  were captured around that resolution. The bot scales templates, but stable
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

## Core Engine

OSROKBOT is built around a state-first automation engine. Each workflow is a
`StateMachine` composed of small `Action` objects. The state machine runs one
action at a time, reads the boolean result, and moves to the configured success,
failure, or fallback state. Actions implement `execute(context)`, but callers
must enter them through `perform(context)` so UI status updates, safety delays,
and pause/stop checks are applied consistently.

`Context` is the shared runtime object for one bot run. It carries the bot
instance, UI signal emitter, target window title, OCR fields, and extracted
values. New shared state should be added to `Context`, not to process-wide
globals.

`ImageFinder` is the hybrid image engine. It supports grayscale template
matching, automatic alpha masks for transparent PNG templates, multi-scale
search around the current game-window size, ROI-scaled searches, non-maximum
suppression, and SIFT-based world-object matching for terrain-embedded targets.
Template coordinates returned from ROI searches remain in full screenshot space,
so existing click actions and offsets continue to work.

`InputController` is the only input execution layer. It owns `pyautogui` usage,
window-bounds validation, click and move execution, smooth cursor movement,
small click-target sampling, key presses, scrolls, and `DelayPolicy` waits. No
action should import `pyautogui` or bypass this layer.

`OS_ROKBOT` owns the worker threads, pause/stop events, and global pre-action
blocker checks. Before each workflow action, the runner scans for known modal
blockers such as `Media/confirm.png` and `Media/escx.png`; when found, it clears
them through `InputController` so the same bounds checks and interlocks are
preserved.

State checks belong in state-machine preconditions. Use
`precondition=...` and `fallback_state=...` when an action requires a specific
screen, and prefer a reusable `GameStateMonitor` abstraction for named states
such as Map View, City View, modal-open, inventory, troop selection, or march
screen.

## Available Workflows

- `farm_rss_new`: OCR-aware resource gathering.
- `farm_rss`: basic resource gathering.
- `farm_food`, `farm_wood`, `farm_stone`, `farm_gold`: single-resource gathering.
- `farm_barb`, `farm_barb_all`: barbarian farming flows.
- `farm_gems`: continuous gem-deposit scanner.
- `loharjr`, `loharjrt`: marauder and Lohar Jr flows.
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
MEDIA_MAP.md            Active media reference map.
AGENTS.md               Developer rules for AI agents.
SKILLS.md               Technical capability reference.
roklyceum.csv           Lyceum question/answer database.
requirements.txt        Canonical Python dependencies.
verify_integrity.py     Static project health checks.
```

## Project Integrity

Run the integrity checker after any workflow or media change:

```powershell
python verify_integrity.py
```

`verify_integrity.py` performs static checks only. It does not launch live
automation or click the game. It validates:

- every `Media/...` image referenced by `Classes/action_sets.py` exists;
- every literal state-machine transition points to a defined state;
- dynamic resource targets from `Helpers.getRandomRss()` resolve to defined
  states;
- `.env` contains an accessible `TESSERACT_PATH`.

When adding or removing media, update [MEDIA_MAP.md](MEDIA_MAP.md), move unused
root-level media into `Media/Legacy/`, and run the integrity checker before
handing work back.

## Verification

Run these checks before shipping code or documentation that affects workflows,
media, imports, or dependencies:

```powershell
python verify_integrity.py
python -m compileall Classes verify_integrity.py
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
```

If dependencies are only visible outside the sandbox, run the import check in
the same Python environment used to install `requirements.txt`.
