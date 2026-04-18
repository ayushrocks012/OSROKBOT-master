# Runbook: Failure Triage

## Trigger

Use this runbook when a mission stalls, repeated recovery occurs, the planner
returns no safe action, OCR/YOLO output degrades, or the bot stops
unexpectedly.

## Immediate Actions

1. Stop or pause the run before inspecting artifacts.
2. Review `data/handoff/latest_run.txt` first, then open
   `data/handoff/latest_run.json` for structured fields such as `top_errors`,
   `key_events`, `artifacts`, and `next_actions`.
3. Follow the artifact paths from `latest_run.json` to the matching per-run
   `.json`, `.log`, `.err`, and runtime `.ndjson` files under
   `data/session_logs/`.
4. Check `timing` events for slow window capture, YOLO, OCR, planner request,
   or guarded input phases.
5. Inspect `diagnostics/` for failure or CAPTCHA screenshots.
6. Check whether `data/planner_latest.png` matches the expected game window.
7. Confirm `ROK_WINDOW_TITLE`, `ROK_YOLO_WEIGHTS`, `OCR_ENGINE`,
   `TESSERACT_PATH`, `TESSERACT_TIMEOUT_SECONDS`,
   `PLANNER_L1_REVIEW_MIN_CONFIDENCE`, and `OPENAI_KEY` or `OPENAI_API_KEY`
   are configured.

## Verification

- `python verify_integrity.py` should pass or report only known environmental
  warnings.
- OCR failures should correlate with missing or invalid `TESSERACT_PATH`, weak
  screenshot quality, changed UI language, a timeout that is too small, or a
  broken EasyOCR/Torch installation. If `ocr_regions` timing is very high and
  EasyOCR logs a Torch DLL error, set `OCR_ENGINE=tesseract`.
- YOLO failures should correlate with missing weights, outdated labels, or
  shifted UI layout.
- Repeated `confidence_below_threshold` planner rejections with
  `yolo_detect detections=0` indicate OCR-only targeting. In `L1 approve`,
  use `Fix` for low-confidence proposals, then click the corrected target in
  the blocking crosshair overlay, or configure YOLO weights for stable target
  boxes.
- If a gather/resource run shows repeated waits followed by `action=stop`
  while `yolo_detect detections=0`, check whether the overlay raised an
  OCR-only `Fix required` target. That is the bounded no-YOLO fallback path in
  `L1 approve`.

## Escalation

Open an engineering task when the same failure occurs across two supervised
runs with reproducible screenshots and handoff artifacts. Include the mission,
autonomy level, `data/handoff/latest_run.json`, the matching per-run session
group, diagnostic screenshot, and whether the game window was foreground.
