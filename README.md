# OSROKBOT

OSROKBOT is a Windows desktop automation bot for Rise of Kingdoms. It uses a
small PyQt control panel, screenshot/image recognition, OCR, and state-machine
workflows to run repetitive in-game tasks.

## Requirements

- Windows 10 or Windows 11.
- Python 3.13.
- Rise of Kingdoms running in a visible window named `Rise of Kingdoms`.
- Tesseract OCR installed locally for OCR-based workflows.
- A stable `1280x720` game window is recommended because the image templates in
  `Media/` were captured around that resolution.

## Install

Open PowerShell in the project root and install the dependencies:

```powershell
cd C:\path\to\OSROKBOT-master
python -m pip install -r requirements.txt
```

If PyQt5 reports a Qt DLL load error on Python 3.13, reinstall the PyQt trio into
the same user site:

```powershell
python -m pip install --user --force-reinstall PyQt5==5.15.11 PyQt5-Qt5==5.15.2 PyQt5_sip==12.18.0
```

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
  OS_ROKBOT.py          Run loop, pause/stop state, worker threads.
  action_sets.py        Workflow state-machine definitions.
  context.py            Shared runtime state for one bot run.
  state_machine.py      State transition engine.
  image_finder.py       OpenCV template matching.
  window_handler.py     Game-window lookup and screenshots.
Media/                  Image templates and UI icons.
roklyceum.csv           Lyceum question/answer database.
requirements.txt        Python dependencies.
```

## Verification

Run these checks after code changes:

```powershell
python -m compileall Classes
python -c "import cv2, numpy; from PyQt5.QtCore import QObject; print('imports ok')"
```

Do not run live automation checks unless the game window is open and you are
ready for mouse/keyboard input to be controlled.
