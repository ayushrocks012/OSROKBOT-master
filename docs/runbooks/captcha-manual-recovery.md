# Runbook: CAPTCHA Manual Recovery

## Trigger

Use this runbook when the bot pauses with `Captcha detected - paused` or a
session log contains a `captcha` event.

## Immediate Actions

1. Do not attempt to automate the CAPTCHA.
2. Confirm the bot is paused and no pointer action is pending.
3. Manually solve or dismiss the CAPTCHA in the game client.
4. Inspect the game screen for unsafe queued actions, popups, or changed state.
5. Resume only after the CAPTCHA is fully gone and the game window is
   foreground.

## Verification

- `data/session_logs/` should include a `captcha` event for the run.
- `diagnostics/` should include the captured CAPTCHA screenshot when available.
- The next run step should start from `L1 approve` unless the operator
  intentionally selects a higher autonomy level.

## Escalation

If CAPTCHA prompts recur frequently, stop automation for that account/session.
Do not add CAPTCHA solving, bypassing, prompt engineering, or OCR-specific
logic intended to defeat the challenge.
