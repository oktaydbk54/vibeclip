#!/usr/bin/env bash
#
# VibeClip production deploy. RUN THIS ON THE SERVER, from the repo directory
# (e.g. /home/debian/vibeclip), inside your own SSH session:
#
#     bash scripts/prod_deploy.sh
#
# It idempotently sets the public-instance hardening flags in .env, pulls the
# latest main, and rebuilds the container. It contains NO secrets and never
# connects anywhere with a password — rotate API keys by editing .env yourself
# (`nano .env`) before/after running this.
set -euo pipefail

# Resolve repo root (this script lives in scripts/).
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f .env ]; then
  echo "✗ No .env in $(pwd) — run this from the deployed repo on the server." >&2
  exit 1
fi

# --- 1) Public-instance hardening flags (idempotent: update in place or append).
#        These are config, not secrets. See .env.example for what each does.
ensure_env() {
  local kv="$1" key="${1%%=*}"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${kv}|" .env
    echo "  updated ${key}"
  else
    printf '%s\n' "$kv" >> .env
    echo "  added   ${key}"
  fi
}
echo "→ Ensuring hardening flags in .env:"
ensure_env "EMAIL_MODE=resend"            # actually send OTP emails via Resend
ensure_env "SECURE_COOKIES=true"          # session cookie Secure (HTTPS-only)
ensure_env "SERVER_KEY_ADMINS_ONLY=true"  # non-admins must bring their own LLM key

# --- 2) Pull latest main + rebuild. (.env is gitignored, so reset never touches it.)
echo "→ Pulling origin/main…"
git fetch origin
git reset --hard origin/main

echo "→ Rebuilding container…"
docker compose up -d --build

echo "✅ Deploy complete — HEAD: $(git rev-parse --short HEAD)"
echo "   Reminder: rotate any exposed API keys in .env, then re-run this script."
