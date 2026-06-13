"""Automation config roundtrip + the new-video diff/seed logic (no network, no
real downloads — fetch + ingest are monkeypatched)."""

import uuid

import pytest

from chat import auth, automation, youtube


@pytest.fixture
def user():
    """A throwaway verified user; deleted after the test."""
    email = f"auto_{uuid.uuid4().hex[:10]}@test.local"
    uid = auth.create_user(email, "Auto Tester", "pw123456")
    yield uid
    auth.delete_user(uid)


def test_config_roundtrip_and_defaults(user):
    # Unset → all defaults.
    cfg = automation.get_config(user)
    assert cfg["enabled"] is False
    assert cfg["auto_edit_style"] == automation.DEFAULT_STYLE
    # Save a subset; unknown keys ignored, known keys persisted.
    cfg["enabled"] = True
    cfg["channel_id"] = "UC1234567890abcdef_ghij"
    cfg["channel_handle"] = "@codewithbod"
    cfg["auto_edit_style"] = "mrbeast"
    cfg["last_video_id"] = "AAAAAAAAAAA"
    automation.save_config(user, cfg)
    again = automation.get_config(user)
    assert again["enabled"] is True
    assert again["channel_id"] == "UC1234567890abcdef_ghij"
    assert again["auto_edit_style"] == "mrbeast"
    assert again["last_video_id"] == "AAAAAAAAAAA"


def test_save_config_preserves_other_profile_keys(user):
    # A sibling profile block (e.g. onboarding) must survive an automation save.
    auth.update_profile(user, {"goal": "viral"})
    cfg = automation.get_config(user)
    cfg["channel_id"] = "UCaaaaaaaaaaaaaaaaaaaaaa"
    automation.save_config(user, cfg)
    assert auth.get_profile(user).get("goal") == "viral"


_UPLOADS = [
    {"video_id": "AAAAAAAAAAA", "title": "Newest",
     "url": "https://youtu.be/AAAAAAAAAAA"},
    {"video_id": "BBBBBBBBBBB", "title": "Middle",
     "url": "https://youtu.be/BBBBBBBBBBB"},
    {"video_id": "CCCCCCCCCCC", "title": "Oldest",
     "url": "https://youtu.be/CCCCCCCCCCC"},
]


def test_poll_enqueues_only_new_videos(user, monkeypatch):
    enqueued = []
    monkeypatch.setattr(youtube, "fetch_uploads", lambda cid: list(_UPLOADS))
    monkeypatch.setattr(automation, "submit_ingest_job",
                        lambda *a, **k: enqueued.append(a))

    cfg = automation.get_config(user)
    cfg.update({"enabled": True, "channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa",
                "last_video_id": "BBBBBBBBBBB", "auto_edit_style": "hormozi"})
    automation.save_config(user, cfg)

    res = automation.poll_user(user, auth.get_profile(user), cfg)
    assert res["new"] == 1                                   # only AAAA is newer
    assert [a[2] for a in enqueued] == ["AAAAAAAAAAA"]       # video_id arg
    # last_video_id advances to the newest upload.
    assert automation.get_config(user)["last_video_id"] == "AAAAAAAAAAA"


def test_poll_seeds_without_backfill(user, monkeypatch):
    enqueued = []
    monkeypatch.setattr(youtube, "fetch_uploads", lambda cid: list(_UPLOADS))
    monkeypatch.setattr(automation, "submit_ingest_job",
                        lambda *a, **k: enqueued.append(a))

    cfg = automation.get_config(user)
    cfg.update({"enabled": True, "channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa",
                "last_video_id": "", "auto_edit_style": "hormozi"})
    automation.save_config(user, cfg)

    res = automation.poll_user(user, auth.get_profile(user), cfg)
    assert res["new"] == 0                                   # empty last_seen seeds only
    assert enqueued == []
    assert automation.get_config(user)["last_video_id"] == "AAAAAAAAAAA"


def test_poll_enqueues_chronologically(user, monkeypatch):
    enqueued = []
    monkeypatch.setattr(youtube, "fetch_uploads", lambda cid: list(_UPLOADS))
    monkeypatch.setattr(automation, "submit_ingest_job",
                        lambda *a, **k: enqueued.append(a))

    cfg = automation.get_config(user)
    cfg.update({"enabled": True, "channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa",
                "last_video_id": "CCCCCCCCCCC", "auto_edit_style": "hormozi"})
    automation.save_config(user, cfg)

    automation.poll_user(user, auth.get_profile(user), cfg)
    # Two new (AAAA, BBBB) enqueued OLDEST-first so projects appear chronologically.
    assert [a[2] for a in enqueued] == ["BBBBBBBBBBB", "AAAAAAAAAAA"]


def test_poll_skips_when_disabled(user, monkeypatch):
    enqueued = []
    monkeypatch.setattr(youtube, "fetch_uploads", lambda cid: list(_UPLOADS))
    monkeypatch.setattr(automation, "submit_ingest_job",
                        lambda *a, **k: enqueued.append(a))
    cfg = automation.get_config(user)
    cfg.update({"enabled": False, "channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa",
                "last_video_id": "BBBBBBBBBBB"})
    res = automation.poll_user(user, auth.get_profile(user), cfg)
    assert res.get("skipped") is True and enqueued == []
    # …but force=True (the manual "Check now") bypasses the enabled gate.
    automation.poll_user(user, auth.get_profile(user), cfg, force=True)
    assert [a[2] for a in enqueued] == ["AAAAAAAAAAA"]
