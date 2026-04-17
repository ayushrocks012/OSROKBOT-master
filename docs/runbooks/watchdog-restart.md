# Runbook: Watchdog Restart

## Trigger

Use this runbook when `watchdog.py` reports a stale heartbeat, a missing game
window, or an inability to relaunch the game client.

## Immediate Actions

1. Confirm `data/heartbeat.json` exists and has a recent `timestamp_epoch`.
2. Run one check from the project root:

```powershell
python watchdog.py --once
```

3. If the watchdog reports `ROK_CLIENT_PATH is not configured`, decide whether
   game relaunch should be enabled for this workstation.
4. If relaunch is required, set `ROK_CLIENT_PATH` in `.env` to the exact game
   executable path and restart the watchdog.
5. Keep `WATCHDOG_RESTART_ENABLED=0` when observing or testing restart logic
   without allowing process termination or relaunch.

## Verification

- The watchdog must only terminate PIDs recorded in the heartbeat.
- The UI restart path must come from the heartbeat `python_executable` and
  `ui_entrypoint` fields.
- The game relaunch path must be an accessible file from `ROK_CLIENT_PATH`.
- A fresh heartbeat should make `python watchdog.py --once` exit successfully.

## Escalation

Stop the run and inspect `data/session_logs/` if restarts repeat more than once
in a session. Repeated restarts usually indicate a foreground/capture issue,
an unhandled exception in the runner, or a machine sleep/power-management
problem.
