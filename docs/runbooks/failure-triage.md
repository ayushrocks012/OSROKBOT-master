# Runbook: Failure Triage

## Trigger

Use this runbook when a mission stalls, repeated recovery occurs, the planner
returns no safe action, OCR/YOLO output degrades, or the bot stops
unexpectedly.

## Immediate Actions

1. Stop or pause the run before inspecting artifacts.
2. Review the latest file under `data/session_logs/`.
3. Check `timing` events for slow window capture, YOLO, OCR, planner request,
   or guarded input phases.
4. Inspect `diagnostics/` for failure or CAPTCHA screenshots.
5. Check whether `data/planner_latest.png` matches the expected game window.
6. Confirm `ROK_WINDOW_TITLE`, `ROK_YOLO_WEIGHTS`, `TESSERACT_PATH`, and
   `OPENAI_KEY` or `OPENAI_API_KEY` are configured.

## Verification

- `python verify_integrity.py` should pass or report only known environmental
  warnings.
- OCR failures should correlate with missing or invalid `TESSERACT_PATH`, weak
  screenshot quality, or changed UI language.
- YOLO failures should correlate with missing weights, outdated labels, or
  shifted UI layout.

## Escalation

Open an engineering task when the same failure occurs across two supervised
runs with reproducible screenshots and session logs. Include the mission,
autonomy level, latest session log, diagnostic screenshot, and whether the game
window was foreground.
