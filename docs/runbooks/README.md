# OSROKBOT Operator Runbooks

These runbooks are for supervised workstation operations. They do not replace
the safety rules in `README.md` or the maintainer contract in `AGENTS.md`.

Use these when running overnight sessions, preparing a workstation, or
triaging a paused automation run:

- `watchdog-restart.md`: heartbeat monitoring and conservative restart flow.
- `captcha-manual-recovery.md`: required manual handling when CAPTCHA is detected.
- `emergency-stop.md`: F12 kill switch behavior and recovery after termination.
- `secret-provisioning.md`: local `.env` setup and rotation limits.
- `failure-triage.md`: first-pass investigation for failed or degraded runs.

Default to `L1 approve` after any runbook-driven intervention. Do not resume
automation until the game window is visible, foreground, and safe for input.
