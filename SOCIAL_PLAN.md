# shorts-mcp — Social Connect + Share — Development Plan
_(Planned with Fable 5; implementation with Opus 4.8 after approval. 2026-06-11.)_

## Part 1 — Research Summary

### 1.1 Zernio (the reference product) — key finding
Zernio is not a consumer scheduler — it is a **developer-first social posting API** ("15 platforms, one call"). Pivotal: the reference product is *itself the candidate vendor*, purpose-built for exactly this use case (an app whose end users connect accounts and publish through it).

- **Platforms (15):** Instagram, TikTok, YouTube, X, LinkedIn, Facebook, Threads, Pinterest, Reddit, Bluesky, WhatsApp, Telegram, Discord, Snapchat, Google Business.
- **Content types:** Instagram Feed / Stories / Reels / Carousels; Facebook video + Reels + Stories; TikTok video; YouTube video + Shorts; LinkedIn text/image/video; X video. Auto-validation + resizing of media.
- **Connect UX to emulate:** backend calls a connect endpoint with `platform` + profile id → API returns a **hosted OAuth URL** → redirect user there → user authorizes on the platform's native screen → account linked. The platform OAuth redirect URI lives on *Zernio's* domain — **your app never needs a public redirect URI** → solves the 127.0.0.1:8765 local-vs-deployed problem outright.
- **Posting:** one REST call with `accountId`, media, caption, optional `scheduledFor` → instant or scheduled (scheduling runs provider-side, so posts go out even if the local studio is closed).
- **Pricing:** free tier = first 2 connected accounts, unlimited posts, full API; then ~$6/account/mo. Bearer-token auth (one `ZERNIO_API_KEY`).
- Bonus: an MCP server (could later let the Director AI chat agent stage shares).

### 1.2 Integration options compared

| Option | IG Reels | IG Stories | LinkedIn video | OAuth handled | Cost | Time to ship | Notes |
|---|---|---|---|---|---|---|---|
| **Zernio** | Yes | Yes | Yes | Yes (hosted URL) | Free ≤2 acct, ~$6/acct/mo | **Days** | Reference product itself; media upload API; provider-side scheduling; MCP server |
| Ayrshare | Yes | Yes | Yes | Yes | Multi-user = **$499–599/mo** | Days | Most mature; multi-user gated behind priciest tier |
| upload-post / getlate / bundle.social | Yes (varies) | Partial | Yes | Yes | low–mid | Days | Budget alternates; less proven stories; evaluate if Zernio fails trial |
| Postiz (self-host OSS) | Yes | Yes | Yes | **No** (own Meta/LinkedIn apps) | server only | **Weeks–months** (app reviews) | Long-term escape from per-acct fees; not MVP |
| Mixpost (self-host) | Yes | Yes | Yes | No | one-time | Weeks–months | Same caveat as Postiz |
| Direct Meta Graph API | Yes (≤90s, 9:16) | Yes | — | build it | Free | **4–8 wks** | IG Business/Creator (+FB Page legacy), app review 2–4 wks, 25 posts/24h/acct |
| Direct LinkedIn API | — | — | Yes | build it | Free | **3–6 wks** | Community Mgmt review w/ screencast; versioned headers; token refresh |
| Unipile (existing creds) | **No IG** | No | **Yes** (video attach verified) | Yes (hosted) | known | Days (LinkedIn only) | Memory "read+DM only" is OUTDATED for posts: `genvision-social-media/backend/unipile.py` already does `create_post(...attachments)` + `create_hosted_auth_link`. No IG → insufficient alone |

### 1.3 Recommendation
**MVP: aggregator-first, vendor = Zernio.** Because it removes the two schedule-killers — platform app reviews (Meta/LinkedIn, weeks) and OAuth redirect-URI hosting (studio is on 127.0.0.1; hosted connect URL works locally + deployed). Only sub-$50/mo option with verified IG Reels+Stories + LinkedIn video + hosted multi-user connect (Ayrshare matches but $499+/mo multi-user). Free tier (2 accounts) = ship + dogfood at $0. Accepts direct media upload → no public URL for `outputs/` needed.

**Hedge:** all provider calls go through a one-file `SocialProvider` abstraction. Phase 5 documents direct-API migration + a Unipile LinkedIn fast-path (reuse genvision client) to engineer away per-account fees later. If direct APIs are ever wanted, file Meta+LinkedIn dev-app reviews at the START of Phase 1 so the review clock runs in parallel.

