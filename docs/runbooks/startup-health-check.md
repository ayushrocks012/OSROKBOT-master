# Runbook: Startup Health Check

## Trigger

Use this runbook before a supervised run or when the startup health-check
dialog blocks progress because a required or optional dependency is missing.

## Immediate Actions

1. Launch `python Classes\UI.py` and inspect the health-check dialog before
   pressing Continue.
2. Save a valid OpenAI key through the dialog if the API key check is failing.
3. Install or repair the Oblita Interception driver if the hardware-input check
   is failing, then reboot before retrying.
4. Start Rise of Kingdoms and ensure the configured window title matches the
   actual game window.
5. If YOLO weights are missing, either configure `ROK_YOLO_WEIGHTS` or use the
   dialog download flow described in `docs/runbooks/yolo-warmup-and-download.md`.
6. If Tesseract is optional for the current run, continue only if the active
   OCR strategy is otherwise healthy and documented.

## Verification

- The health-check dialog should report `OK` for the API key, Interception, and
  game-window checks before you start a live run.
- Optional YOLO and Tesseract checks should match the intended mission profile.
- `python verify_integrity.py` should agree with the dialog for local config,
  runtime imports, and window reachability.

## Escalation

Escalate when a required health-check item stays in `FAIL` after the local
workstation has been repaired and rebooted. Include a screenshot of the dialog,
the current config values in use, and any `verify_integrity.py` failures.
