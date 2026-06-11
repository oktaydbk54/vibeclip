"""Email/password auth for KESİM Studio — stdlib only (no new deps).

A tiny SQLite-backed user store with a signed-cookie session (HMAC over the
cookie's own value, no server-side session table). All user-facing strings are
English; the profile prompt fed to the agent is English too (the agent reasons
in English). One fresh sqlite3 connection per call — simplest thread-safe approach
for the demo, and the app already runs DB work off a single worker thread.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from chat import emailer
from pipeline import config

DB_PATH = config.CACHE_DIR / "users.db"
SECRET_PATH = config.CACHE_DIR / ".auth_secret"
STATIC = Path(__file__).parent / "static"

COOKIE_NAME = "kesim_session"
COOKIE_MAX_AGE = 30 * 86400   # 30 days

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Allowed onboarding values (validated server-side; the UI sends these keys).
CONTENT_TYPES = {"podcast", "egitim", "oyun", "vlog", "marka"}
PLATFORMS = {"tiktok", "reels", "youtube_shorts", "multi"}
EXPERIENCES = {"yeni", "orta", "pro"}
VIBES = {"enerjik", "sinematik", "sade", "eglenceli"}
GOALS = {"viral", "topluluk", "satis", "zaman"}


# --------------------------------------------------------------------- secret
def _secret() -> bytes:
    """32-byte cookie-signing secret, persisted on first import (chmod 600)."""
    if SECRET_PATH.exists():
        return SECRET_PATH.read_bytes()
    s = secrets.token_bytes(32)
    SECRET_PATH.write_bytes(s)
    try:
        SECRET_PATH.chmod(0o600)
    except OSError:
        pass
    return s


_SECRET = _secret()


# ------------------------------------------------------------------------- db
def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT UNIQUE NOT NULL COLLATE NOCASE,
          display_name TEXT NOT NULL DEFAULT '',
          pw_salt BLOB NOT NULL,
          pw_hash BLOB NOT NULL,
          profile_json TEXT NOT NULL DEFAULT '{}',
          onboarded INTEGER NOT NULL DEFAULT 0,
          verified INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    return con


# ---------------------------------------------------------------- otp / verify
OTP_TTL = 600            # code valid for 10 minutes
OTP_MAX_ATTEMPTS = 5     # wrong-code tries before a new code is required
RESEND_COOLDOWN = 45     # seconds between code sends to one address


def _migrate() -> None:
    """One-time schema upgrade for pre-existing databases: add the `verified`
    column (grandfathering everyone who signed up before verification existed)
    and create the email_codes table. Safe to run on every boot."""
    with _connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
        if "verified" not in cols:
            con.execute(
                "ALTER TABLE users ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
            con.execute("UPDATE users SET verified = 1")  # grandfather existing
        con.execute("""
            CREATE TABLE IF NOT EXISTS email_codes (
              email     TEXT    NOT NULL COLLATE NOCASE,
              code_hash TEXT    NOT NULL,
              purpose   TEXT    NOT NULL DEFAULT 'verify',
              expires   INTEGER NOT NULL,
              attempts  INTEGER NOT NULL DEFAULT 0,
              sent_at   INTEGER NOT NULL,
              UNIQUE(email, purpose)
            )
        """)


def _hash_code(code: str) -> str:
    return hmac.new(_SECRET, code.strip().encode(), hashlib.sha256).hexdigest()


def _gen_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _issue_code(email: str, purpose: str = "verify") -> tuple[str | None, int]:
    """Mint + store a fresh code for (email, purpose). Returns (code, 0) on
    success, or (None, seconds_left) if still within the resend cooldown."""
    now = int(time.time())
    with _connect() as con:
        row = con.execute(
            "SELECT sent_at FROM email_codes WHERE email = ? AND purpose = ?",
            (email.strip(), purpose)).fetchone()
        if row and (now - int(row["sent_at"])) < RESEND_COOLDOWN:
            return None, RESEND_COOLDOWN - (now - int(row["sent_at"]))
        code = _gen_code()
        con.execute(
            "INSERT INTO email_codes (email, code_hash, purpose, expires, "
            "attempts, sent_at) VALUES (?, ?, ?, ?, 0, ?) "
            "ON CONFLICT(email, purpose) DO UPDATE SET "
            "code_hash = excluded.code_hash, expires = excluded.expires, "
            "attempts = 0, sent_at = excluded.sent_at",
            (email.strip(), _hash_code(code), purpose, now + OTP_TTL, now))
        return code, 0


def _check_code(email: str, code: str, purpose: str = "verify") -> tuple[bool, str]:
    """Validate a code. On success the code is consumed (deleted)."""
    now = int(time.time())
    email = email.strip()
    code = (code or "").strip()
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM email_codes WHERE email = ? AND purpose = ?",
            (email, purpose)).fetchone()
        if row is None:
            return False, "No active code. Request a new one."
        if now > int(row["expires"]):
            con.execute("DELETE FROM email_codes WHERE email = ? AND purpose = ?",
                        (email, purpose))
            return False, "Code expired. Request a new one."
        if int(row["attempts"]) >= OTP_MAX_ATTEMPTS:
            con.execute("DELETE FROM email_codes WHERE email = ? AND purpose = ?",
                        (email, purpose))
            return False, "Too many attempts. Request a new code."
        if not hmac.compare_digest(row["code_hash"], _hash_code(code)):
            con.execute(
                "UPDATE email_codes SET attempts = attempts + 1 "
                "WHERE email = ? AND purpose = ?", (email, purpose))
            left = OTP_MAX_ATTEMPTS - int(row["attempts"]) - 1
            tail = f" {left} tries left." if left > 0 else ""
            return False, f"Incorrect code.{tail}"
        con.execute("DELETE FROM email_codes WHERE email = ? AND purpose = ?",
                    (email, purpose))
        return True, ""


def _set_password(uid: int, password: str) -> None:
    salt = secrets.token_bytes(16)
    with _connect() as con:
        con.execute("UPDATE users SET pw_salt = ?, pw_hash = ? WHERE id = ?",
                    (salt, _hash_pw(password, salt), uid))


def _mark_verified(uid: int) -> None:
    with _connect() as con:
        con.execute("UPDATE users SET verified = 1 WHERE id = ?", (uid,))


_migrate()


def _hash_pw(pw: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)


def create_user(email: str, name: str, password: str) -> int:
    """Insert a user. Raises sqlite3.IntegrityError on a duplicate email."""
    salt = secrets.token_bytes(16)
    pw_hash = _hash_pw(password, salt)
    with _connect() as con:
        cur = con.execute(
            "INSERT INTO users (email, display_name, pw_salt, pw_hash) "
            "VALUES (?, ?, ?, ?)",
            (email.strip(), name.strip(), salt, pw_hash),
        )
        return int(cur.lastrowid)


def _row_by_email(email: str) -> dict | None:
    with _connect() as con:
        r = con.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE",
                        (email.strip(),)).fetchone()
        return dict(r) if r else None


def _row_by_id(uid: int) -> dict | None:
    with _connect() as con:
        r = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(r) if r else None


def verify_password(row: dict, password: str) -> bool:
    expect = row["pw_hash"]
    got = _hash_pw(password, row["pw_salt"])
    return hmac.compare_digest(expect, got)


# --------------------------------------------------------------------- cookie
def mint_cookie(user_id: int, ttl: int = COOKIE_MAX_AGE) -> str:
    expires = int(time.time()) + ttl
    payload = f"{user_id}.{expires}"
    sig = hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_cookie(value: str | None) -> int | None:
    """Return the user_id for a valid, unexpired cookie, else None."""
    if not value:
        return None
    try:
        uid_s, exp_s, sig = value.split(".")
        payload = f"{uid_s}.{exp_s}"
        expect = hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            return None
        if int(exp_s) < int(time.time()):
            return None
        return int(uid_s)
    except (ValueError, AttributeError):
        return None


def _set_cookie(resp: JSONResponse, user_id: int) -> None:
    resp.set_cookie(
        COOKIE_NAME, mint_cookie(user_id),
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", path="/",
    )


# -------------------------------------------------------------------- helpers
def get_user(request: Request) -> dict | None:
    """Parse + verify the session cookie and fetch the row. None on any
    failure/expiry (never raises) — safe to call from the chat handler."""
    try:
        uid = verify_cookie(request.cookies.get(COOKIE_NAME))
        if uid is None:
            return None
        return _row_by_id(uid)
    except Exception:
        return None


def require_user(request: Request) -> dict:
    """Like get_user but raises 401 — for JSON APIs that need a user."""
    user = get_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user


# ----------------------------------------------------------------- profile -> prompt
_CONTENT_FRAG = {
    "podcast": "podcast / long-form conversation clips",
    "egitim": "educational / how-to content",
    "oyun": "gaming / stream highlights",
    "vlog": "vlog & lifestyle content",
    "marka": "brand / product promo content",
}
_PLATFORM_FRAG = {
    "tiktok": "TikTok",
    "reels": "Instagram Reels",
    "youtube_shorts": "YouTube Shorts",
    "multi": "multiple platforms (TikTok + Reels + Shorts)",
}
_EXP_FRAG = {
    "yeni": "complete beginner — avoid ALL jargon, explain every suggestion "
            "in one plain sentence, be proactive",
    "orta": "some experience — keep explanations short, offer creative options",
    "pro": "professional editor — be terse and technical, no hand-holding",
}
_VIBE_FRAG = {
    "enerjik": "energetic & fast: punchy pacing, bold karaoke-style captions "
               "(hormozi/mrbeast direction), upbeat music",
    "sinematik": "cinematic & emotional: slower pacing, warm/cinematic looks, "
                 "subtle music, minimal captions",
    "sade": "clean & professional: minimal captions, no flashy effects, "
            "neutral music",
    "eglenceli": "fun & meme-flavored: stickers, sfx, reactions, playful "
                 "captions",
}
_GOAL_FRAG = {
    "viral": "maximize watch-time and viral reach (strong hooks, retention "
             "pacing)",
    "topluluk": "grow a loyal audience (consistent style, personality-forward "
                "edits)",
    "satis": "drive sales/conversions (clear messaging, brand-safe, "
             "CTA-friendly endings)",
    "zaman": "save time (prefer one solid proposal over many options, move "
             "fast)",
}
# Map a platform key to a PLATFORM_LOUDNESS key that actually exists.
_PLATFORM_LOUDNESS_KEY = {
    "tiktok": "tiktok",
    "reels": "instagram_reels",
    "youtube_shorts": "youtube_shorts",
    "multi": "tiktok",
}


def build_profile_prompt(profile: dict, name: str) -> str:
    """English profile block injected into the agent's system prompt. Returns
    "" if the profile is empty (unonboarded users get no personalization)."""
    if not profile:
        return ""
    content = _CONTENT_FRAG.get(profile.get("content_type", ""), "")
    platform = _PLATFORM_FRAG.get(profile.get("platform", ""), "")
    exp = _EXP_FRAG.get(profile.get("experience", ""), "")
    vibe = _VIBE_FRAG.get(profile.get("style_vibe", ""), "")
    goal = _GOAL_FRAG.get(profile.get("goal", ""), "")
    platform_key = _PLATFORM_LOUDNESS_KEY.get(profile.get("platform", ""),
                                              "tiktok")
    return (
        "USER PROFILE (tailor every suggestion and default to this user; "
        "persists across sessions):\n"
        f"- Name: {name} — address them by name occasionally.\n"
        f"- Makes: {content}. Publishes on: {platform}.\n"
        f"- Skill level: {exp}\n"
        f"- Preferred vibe: {vibe}\n"
        f"- Primary goal: {goal}\n"
        "Defaults that follow from this profile (apply unless the user "
        f"overrides): target platform for loudness/format = {platform_key}; "
        "caption & pacing style should match the vibe above; when proposing "
        "ideas with propose_edit, bias them toward the user's content type "
        "and goal.\n"
    )


# -------------------------------------------------------------------- schemas
class SignupIn(BaseModel):
    name: str = ""
    email: str = ""
    password: str = ""


class LoginIn(BaseModel):
    email: str = ""
    password: str = ""


class VerifyIn(BaseModel):
    email: str = ""
    code: str = ""


class ResendIn(BaseModel):
    email: str = ""


class OnboardingIn(BaseModel):
    content_type: str = ""
    platform: str = ""
    experience: str = ""
    style_vibe: str = ""
    goal: str = ""


# --------------------------------------------------------------------- router
router = APIRouter()


def _send_code(email: str, name: str = "") -> tuple[bool, str]:
    """Issue + email a verification code. Returns (ok, error_message)."""
    code, wait = _issue_code(email, "verify")
    if code is None:
        return False, (f"Please wait {wait}s before requesting another code.")
    if not emailer.send_otp(email, code, name):
        return False, "Could not send the verification email. Try again shortly."
    return True, ""


@router.post("/api/auth/signup")
def signup(body: SignupIn):
    name = (body.name or "").strip()
    email = (body.email or "").strip()
    pw = body.password or ""
    if not name:
        return JSONResponse({"error": "Name is required"}, status_code=400)
    if not EMAIL_RE.match(email):
        return JSONResponse({"error": "Enter a valid email"}, status_code=400)
    if len(pw) < 6:
        return JSONResponse({"error": "Password must be at least 6 characters"},
                            status_code=400)

    existing = _row_by_email(email)
    if existing is not None:
        if existing["verified"]:
            return JSONResponse({"error": "This email is already registered"},
                                status_code=409)
        # Account exists but was never verified — let them re-register: refresh
        # name + password and send a new code.
        with _connect() as con:
            con.execute("UPDATE users SET display_name = ? WHERE id = ?",
                        (name, existing["id"]))
        _set_password(int(existing["id"]), pw)
    else:
        create_user(email, name, pw)  # verified defaults to 0

    ok, err = _send_code(email, name)
    if not ok:
        return JSONResponse({"error": err}, status_code=429)
    # No cookie yet — the account is unverified until the code is confirmed.
    return JSONResponse({"ok": True, "next": "/verify", "email": email})


@router.post("/api/auth/verify")
def verify(body: VerifyIn):
    email = (body.email or "").strip()
    row = _row_by_email(email)
    if row is None:
        return JSONResponse({"error": "No account for this email. Sign up first."},
                            status_code=404)
    if row["verified"]:
        # Never mint a session here without a credential check — that would be an
        # auth bypass. Send already-verified users through normal login.
        return JSONResponse(
            {"error": "This email is already verified. Please sign in.",
             "next": "/login"}, status_code=409)
    ok, msg = _check_code(email, body.code or "", "verify")
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    _mark_verified(int(row["id"]))
    resp = JSONResponse({"ok": True,
                         "next": "/projects" if row["onboarded"] else "/onboarding"})
    _set_cookie(resp, int(row["id"]))
    return resp


@router.post("/api/auth/resend")
def resend(body: ResendIn):
    email = (body.email or "").strip()
    row = _row_by_email(email)
    # Don't leak which emails exist: respond ok even when there's nothing to send.
    if row is None or row["verified"]:
        return JSONResponse({"ok": True})
    ok, err = _send_code(email, row["display_name"])
    if not ok:
        return JSONResponse({"error": err}, status_code=429)
    return JSONResponse({"ok": True})


@router.post("/api/auth/login")
def login(body: LoginIn):
    row = _row_by_email(body.email or "")
    if row is None or not verify_password(row, body.password or ""):
        return JSONResponse({"error": "Email or password is incorrect"},
                            status_code=401)
    if not row["verified"]:
        # Block unverified login; send a fresh code and route to verification.
        _send_code(row["email"], row["display_name"])
        return JSONResponse(
            {"error": "Please verify your email first. We sent you a new code.",
             "next": "/verify", "email": row["email"], "unverified": True},
            status_code=403)
    nxt = "/projects" if row["onboarded"] else "/onboarding"
    resp = JSONResponse({"ok": True, "next": nxt})
    _set_cookie(resp, int(row["id"]))
    return resp


@router.post("/api/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/api/me")
def me(request: Request):
    user = require_user(request)
    profile = json.loads(user["profile_json"] or "{}")
    default_mode = "pro" if profile.get("experience") == "pro" else "basit"
    return {
        "id": user["id"],
        "name": user["display_name"],
        "email": user["email"],
        "onboarded": bool(user["onboarded"]),
        "profile": profile,
        "default_mode": default_mode,
    }


@router.post("/api/onboarding")
def onboarding(body: OnboardingIn, request: Request):
    user = require_user(request)
    checks = [
        (body.content_type, CONTENT_TYPES),
        (body.platform, PLATFORMS),
        (body.experience, EXPERIENCES),
        (body.style_vibe, VIBES),
        (body.goal, GOALS),
    ]
    for val, allowed in checks:
        if val not in allowed:
            return JSONResponse({"error": "Invalid choice"}, status_code=400)
    profile = {
        "content_type": body.content_type,
        "platform": body.platform,
        "experience": body.experience,
        "style_vibe": body.style_vibe,
        "goal": body.goal,
    }
    with _connect() as con:
        con.execute(
            "UPDATE users SET profile_json = ?, onboarded = 1 WHERE id = ?",
            (json.dumps(profile), user["id"]),
        )
    return {"ok": True, "next": "/projects"}


# ----------------------------------------------------------------- page routes
def _serve(name: str) -> HTMLResponse:
    return HTMLResponse((STATIC / name).read_text())


@router.get("/login", response_class=HTMLResponse)
@router.get("/signup", response_class=HTMLResponse)
@router.get("/verify", response_class=HTMLResponse)
def auth_page(request: Request):
    user = get_user(request)
    if user is not None:
        return RedirectResponse("/projects" if user["onboarded"]
                                else "/onboarding", 302)
    return _serve("auth.html")


# Legacy aliases — redirect the old Turkish routes to the canonical ones.
@router.get("/giris")
def giris_alias():
    return RedirectResponse("/login", 302)


@router.get("/kayit")
def kayit_alias():
    return RedirectResponse("/signup", 302)


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request):
    user = get_user(request)
    if user is None:
        return RedirectResponse("/login", 302)
    if user["onboarded"]:
        return RedirectResponse("/projects", 302)
    return _serve("onboarding.html")
