"""Social connect + share API (Phase 1 MVP).

Routes are user-scoped (cookie auth, like the rest of the JSON API). Connect /
accounts / shares are pure per-user SQLite (chat/social_db.py) + provider calls
(chat/social_providers.py). The publish flow captures the active Session at
submit time (like _submit_processing_job) so a queued/scheduled share is
self-contained despite the single global SESSION — it never reads a possibly-
swapped SESSION at publish time.

Publishing is on the USER's behalf: it only ever runs from the explicit
POST /api/social/share confirm (the studio's PUBLISH button). Nothing here
auto-posts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from chat import auth, social_db
from chat.jobs import MANAGER
from chat.social_providers import (PLATFORM_KINDS, ProviderError, get_provider,
                                    validate)

router = APIRouter()


# --------------------------------------------------------------------- status
@router.get("/api/social/status")
def social_status(_user: dict = Depends(auth.require_user)):
    """Whether sharing is configured + the platform/kind catalog for the UI."""
    prov = get_provider()
    return {"enabled": prov.enabled, "provider": prov.name,
            "platforms": list(PLATFORM_KINDS.keys()), "kinds": PLATFORM_KINDS}


# ------------------------------------------------------------------- accounts
@router.get("/api/social/accounts")
def social_accounts(_user: dict = Depends(auth.require_user)):
    prov = get_provider()
    if not prov.enabled:
        return {"enabled": False, "accounts": []}
    try:
        accounts = prov.sync_accounts(_user["id"])
    except ProviderError as e:
        # fall back to the cached list so the UI still renders
        return {"enabled": True, "accounts": social_db.list_accounts(_user["id"]),
                "warning": str(e)}
    return {"enabled": True, "accounts": _public_accounts(accounts)}


class ConnectIn(BaseModel):
    platform: str = ""


@router.post("/api/social/connect")
def social_connect(body: ConnectIn, _user: dict = Depends(auth.require_user)):
    prov = get_provider()
    try:
        url = prov.connect_url(_user["id"], body.platform.strip().lower())
    except ProviderError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"url": url}


@router.delete("/api/social/accounts/{account_id}")
def social_disconnect(account_id: int,
                      _user: dict = Depends(auth.require_user)):
    acct = social_db.get_account(_user["id"], account_id)
    if acct is None:
        return JSONResponse({"error": "No such account."}, status_code=404)
    try:
        get_provider().disconnect(acct)
    except ProviderError:
        pass  # local removal still proceeds
    social_db.delete_account(_user["id"], account_id)
    return {"ok": True}


# --------------------------------------------------------------------- share
class ShareIn(BaseModel):
    clip_id: int
    account_ids: list[int] = []
    kind: str = "post"
    caption: str = ""
    scheduled_at: str | None = None   # ISO 8601; None = post now


@router.post("/api/social/share")
def social_share(body: ShareIn, request: Request,
                 _user: dict = Depends(auth.require_user)):
    import chat.app as appmod

    prov = get_provider()
    if not prov.enabled:
        return JSONResponse(
            {"error": "Social sharing isn't configured. Add ZERNIO_API_KEY to "
                      ".env (free key at zernio.com), then restart."},
            status_code=400)
    if not body.account_ids:
        return JSONResponse({"error": "Pick at least one account."},
                            status_code=400)

    # Resolve the destination accounts (must belong to this user).
    accounts = [social_db.get_account(_user["id"], aid)
                for aid in body.account_ids]
    accounts = [a for a in accounts if a]
    if not accounts:
        return JSONResponse({"error": "No valid accounts selected."},
                            status_code=400)

    # Capture the active Session + project NOW (single-SESSION safety).
    with appmod.SESSION_LOCK:
        sess = appmod.SESSION
        if sess is None:
            return JSONResponse({"error": "No active project."},
                                status_code=409)
        project = sess.data.get("name", "")
        try:
            clip = sess.clip(body.clip_id)
        except ValueError:
            return JSONResponse({"error": "No such clip."}, status_code=404)
        # Validate against the proxy (same timing/aspect as the full-res export,
        # cheaper) so a bad destination fails BEFORE we export/upload anything.
        probe_src = clip.get("current") or clip.get("export")

    from pipeline.media import ffprobe_info
    media_info = ffprobe_info(probe_src) if probe_src else {}
    issues: dict[str, list[str]] = {}
    for a in accounts:
        iss = validate(media_info, a.get("platform", ""), body.kind)
        if iss:
            issues[a["display_name"] or a["platform"]] = iss
    if issues:
        return JSONResponse({"error": "Some destinations can't accept this clip.",
                             "issues": issues}, status_code=422)

    # Record one share row per destination (status reflects now vs scheduled).
    base_status = "scheduled" if body.scheduled_at else "publishing"
    share_ids: list[int] = []
    for a in accounts:
        sid = social_db.create_share(
            _user["id"], project, body.clip_id, a["id"], body.kind,
            body.caption, "", status=base_status,
            scheduled_at=body.scheduled_at)
        share_ids.append(sid)

    pairs = list(zip(share_ids, accounts))
    clip_id = body.clip_id
    caption, kind, scheduled_at = body.caption, body.kind, body.scheduled_at

    def _run(job):
        from pipeline import progress as pg
        # Ensure the full-res export exists on the captured session, then publish
        # each destination. Export is cache-keyed, so a re-share is a cache hit.
        with appmod.SESSION_LOCK:
            c = sess.clip(clip_id)
            media_path = c.get("export") or sess.export_clip(clip_id)
        results = []
        for sid, acct in pairs:
            social_db.update_share(sid, media_path=media_path)
            try:
                pg.note(f"publishing to {acct.get('platform')}…")
                out = prov.publish(acct, media_path, caption, kind, scheduled_at)
                social_db.update_share(
                    sid,
                    status=("scheduled" if scheduled_at else "published"),
                    external_post_id=out.get("external_id", ""),
                    post_url=out.get("url", ""), error="")
                results.append({"share_id": sid, "ok": True,
                                "url": out.get("url", "")})
            except Exception as e:  # noqa: BLE001 — surface per-destination
                msg = str(e)
                social_db.update_share(sid, status="failed", error=msg)
                results.append({"share_id": sid, "ok": False, "error": msg})
        return {"ok": any(r["ok"] for r in results), "results": results}

    job = MANAGER.submit("tool", "share", _run)
    return {"job_id": job.id, "share_ids": share_ids}


@router.get("/api/social/shares")
def social_shares(project: str | None = None, clip_id: int | None = None,
                  _user: dict = Depends(auth.require_user)):
    rows = social_db.list_shares(_user["id"], project, clip_id)
    # join in the account display for the UI
    accts = {a["id"]: a for a in social_db.list_accounts(_user["id"])}
    for r in rows:
        a = accts.get(r["account_id"])
        r["platform"] = a["platform"] if a else ""
        r["account_name"] = (a["display_name"] if a else "") or (
            a["platform"] if a else "")
    return {"shares": rows}


# --------------------------------------------------------------------- helpers
def _public_accounts(rows: list[dict]) -> list[dict]:
    """Strip secrets; expose only what the UI renders."""
    return [{"id": r["id"], "platform": r["platform"],
             "display_name": r["display_name"], "avatar_url": r["avatar_url"],
             "status": r["status"]} for r in rows]
