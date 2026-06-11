"""Social connect + share persistence — stdlib SQLite, no new deps.

Lives in the SAME cache/users.db as auth.py (one DB file for the demo) and
mirrors its one-connection-per-call pattern. Two tables:

  connected_accounts — a user's linked social accounts (provider account ids;
    with the aggregator model NO platform tokens are stored here, only the
    provider's non-secret account id; secret_enc stays NULL until a future
    direct-API provider needs it).
  shares             — one row per (clip -> destination account) publish action,
    capturing everything needed to publish WITHOUT reading the live global
    SESSION at publish time (export path, caption, account) — the single-SESSION
    design means a queued/scheduled share must be self-contained.

All user-facing strings are English (product convention); this module has none.
"""

from __future__ import annotations

import json
import sqlite3

from pipeline import config

DB_PATH = config.CACHE_DIR / "users.db"


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS connected_accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          provider TEXT NOT NULL DEFAULT 'zernio',
          platform TEXT NOT NULL,
          external_id TEXT NOT NULL,
          display_name TEXT NOT NULL DEFAULT '',
          avatar_url TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active',
          secret_enc BLOB,
          meta_json TEXT NOT NULL DEFAULT '{}',
          connected_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(user_id, provider, external_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS shares (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          project TEXT NOT NULL DEFAULT '',
          clip_id INTEGER NOT NULL,
          account_id INTEGER NOT NULL,
          kind TEXT NOT NULL DEFAULT 'post',
          caption TEXT NOT NULL DEFAULT '',
          media_path TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'draft',
          scheduled_at TEXT,
          external_post_id TEXT NOT NULL DEFAULT '',
          post_url TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    return con


# ----------------------------------------------------- per-user provider profile
# Each shorts-mcp user maps to ONE provider "profile" (Zernio groups a user's
# accounts under a profile id). We stash that id in a tiny key/value row reusing
# meta_json on a sentinel account, OR a dedicated table — a dedicated table is
# cleaner:
def _ensure_profile_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS provider_profiles (
          user_id INTEGER NOT NULL,
          provider TEXT NOT NULL,
          profile_id TEXT NOT NULL,
          PRIMARY KEY (user_id, provider)
        )
    """)


def get_profile_id(user_id: int, provider: str = "zernio") -> str | None:
    with _connect() as con:
        _ensure_profile_table(con)
        r = con.execute(
            "SELECT profile_id FROM provider_profiles "
            "WHERE user_id = ? AND provider = ?", (user_id, provider)).fetchone()
        return r["profile_id"] if r else None


def set_profile_id(user_id: int, profile_id: str,
                   provider: str = "zernio") -> None:
    with _connect() as con:
        _ensure_profile_table(con)
        con.execute(
            "INSERT INTO provider_profiles (user_id, provider, profile_id) "
            "VALUES (?, ?, ?) ON CONFLICT(user_id, provider) "
            "DO UPDATE SET profile_id = excluded.profile_id",
            (user_id, provider, profile_id))


# ----------------------------------------------------------- connected accounts
def upsert_account(user_id: int, provider: str, platform: str, external_id: str,
                   display_name: str = "", avatar_url: str = "",
                   meta: dict | None = None) -> None:
    """Insert or refresh a connected account (idempotent on the provider id)."""
    with _connect() as con:
        con.execute(
            "INSERT INTO connected_accounts "
            "(user_id, provider, platform, external_id, display_name, "
            " avatar_url, status, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?) "
            "ON CONFLICT(user_id, provider, external_id) DO UPDATE SET "
            "  platform = excluded.platform, "
            "  display_name = excluded.display_name, "
            "  avatar_url = excluded.avatar_url, "
            "  status = 'active', meta_json = excluded.meta_json",
            (user_id, provider, platform, external_id, display_name,
             avatar_url, json.dumps(meta or {})))


def list_accounts(user_id: int) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM connected_accounts WHERE user_id = ? "
            "ORDER BY connected_at DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_account(user_id: int, account_id: int) -> dict | None:
    with _connect() as con:
        r = con.execute(
            "SELECT * FROM connected_accounts WHERE id = ? AND user_id = ?",
            (account_id, user_id)).fetchone()
        return dict(r) if r else None


def delete_account(user_id: int, account_id: int) -> dict | None:
    """Mark revoked + remove the row. Returns the row (for the provider call)."""
    acct = get_account(user_id, account_id)
    if acct is None:
        return None
    with _connect() as con:
        con.execute("DELETE FROM connected_accounts WHERE id = ? AND user_id = ?",
                    (account_id, user_id))
    return acct


def prune_missing(user_id: int, provider: str,
                  keep_external_ids: set[str]) -> None:
    """Drop locally-cached accounts the provider no longer reports (revoked
    upstream). Keeps the local list honest after a re-sync."""
    with _connect() as con:
        rows = con.execute(
            "SELECT id, external_id FROM connected_accounts "
            "WHERE user_id = ? AND provider = ?", (user_id, provider)).fetchall()
        stale = [r["id"] for r in rows if r["external_id"] not in keep_external_ids]
        for sid in stale:
            con.execute("DELETE FROM connected_accounts WHERE id = ?", (sid,))


# --------------------------------------------------------------------- shares
def create_share(user_id: int, project: str, clip_id: int, account_id: int,
                 kind: str, caption: str, media_path: str,
                 status: str = "draft", scheduled_at: str | None = None) -> int:
    with _connect() as con:
        cur = con.execute(
            "INSERT INTO shares (user_id, project, clip_id, account_id, kind, "
            " caption, media_path, status, scheduled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, project, clip_id, account_id, kind, caption, media_path,
             status, scheduled_at))
        return int(cur.lastrowid)


def update_share(share_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as con:
        con.execute(f"UPDATE shares SET {cols} WHERE id = ?",
                    (*fields.values(), share_id))


def list_shares(user_id: int, project: str | None = None,
                clip_id: int | None = None) -> list[dict]:
    q = "SELECT * FROM shares WHERE user_id = ?"
    args: list = [user_id]
    if project is not None:
        q += " AND project = ?"
        args.append(project)
    if clip_id is not None:
        q += " AND clip_id = ?"
        args.append(clip_id)
    q += " ORDER BY created_at DESC"
    with _connect() as con:
        return [dict(r) for r in con.execute(q, args).fetchall()]


def get_share(user_id: int, share_id: int) -> dict | None:
    with _connect() as con:
        r = con.execute("SELECT * FROM shares WHERE id = ? AND user_id = ?",
                        (share_id, user_id)).fetchone()
        return dict(r) if r else None
