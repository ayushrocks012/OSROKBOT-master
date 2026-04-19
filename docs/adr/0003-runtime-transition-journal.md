# ADR 0003: Runtime Transition Journal And Resume Boundary

Status: Accepted

## Context

OSROKBOT can be interrupted by operator stop, process crashes, or the F12
emergency kill switch. Before this ADR, the runtime had per-run session events
and latest-run handoff files, but it did not have a cryptographically chained
record that distinguished:

- a logical workflow transition that was durably committed, and
- a low-level side effect that may have happened after the last safe resume
  boundary.

That gap made it hard to resume safely after abrupt termination, especially if
the last observed activity involved a pending approval or guarded hardware
input.

## Decision

Runtime sessions now own a per-run append-only journal plus an atomic
checkpoint:

- `data/session_logs/<run_id>.journal.ndjson` stores HMAC-chained runtime
  boundaries such as `step_started`, `decision_selected`,
  `approval_requested`, `approval_resolved`, `input_started`,
  `input_completed`, `transition_committed`, and `terminal`.
- `data/session_logs/<run_id>.checkpoint.json` stores the last committed
  logical transition, not the most recent raw input event.
- The HMAC key is stored through the configured secret-provider chain under
  `RUNTIME_JOURNAL_HMAC_KEY`.
- `data/handoff/latest_run.json` and `latest_run.txt` surface the journal
  checkpoint as the canonical resume boundary for operators and AI agents.

## Consequences

Positive:

- Interrupted runs can be resumed from a verified logical state boundary.
- F12 no longer depends on graceful teardown to preserve a safe resume point.
- Operators can distinguish committed progress from uncommitted tail events.

Tradeoffs:

- Resume semantics are intentionally conservative: the bot must re-observe the
  screen before any new input.
- The journal adds extra per-step file I/O for runtime sessions.

## Rules

- Only `transition_committed` advances the resume checkpoint.
- Approval waits, input start, and input completion do not advance the resume
  checkpoint on their own.
- After crashes or F12, operators must restart from `latest_run.*`, inspect the
  `Resume Boundary` section, and use `L1 approve` for the first resumed run.
