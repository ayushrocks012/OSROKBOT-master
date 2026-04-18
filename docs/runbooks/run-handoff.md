# Runbook: Run Handoff

## Trigger

Use this runbook when an operator or AI agent needs to understand the latest
runtime session or maintainer command without extra verbal handoff.

## Immediate Actions

1. Open `data/handoff/latest_run.txt` for the fixed-section summary.
2. Open `data/handoff/latest_run.json` for the structured fields:
   `status`, `end_reason`, `counts`, `top_errors`, `key_events`, `artifacts`,
   and `next_actions`.
3. Follow the paths in `artifacts` to the matching per-run history group under
   `data/session_logs/`.
4. Use `.err` first for failures, `.log` for transcript context, and runtime
   `.ndjson` when event ordering matters.
5. Only fall back to `data/logs/osrokbot.log` after the per-run artifacts.

## Verification

- `latest_run.json` should point to the same run stem across `.json`, `.txt`,
  `.log`, `.err`, and runtime `.ndjson`.
- `status` should be one of `success`, `failed`, `interrupted`, or `partial`.
- Runtime sessions should expose mission/autonomy fields and maintainer runs
  should expose command/exit-code details.
- If the previous runtime crashed before finalization, the next startup should
  mark it `interrupted` with `end_reason=previous_run_incomplete`.

## Escalation

Escalate when `data/handoff/latest_run.json` is missing, points to unreadable
artifact paths, or disagrees with the matching per-run files. Include the
broken handoff files and the referenced artifact paths in the report.
