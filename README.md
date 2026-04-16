# OSROKBOT Agentic Upgrade

OSROKBOT is a Windows automation agent for **Rise of Kingdoms**. It combines a
PyQt control overlay, computer vision, OCR, a guarded AI planner, and
hardware-level mouse/keyboard input through the Oblita Interception driver.

This project is powerful because it can physically move your real mouse. Read
the safety section before running it.

## Agentic Architecture

Think of OSROKBOT as three parts working together:

- **Brain: Vision-Language Model (VLM)**  
  The Dynamic Planner sends screenshots, OCR text, visible object labels, and
  your mission prompt to an AI model. The model returns strict JSON describing
  one next step, such as `click`, `wait`, or `stop`.

- **Eyes: YOLO + OCR**  
  YOLOv8 can detect trained Rise of Kingdoms UI elements when you provide local
  weights. EasyOCR reads game text, with Tesseract kept as a fallback for older
  workflows.

- **Hands: Interception**  
  The Oblita Interception driver sends hardware-level mouse and keyboard events.
  The bot uses Bezier mouse paths, click timing jitter, and bounds checks before
  clicking.

The safety layer around the agent includes:

- F12 emergency process kill switch.
- Human-in-the-loop approval mode.
- Foreground-window checks before hardware input.
- CAPTCHA detect-and-pause behavior. The bot does **not** solve CAPTCHAs.

## Important Safety Notes

Interception controls your physical mouse and keyboard. If something goes
wrong, press:

```text
F12
```

That immediately terminates OSROKBOT and returns mouse control to you.

Start with **L1 approve** mode. In this mode the AI cannot click until you press
`OK` in the overlay.

## Requirements

- Windows 10 or Windows 11.
- Python 3.13.
- Rise of Kingdoms installed and runnable in a window named `Rise of Kingdoms`.
- Administrator access for the Interception driver install.
- OpenAI API key for the Dynamic Planner.
- Optional: YOLO `.pt` weights trained for Rise of Kingdoms UI labels.

## Step-by-Step Setup

Open PowerShell in the repository folder:

```powershell
cd C:\Users\hp\OneDrive\Desktop\OSROKBOT-master
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install Python dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Install the Oblita Interception Driver

The Python package is not enough. You must install the Windows kernel driver.

1. Download the official Oblita Interception driver package.
2. Extract it somewhere easy to find, for example:

```text
C:\Tools\Interception
```

3. Open PowerShell as Administrator.
4. Run the installer:

```powershell
cd C:\Tools\Interception\command line installer
.\install-interception.exe /install
```

5. Reboot your PC.

The reboot is mandatory. The driver will not work until Windows restarts.

## Configure OpenAI and Local Paths

Create a `.env` file in the project root:

```dotenv
OPENAI_KEY=your-openai-api-key-here
OPENAI_VISION_MODEL=gpt-5.4-mini
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
ROK_WINDOW_TITLE=Rise of Kingdoms
```

Optional YOLO settings:

```dotenv
ROK_YOLO_WEIGHTS=C:\Users\hp\OneDrive\Desktop\OSROKBOT-master\models\rok-ui.pt
ROK_YOLO_WEIGHTS_URL=https://example.com/your-release/rok-ui.pt
```

Optional game relaunch path:

```dotenv
ROK_CLIENT_PATH=C:\Path\To\RiseOfKingdoms.exe
```

You can also edit most settings from the gear button in the UI. Settings saved
there go into local `config.json`, which is ignored by Git.

## Run OSROKBOT

Start Rise of Kingdoms first. Then run:

```powershell
python Classes\UI.py
```

The overlay appears near the game window.

1. Type a mission in plain English, for example:

```text
Farm the nearest level 4 wood node. Do not spend action points.
```

2. Choose autonomy:

- `L1 approve`: safest. AI asks before clicking.
- `L2 trusted`: labels with enough successful approvals can auto-click.
- `L3 auto`: full autonomy. Use only after testing.

3. Press Play.

When the AI wants to click, the overlay displays the intended target and screen
coordinates. Press:

- `OK` to approve.
- `No` to reject.
- `Fix` to correct. After pressing `Fix`, move your cursor to the correct game
  target. The bot samples that position and saves it to memory.

## Run the Watchdog for Overnight Sessions

The watchdog is a separate safety process. It reads `data/heartbeat.json`, which
OSROKBOT updates while automation is running. If the heartbeat becomes stale,
the watchdog restarts only the tracked bot/game PIDs written in that file.

Open a second PowerShell window in the project folder and run:

```powershell
python watchdog.py
```

For a one-time manual check, run:

```powershell
python watchdog.py --once
```

To let the watchdog relaunch Rise of Kingdoms, configure:

```dotenv
ROK_CLIENT_PATH=C:\Path\To\RiseOfKingdoms.exe
```

If `ROK_CLIENT_PATH` is missing, the watchdog can still restart the UI from the
heartbeat, but it will not guess or kill processes by name.

## Development and QA

Run the integrity checker:

```powershell
python verify_integrity.py
```

Compile-check the code:

```powershell
python -m compileall Classes verify_integrity.py
```

Run unit tests:

```powershell
pytest
```

Run Ruff linting:

```powershell
ruff check .
```

Format code with Ruff:

```powershell
ruff format .
```

## Project Layout

```text
Classes/
  Actions/                    Action wrappers used by state machines.
  UI.py                       PyQt overlay and Commander mission input.
  OS_ROKBOT.py                Runner, pause/stop events, captcha checks.
  dynamic_planner.py          AI planner JSON schema and validation.
  vision_memory.py            CLIP/FAISS local visual memory.
  object_detector.py          YOLO adapter and no-op detector fallback.
  ocr_service.py              EasyOCR first, Tesseract fallback.
  input_controller.py         Interception hardware input and bounds checks.
  emergency_stop.py           F12 emergency kill switch.
  context.py                  Shared runtime state.
Media/                        UI and README assets; gameplay templates are purged.
tests/                        Pure-logic pytest tests.
watchdog.py                   Conservative heartbeat watchdog for overnight runs.
verify_integrity.py           Static and runtime health checks.
requirements.txt              Python dependencies.
pyproject.toml                Ruff and pytest configuration.
```

## Beginner Troubleshooting

### The bot says Interception is unavailable

Install the Oblita driver as Administrator and reboot. Installing only
`interception-python` is not enough.

### The bot does not click

Check these first:

- Rise of Kingdoms is open.
- The game window title is `Rise of Kingdoms`.
- The game is in the foreground.
- You are not paused.
- You approved the AI action in `L1 approve` mode.

### The AI chooses the wrong target

Use `Fix`. Move your cursor to the right target. The bot records that correction
in local visual memory.

### The planner cannot see game objects

YOLO only works when you provide trained weights through `ROK_YOLO_WEIGHTS`.
Without weights, the detector safely returns no detections.

## Design Rules

- Keep DynamicPlanner as the primary workflow.
- Keep gameplay training exports under `datasets/recovery/`, not `Media/`.
- Do not bypass `InputController.validate_bounds(...)`.
- Do not solve or bypass CAPTCHAs.
- Keep secrets in `.env` or `config.json`; both are ignored by Git.
