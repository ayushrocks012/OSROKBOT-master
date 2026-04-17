# Runbook: Secret Provisioning

## Trigger

Use this runbook when configuring API keys, rotating keys, moving to a new
workstation, or reviewing whether secrets are stored in the correct place.

## Immediate Actions

1. Store `OPENAI_KEY`, `OPENAI_API_KEY`, and any password-like value in `.env`
   or the process environment, not in `config.json`.
2. Confirm `.env` is ignored by Git before adding real values.
3. Use the settings UI or `ConfigManager` for normal updates so sensitive keys
   migrate out of `config.json`.
4. Run:

```powershell
python verify_integrity.py
```

## Verification

- `config.json` must not contain API keys, passwords, tokens, or secret
  assignment values.
- Logs must redact OpenAI-style keys and known secret assignments.
- `.env` is local workstation configuration. It is not an enterprise vault,
  DPAPI-backed credential store, or centralized rotation/audit system.

## Escalation

If a secret was committed or shared in logs, revoke it at the provider, rotate
the value, remove the local artifact, and treat the incident as credential
exposure.
