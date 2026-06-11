"""Social publishing providers behind one small interface.

Every social call in the app goes through SocialProvider so the vendor is a
one-file swap (MVP = Zernio, an aggregator that hosts the OAuth connect screen
on its own domain — so the local 127.0.0.1 studio needs no public redirect URI).
A future direct Meta/LinkedIn provider, or a Unipile LinkedIn provider, just
implements the same methods.

Platform-token handling: with the aggregator model NO platform tokens live on
this machine — only Zernio's non-secret account ids. The single ZERNIO_API_KEY
is read from the gitignored .env (never committed), exactly like OPENAI_API_KEY.
"""

from __future__ import annotations

import httpx

from pipeline import config

# ---------------------------------------------------------------- platform specs
# Per-platform / per-kind publish constraints, used by validate() to fail a
# share BEFORE it leaves the machine with a readable reason. Kept conservative;
# tighten per provider feedback. (sec = max video length; ar = "vertical" hint.)
PLATFORM_SPECS: dict[str, dict] = {
    "instagram": {"post": {"max_sec": 90, "vertical": True},
                  "reel": {"max_sec": 90, "vertical": True},
                  "story": {"max_sec": 60, "vertical": True}},
    "facebook":  {"post": {"max_sec": 1200}, "reel": {"max_sec": 90,
                  "vertical": True}, "story": {"max_sec": 60, "vertical": True}},
    "linkedin":  {"post": {"max_sec": 900}},
    "tiktok":    {"post": {"max_sec": 600, "vertical": True}},
    "youtube":   {"post": {"max_sec": 900},
                  "story": {"max_sec": 180, "vertical": True}},
    "threads":   {"post": {"max_sec": 300}},
    "pinterest": {"post": {"max_sec": 900, "vertical": True}},
    "bluesky":   {"post": {"max_sec": 60}},
}

# Which post kinds each platform offers (drives the UI kind selector).
# (X/Twitter is intentionally absent — the Zernio account can't connect it.)
PLATFORM_KINDS: dict[str, list[str]] = {
    "instagram": ["reel", "story", "post"],
    "facebook": ["post", "reel", "story"],
    "linkedin": ["post"],
    "tiktok": ["post"],
    "youtube": ["post", "story"],
    "threads": ["post"],
    "pinterest": ["post"],
    "bluesky": ["post"],
}

CONNECTABLE_PLATFORMS = list(PLATFORM_KINDS.keys())


class ProviderError(Exception):
    """A provider call failed — message is safe to surface to the user."""


class SocialProvider:
    """Interface every provider implements."""

    name = "base"

    @property
    def enabled(self) -> bool:
        raise NotImplementedError

    def connect_url(self, user_id: int, platform: str) -> str:
        raise NotImplementedError

    def sync_accounts(self, user_id: int) -> list[dict]:
        """Pull the provider's current accounts and reconcile the local cache.
        Returns the local rows after sync."""
        raise NotImplementedError

    def disconnect(self, account: dict) -> None:
        raise NotImplementedError

    def publish(self, account: dict, media_path: str, caption: str,
                kind: str, scheduled_at: str | None = None) -> dict:
        """Publish media to one account. Returns {external_id, url}."""
        raise NotImplementedError


def validate(media_info: dict, platform: str, kind: str) -> list[str]:
    """Return a list of human-readable issues ([] = OK) for a destination.

    media_info: {duration: sec, width, height} (from ffprobe_info)."""
    issues: list[str] = []
    spec = PLATFORM_SPECS.get(platform, {}).get(kind)
    if spec is None:
        issues.append(f"{platform} does not support '{kind}'.")
        return issues
    dur = float(media_info.get("duration") or 0)
    if spec.get("max_sec") and dur > spec["max_sec"] + 0.5:
        issues.append(
            f"{platform} {kind} max length is {spec['max_sec']}s — this clip "
            f"is {dur:.0f}s. Trim it first.")
    if spec.get("vertical"):
        w, h = media_info.get("width") or 0, media_info.get("height") or 0
        if w and h and w > h:
            issues.append(
                f"{platform} {kind} expects a vertical (9:16) video; this clip "
                f"is {w}×{h} (landscape).")
    return issues


# ===================================================================== Zernio
# Documented REST shapes (docs.zernio.com): base https://zernio.com/api/v1,
#   GET  /connect/{platform}?profileId=  -> {authUrl}
#   GET  /accounts                       -> {accounts:[{_id, platform, ...}]}
#   POST /posts {content, platforms:[{platform,accountId}], publishNow|scheduledFor}
#   POST /profiles {name}                -> {profile:{_id}}
# Media-upload shape (VERIFIED live 2026-06-11 against a real key):
#   POST /media  multipart field "files"  ->  {"files":[{"type","url",...}]}
#   /posts references it as  mediaItems:[{"type":"video","url":<that url>}]
_MEDIA_UPLOAD_PATH = "/media"
_MEDIA_FILE_FIELD = "files"


