# OSROKBOT Operator Runbooks

These runbooks are for supervised workstation operations. They do not replace
the safety rules in `README.md` or the maintainer contract in `AGENTS.md`.

Use these when running overnight sessions, preparing a workstation, or
triaging a paused automation run:

- `watchdog-restart.md`: heartbeat monitoring and conservative restart flow.
- `captcha-manual-recovery.md`: required manual handling when CAPTCHA is detected.
- `emergency-stop.md`: F12 kill switch behavior and recovery after termination.
- `startup-health-check.md`: pre-flight readiness checks before supervised runs.
- `yolo-warmup-and-download.md`: YOLO weight warmup, download, and refresh steps.
- `ocr-degradation.md`: OCR slowdown, unreadable text, and fallback handling.
- `planner-transport-outage.md`: OpenAI planner transport cooldown and outage triage.
- `secret-provisioning.md`: secret-provider selection, `.env` fallback, and Windows DPAPI rotation guidance.
- `failure-triage.md`: first-pass investigation for failed or degraded runs.
- `run-handoff.md`: how to start from `data/handoff/latest_run.*` and follow
  the matching run artifacts.

Default to `L1 approve` after any runbook-driven intervention. Do not resume
automation until the game window is visible, foreground, and safe for input.
