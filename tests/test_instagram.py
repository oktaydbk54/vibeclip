"""Instagram download helper: URL validation must accept ONLY individual
Reel/post permalinks and reject profile roots (the bulk-scraping shape we
refuse). No network is touched — these are pure string checks plus a caption
sidecar parse."""

import json

from chat import instagram


def test_accepts_reel_and_post_permalinks():
    for url in (
        "https://www.instagram.com/reel/ABC123def/",
        "https://instagram.com/reel/ABC123def",
        "https://www.instagram.com/codewithbod/reel/ABC123def/",
        "https://www.instagram.com/p/ABC123def/",
        "https://www.instagram.com/codewithbod/p/ABC123/",
        "https://www.instagram.com/reels/ABC123/",
    ):
        assert instagram.is_instagram_url(url), url


def test_rejects_profiles_and_other_hosts():
    for url in (
        "https://www.instagram.com/codewithbod/",      # profile root = bulk
        "https://instagram.com/codewithbod",
        "https://www.youtube.com/watch?v=abc",
        "https://example.com/reel/abc/",
        "not a url",
        "",
    ):
        assert not instagram.is_instagram_url(url), url


def test_handle_extracted_only_from_namespaced_url():
    assert instagram.handle_from_url(
        "https://instagram.com/codewithbod/reel/ABC/") == "codewithbod"
    assert instagram.handle_from_url(
        "https://www.instagram.com/reel/ABC/") == ""


def test_basename_is_deterministic_and_safe():
    u = "https://www.instagram.com/reel/ABC123/"
    b = instagram._basename_for(u)
    assert b == instagram._basename_for(u)        # stable
    assert b.startswith("ig_") and b.isascii() and "/" not in b


def test_caption_recovered_from_info_json(tmp_path):
    vid = tmp_path / "ig_x.mp4"
    vid.write_bytes(b"\x00")
    (tmp_path / "ig_x.info.json").write_text(
        json.dumps({"description": "my reel caption 🔥 #devlife"}))
    assert "my reel caption" in instagram._caption_from_info_json(vid)
    # Missing sidecar → empty string, never raises.
    assert instagram._caption_from_info_json(tmp_path / "ig_none.mp4") == ""


def test_download_instagram_rejects_non_reel_before_network():
    import pytest
    with pytest.raises(ValueError):
        instagram.download_instagram("https://instagram.com/codewithbod/")
