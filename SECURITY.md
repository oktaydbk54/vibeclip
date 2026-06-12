# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Instead, email **oktaydabak54@gmail.com** with:

- a description of the issue and its impact,
- steps to reproduce (a proof-of-concept if you have one),
- any suggested fix.

You'll get an acknowledgement as soon as possible. Please give a reasonable window
to ship a fix before any public disclosure.

## Scope — things we especially care about

VibeClip handles **user-supplied API keys** (BYOK). Reports involving these are
high priority:

- Leakage of stored LLM keys (they are encrypted at rest with Fernet under a
  per-instance `.llm_secret`; keys are never returned to the client unmasked).
- Server-Side Request Forgery via the custom LLM `base_url` (a public instance can
  set `BLOCK_PRIVATE_LLM_ENDPOINTS=true` to reject private/loopback targets).
- Auth/session issues (signed-cookie sessions, the admin allowlist).
- Path traversal or arbitrary file read/write in the upload/render paths.

## Good operational hygiene for self-hosters

- Keep `.env`, `.auth_secret`, and `.llm_secret` private (all gitignored).
- Put a public instance behind HTTPS and set `REQUIRE_EMAIL_VERIFICATION=true`.
- Set `BLOCK_PRIVATE_LLM_ENDPOINTS=true` on multi-user/public instances.
- Rotate any key that may have been exposed.
