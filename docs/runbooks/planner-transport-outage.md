# Runbook: Planner Transport Outage

## Trigger

Use this runbook when the planner or task-graph transport starts returning
OpenAI connection failures, repeated rate limits, or
`PlannerTransportCircuitOpenError`.

## Immediate Actions

1. Pause the run and keep the bot in `L1 approve`.
2. Review `data/handoff/latest_run.json` for planner-request errors, transport
   cooldown windows, and whether task-graph decomposition fell back to a
   single-goal mission.
3. Confirm the configured `OPENAI_KEY` or `OPENAI_API_KEY` is present and not
   expired.
4. Check network reachability, proxy configuration, and current OpenAI service
   health before retrying the run.
5. Wait for the documented cooldown window when the transport circuit breaker
   has opened instead of forcing repeated retries.
6. If local visual memory is still finding safe decisions, allow only
   supervised continuation; otherwise stop the run until the transport recovers.

## Verification

- The next supervised retry should show successful `planner_request` timing
  entries instead of repeated transport failures.
- `task_graph_decompose` should recover from single-goal fallback after the
  transport is healthy again.
- The circuit breaker should reset automatically after the cooldown elapses and
  a successful request is observed.

## Escalation

Escalate when planner transport failures persist for more than one cooldown
window or reproduce across multiple workstations. Include the failing run
handoff, network/proxy details, and the full planner error messages.