class ZernioProvider(SocialProvider):
    name = "zernio"

    def __init__(self) -> None:
        self.key = config.ZERNIO_API_KEY
        self.base = config.ZERNIO_BASE_URL.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    # ----------------------------------------------------------- http plumbing
    def _client(self) -> httpx.Client:
        if not self.enabled:
            raise ProviderError(
                "Social sharing isn't configured. Add ZERNIO_API_KEY to .env "
                "(free key at zernio.com), then restart the studio.")
        return httpx.Client(
            base_url=self.base, timeout=120.0,
            headers={"Authorization": f"Bearer {self.key}"})

    @staticmethod
    def _raise_for(r: httpx.Response, what: str) -> None:
        if r.status_code >= 400:
            detail = ""
            try:
                detail = (r.json().get("error") or r.json().get("message")
                          or "")[:200]
            except Exception:
                detail = r.text[:200]
            raise ProviderError(f"{what} failed ({r.status_code}). {detail}")

    # ----------------------------------------------------------- profile (group)
    def _ensure_profile(self, user_id: int) -> str:
        from chat import social_db
        pid = social_db.get_profile_id(user_id, self.name)
        if pid:
            return pid
        with self._client() as c:
            # Reuse an existing Zernio profile (Default first): the whole app
            # shares one ZERNIO_API_KEY = one Zernio account, so an already-
            # connected account is surfaced immediately and the free-tier
            # account quota isn't wasted on a duplicate connection. Only create
            # a profile if the account has none at all.
            r = c.get("/profiles")
            profiles = r.json().get("profiles") or [] if r.status_code < 300 \
                else []
            chosen = next((p for p in profiles if p.get("isDefault")),
                          profiles[0] if profiles else None)
            if chosen:
                pid = chosen.get("_id")
            else:
                cr = c.post("/profiles",
                            json={"name": f"shorts-mcp-user-{user_id}"})
                self._raise_for(cr, "Creating a Zernio profile")
                pid = (cr.json().get("profile") or {}).get("_id")
        if not pid:
            raise ProviderError("Zernio did not return a profile id.")
        social_db.set_profile_id(user_id, pid, self.name)
        return pid

    # ------------------------------------------------------------------ connect
    def connect_url(self, user_id: int, platform: str) -> str:
        if platform not in CONNECTABLE_PLATFORMS:
            raise ProviderError(f"Unsupported platform '{platform}'.")
        pid = self._ensure_profile(user_id)
        with self._client() as c:
            r = c.get(f"/connect/{platform}", params={"profileId": pid})
            self._raise_for(r, f"Connecting {platform}")
            url = r.json().get("authUrl") or r.json().get("url")
        if not url:
            raise ProviderError("Zernio did not return a connect URL.")
        return url

    # ------------------------------------------------------------------ accounts
    def sync_accounts(self, user_id: int) -> list[dict]:
        from chat import social_db
        # Resolve (reuse Default / create) the profile so opening the share
        # modal immediately surfaces an already-connected account.
        pid = self._ensure_profile(user_id)
        with self._client() as c:
            r = c.get("/accounts", params={"profileId": pid})
            self._raise_for(r, "Listing accounts")
            accounts = r.json().get("accounts") or []
        keep: set[str] = set()
        for a in accounts:
            ext = a.get("_id") or a.get("id")
            if not ext:
                continue
            keep.add(str(ext))
            social_db.upsert_account(
                user_id, self.name, a.get("platform", ""), str(ext),
                display_name=(a.get("displayName") or a.get("username")
                              or a.get("name") or ""),
                avatar_url=(a.get("profilePicture") or a.get("avatar")
                            or a.get("picture") or a.get("profileImageUrl")
                            or ""),
                meta=a)
        social_db.prune_missing(user_id, self.name, keep)
        return social_db.list_accounts(user_id)

    def disconnect(self, account: dict) -> None:
        # Best-effort provider-side revoke; the local row is removed by the
        # caller regardless (so a provider without a delete endpoint still works).
        ext = account.get("external_id")
        if not ext:
            return
        try:
            with self._client() as c:
                c.delete(f"/accounts/{ext}")
        except Exception:
            pass

    # ------------------------------------------------------------------- publish
    def _upload_media(self, media_path: str) -> str:
        """Upload the exported MP4, return the hosted media URL for /posts."""
        with self._client() as c:
            with open(media_path, "rb") as fh:
                r = c.post(_MEDIA_UPLOAD_PATH,
                           files={_MEDIA_FILE_FIELD: (
                               "short.mp4", fh, "video/mp4")})
            self._raise_for(r, "Uploading media")
            data = r.json()
        files = data.get("files") or []
        ref = files[0].get("url") if files else None
        if not ref:
            raise ProviderError(
                "Media uploaded but Zernio returned no file url "
                f"(keys: {list(data)[:6]}).")
        return ref

    def publish(self, account: dict, media_path: str, caption: str,
                kind: str, scheduled_at: str | None = None) -> dict:
        from pipeline import progress as pg
        pg.note("uploading to social provider…")
        media_ref = self._upload_media(media_path)
        # Verified /posts shape: content + mediaItems[{type,url}] + platforms[
        # {platform,accountId}] + publishNow|scheduledFor. (Per-platform post
        # type — reel vs story — is a Phase-2 option; an IG video posts as a
        # Reel by default, which is the Phase-1 target.)
        body: dict = {
            "content": caption or "",
            "mediaItems": [{"type": "video", "url": media_ref}],
            "platforms": [{"platform": account.get("platform"),
                           "accountId": account.get("external_id")}],
        }
        if scheduled_at:
            body["scheduledFor"] = scheduled_at
        else:
            body["publishNow"] = True
        pg.note("creating post…")
        with self._client() as c:
            r = c.post("/posts", json=body)
            self._raise_for(r, "Creating the post")
            data = r.json()
        post = data.get("post") or data
        return {"external_id": str(post.get("_id") or post.get("id") or ""),
                "url": post.get("url") or post.get("permalink") or ""}


def get_provider() -> SocialProvider:
    """The configured provider (only Zernio today)."""
    return ZernioProvider()
