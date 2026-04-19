# Runbook: Secret Provisioning

## Trigger

Use this runbook when configuring API keys, rotating keys, moving to a new
workstation, or reviewing whether secrets are stored in the correct place.

## Immediate Actions

1. Store `OPENAI_KEY`, `OPENAI_API_KEY`, and any password-like value in `.env`
   or the configured secret provider, not in `config.json`.
2. Choose a provider:
   `SECRET_PROVIDER=dotenv` keeps the existing local `.env` path.
   `SECRET_PROVIDER=dpapi` stores supported secrets in
   `data/secrets/dpapi_secrets.json` with Windows user-bound DPAPI encryption.
3. Confirm `.env` is ignored by Git before adding real values.
4. Use the settings UI or `ConfigManager` for normal updates so sensitive keys
   migrate out of `config.json` and into the selected provider.
5. Run:

```powershell
python verify_integrity.py
```

## Verification

- `config.json` must not contain API keys, passwords, tokens, or secret
  assignment values.
- Logs must redact OpenAI-, Vault-, AWS-, bearer-token, and password-style
  secret assignments.
- When `SECRET_PROVIDER=dpapi`, supported secrets should be absent from `.env`
  after saving through the UI or `ConfigManager`, and the DPAPI store must not
  contain plaintext secret values.
- `.env` remains local workstation fallback configuration. The provider
  boundary is ready for future external backends such as Vault or AWS Secrets
  Manager, but this slice ships `.env` and Windows DPAPI.

## Escalation

If a secret was committed or shared in logs, revoke it at the provider, rotate
the value, remove the local artifact, and treat the incident as credential
exposure.
