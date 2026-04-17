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

## Verification

- `OS_ROKBOT.start(...)` refuses live automation when the emergency stop cannot
  arm.
- A restarted session should write a fresh heartbeat to `data/heartbeat.json`.
- The first resumed mission should use `L1 approve`.

## Escalation

Treat any emergency-stop failure as a release blocker. Do not operate live
automation on that workstation until Interception, keyboard hooks, and process
termination behavior have been manually verified.