## Part 2 — Codebase Grounding (verified)
- **Server:** `chat/app.py` — FastAPI, single global `SESSION`+`SESSION_LOCK`, single-worker `MANAGER` (`chat/jobs.py`, SSE on `/api/events`), auth from `chat/auth.py`. `uvicorn 127.0.0.1:8765`.
- **Auth:** `chat/auth.py` — SQLite `cache/users.db` (`users`), HMAC cookie, `require_user`, stdlib-only. `cache/`, `.env`, `outputs/` gitignored (verified).
- **Export:** `chat/session.py` `export_clip()` replays approved chain on full-res → additive `clip["export"]` key, cache-keyed. Served `GET /media/export/{clip_id}`. Triggered via whitelisted `POST /api/tool {name:"export_clip"}` (`app.js exportClip()`).
- **UI surface:** deliver cluster in `index.html` (#ccExport: EXPORT MP4, full-res, ▾ popover). SHARE button mounts here. Vanilla JS, `?v=mtime` cache-bust, no build.
- **Per-user data:** users in SQLite; projects global under `outputs/sessions/<name>/project.json`. Connected accounts + shares → **SQLite keyed on user_id**, NOT project.json (keeps data contract additive by construction).
- **Existing QC:** `/api/qc/{clip_id}` (aspect/duration/loudness) — extend with per-destination share validation.
- **Reusable:** `genvision-social-media/backend/unipile.py` (async httpx: `create_post` w/ attachments + `as_organization`, `create_hosted_auth_link`) — portable for the optional Unipile LinkedIn provider. Creds in `~/Desktop/DBK solutions/.env`.
- **Caveat:** global single SESSION → a scheduled/queued share must capture export path + caption + account ids at confirm time, never read live SESSION at publish time.

## Part 3 — Phased Plan

### Phase 0 — Groundwork (~½ day)
- `chat/social_db.py` (new) — SQLite tables in existing `cache/users.db`:
  - `connected_accounts(id, user_id, provider, platform, external_id, display_name, avatar_url, status, secret_enc NULL, meta_json, connected_at, UNIQUE(user_id,provider,external_id))`
  - `shares(id, user_id, project, clip_id, account_id, kind /*post|reel|story*/, caption, media_path, status /*draft|scheduled|publishing|published|failed|canceled*/, scheduled_at NULL, external_post_id, post_url, error, created_at)`
- `chat/social_providers.py` (new) — `SocialProvider` interface: `connect_url(user,platform)`, `list_accounts(user)`, `disconnect(account)`, `publish(account,media_path,caption,kind,scheduled_at=None)→{external_id,url}`, `validate(media_info,platform,kind)→[issues]`.
- `pipeline/config.py` — `ZERNIO_API_KEY = os.getenv("ZERNIO_API_KEY","")` (gitignored `.env`, same rule as OPENAI_API_KEY).
- Security baseline: aggregator model → platform tokens NEVER touch this machine, only Zernio account ids stored (non-secret). `secret_enc` column exists from day 1 for the Phase-5 direct-API future (Fernet, key at `cache/.token_secret` chmod 600, mirror `auth.py:_secret()`). Per-platform spec table (IG Reel ≤90s/9:16/H.264; IG Story ≤60s/card; LinkedIn ≤15min; TikTok ≤10min; YT Shorts ≤3min vertical; X ≤2:20).

### Phase 1 — MVP: connect + share exported short as a post (2–4 days)
Scope: Instagram (Reel-as-post) + LinkedIn (video post), post-now only, explicit confirm.
- `chat/social.py` (new APIRouter, included like auth.router, all behind `require_user`):
  - `GET /api/social/accounts` — user rows + lazy provider sync.
  - `POST /api/social/connect {platform}` — provider `connect_url()` → `{url}`; UI opens in new tab. user_id ↔ one Zernio profile.
  - `DELETE /api/social/accounts/{id}` — disconnect.
  - `POST /api/social/share {clip_id, account_ids[], kind, caption}` — confirm endpoint: (1) ensure `clip["export"]`, else chain an `export_clip` job (reuse `MANAGER.submit`, capture Session); (2) `validate()` per destination (duration/aspect via ffprobe), hard-fail readable; (3) insert `shares` (publishing), submit publish job → upload MP4 bytes to Zernio → create post per account → write back `external_post_id`/`post_url`/`status`; narrate over SSE.
  - `GET /api/social/shares?project=&clip_id=` — history/status.
- Studio UI: `↗ SHARE` button in deliver cluster + `<dialog>` modal (`share.js` + ~120 lines CSS). Modal: left 9:16 preview; right connected-account chips w/ avatars + "+ Connect Instagram/LinkedIn" (open connect_url; on `focus` return re-poll accounts); caption textarea w/ per-platform counters; kind selector (Phase 1: Post); single explicit `PUBLISH TO {n} ACCOUNT(S)` button w/ "posts publicly as you" notice. Nothing publishes without this click. Status → PUBLISHING… → per-dest ✓/✗ + post_url; failures show provider error verbatim.
- Onboarding tie-in: `users.profile_json.platform` preselects the matching chip.
- Risk: IG requires the user's account to be Business/Creator — explain in error path.

### Phase 2 — Stories + Facebook + share-aware QC (1–2 days)
Kind selector → Post/Reel/Story per destination (LinkedIn post-only greyed). Story ≤60s/card; if longer, offer one-off ffmpeg `-t 60` derivative (NOT a stage-stack mutation) or block. Extend `/api/qc` with a `share` section. Shares-history entry in ▾ popover. Optional additive `clip["shared"]=true` marker for a clipstrip badge.

### Phase 3 — Scheduling + multi-account fan-out (1–2 days)
Post-now/Schedule toggle + datetime-local → `scheduledFor` (provider-side, fires even if studio closed). `POST /api/social/shares/{id}/cancel`. Fan-out: multi-select across platforms, one row per dest, per-destination caption overrides. "Scheduled" tab; reconcile on open (no local cron).

### Phase 4 — More platforms + caption/hashtag assist (2–3 days)
Enable TikTok, YT Shorts, X, Facebook chips (per-platform fields). AI caption assist ("✨ Suggest caption") reusing existing LLM config + transcript + user profile → 2–3 variants; pure suggestion, user confirms. Chat-agent `propose_share` tool that only OPENS the prefilled modal (never publishes — in-UI confirm stays the single gate).

### Phase 5 — Optional: direct-API migration / cost reduction (only if account volume makes ~$6/acct/mo matter)
Start review clocks early: Meta 2–4 wks, LinkedIn Standard-tier. Add `MetaProvider`/`LinkedInProvider` behind same interface; tokens in `connected_accounts.secret_enc` (Fernet, chmod 600), refresh in provider. Real redirect URI (`127.0.0.1:8765/auth/social/callback` locally; HTTPS deployed; `OAUTH_REDIRECT_BASE` in `.env`; HMAC `state`). Local 60s scheduler thread for direct providers. Cheap LinkedIn-only path anytime: `UnipileLinkedInProvider` porting `genvision-social-media/backend/unipile.py`.

## Part 4 — Risks & open items
1. IG posting requires a Professional (Business/Creator) account regardless of vendor — surface in connect-error UX from Phase 1.
2. Vendor verification before coding: Day 1 of Phase 1 = 1-hour smoke test on Zernio free tier (connect real IG + LinkedIn, publish one Reel + one LinkedIn video via curl). If underdelivers → upload-post/getlate trial, then Ayrshare-if-budget. Provider abstraction = one-file swap.
3. Length collision: pipeline exports >90s but IG Reels API caps 90s — Phase 1 validator must catch pre-confirm; clip-gen could prefer ≤90s when profile platform = reels.
4. Single-SESSION races: capture export path + share params at confirm time (pattern from `_submit_processing_job`).
5. Secrets hygiene: `.env`+`cache/` gitignored (verified); add no new secret files outside them. Never log captions+tokens together; provider errors → `shares.error` only.
6. App-review lead time (direct path): weeks w/ screencasts. Aggregator MVP exists so this never blocks shipping.

Sources: zernio.com · docs.zernio.com/platforms · zernio.com/social-media-api · ayrshare.com/pricing · Meta IG content-publishing docs · LinkedIn Videos/Posts API · Postiz/Mixpost docs · Unipile changelog.
