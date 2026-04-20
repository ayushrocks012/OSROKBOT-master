# Runbook: OCR Degradation

## Trigger

Use this runbook when planner OCR becomes slow, unreadable, inconsistent, or
starts missing labels and resource counts that were previously reliable.

## Immediate Actions

1. Pause the run and switch to `L1 approve`.
2. Open `data/handoff/latest_run.txt` and review the most recent `ocr_regions`,
   `ocr_text`, or resource-context timing entries.
3. Confirm `TESSERACT_PATH`, `TESSERACT_TIMEOUT_SECONDS`, `OCR_ENGINE`, and
   `OCR_MAX_IMAGE_SIDE` are configured as expected.
4. Check `data/planner_latest.png` and any matching diagnostics to confirm the
   capture is sharp, foreground, and not obscured by blockers or overlays.
5. If EasyOCR startup is degraded or Torch imports fail, set `OCR_ENGINE=tesseract`
   and retry on the next supervised run.
6. If the game UI language, theme, or resolution changed, capture updated
   screenshots and open an engineering task with the failing artifacts.

## Verification

- `python verify_integrity.py` should confirm the OCR modules import and the
  configured `TESSERACT_PATH` is accessible.
- Tesseract-only runs should show bounded `ocr_regions` or `ocr_text` timing
  without DLL import failures.
- Resource OCR in `Classes/state_monitor.py` should recover once the capture,
  engine selection, and timeout are corrected.

## Escalation

Escalate when OCR remains unreadable across two supervised runs with clean
foreground captures. Include `latest_run.json`, `data/planner_latest.png`,
relevant diagnostics, configured OCR variables, and the measured timing data.
