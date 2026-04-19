# Runbook: Emergency Stop

## Trigger

Use this runbook when input is unsafe, the overlay is unresponsive, the wrong
window is foreground, or automation continues after an operator pause.

## Immediate Actions

1. Press `F12` once.
2. Wait for the OSROKBOT process to terminate.
3. If input continues, use Windows Task Manager to terminate the Python process
   that launched `Classes/UI.py`.
4. Do not restart automation until the game client is visible and the
   foreground window is correct.
5. On the next launch, review `data/handoff/latest_run.txt` and
   `data/handoff/latest_run.json` for the `Resume Boundary` section before
   starting another run.

## Verification

- `OS_ROKBOT.start(...)` refuses live automation when the emergency stop cannot
  arm.
- A restarted session should write a fresh heartbeat to `data/heartbeat.json`.
- `latest_run.json` should report the last committed runtime checkpoint and
  `resume_policy=reobserve_before_input` for the interrupted session.
- The first resumed mission should use `L1 approve`.

## Escalation

Treat any emergency-stop failure as a release blocker. Do not operate live
automation on that workstation until Interception, keyboard hooks, and process
termination behavior have been manually verified.
