# Runbook: YOLO Warmup And Download

## Trigger

Use this runbook when YOLO weights are unavailable, warmup fails, or detector
labels remain empty after a supervised startup.

## Immediate Actions

1. Pause the run or stay in the startup dialog until YOLO readiness is clear.
2. Confirm `ROK_YOLO_WEIGHTS` points to an existing `.pt` file or configure
   `ROK_YOLO_WEIGHTS_URL` with a trusted HTTPS download source.
3. Use the settings UI or startup health-check dialog to trigger the download
   only when the target path and URL are correct.
4. Watch the supervisor status for warmup completion before pressing Play.
5. If downloads fail, inspect local disk space, HTTPS reachability, and the
   configured `ROK_YOLO_MAX_BYTES` size cap.
6. If YOLO remains unavailable, continue only with supervised `L1 approve`
   missions that can tolerate OCR-only `Fix required` fallback behavior.

## Verification

- The configured local weights path should exist after warmup or download.
- The supervisor console should return to a ready state and stop showing the
  YOLO-unavailable startup tooltip.
- Subsequent runs should emit non-empty `yolo_detect` timings when the model
  can see supported labels on screen.

## Escalation

Escalate when a known-good weights file still fails to load or when repeated
downloads fail from a trusted HTTPS source. Include the configured path or URL,
the size-cap value, and any warmup error text from the console or logs.
