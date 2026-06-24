"""Generative media (Faz 1): provider-agnostic text-to-video/image as b-roll.

Mocks the HTTP layer — asserts graceful degradation without a key, URL
extraction across provider response shapes, aspect mapping, content-addressed
caching (generate once, reuse on replay), and failure -> None (never raises).
"""

import pytest

from pipeline import config, genmedia


@pytest.fixture(autouse=True)
def _tmp_genmedia_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(genmedia, "GENMEDIA_DIR", tmp_path / "genmedia")


def _enable(monkeypatch):
    monkeypatch.setattr(config, "GENMEDIA_API_KEY", "test-key")
    monkeypatch.setattr(config, "GENMEDIA_PROVIDER", "fal")


# ------------------------------------------------------------------- helpers

def test_available_reflects_key(monkeypatch):
    monkeypatch.setattr(config, "GENMEDIA_API_KEY", "")
    assert genmedia.available() is False
    monkeypatch.setattr(config, "GENMEDIA_API_KEY", "k")
    assert genmedia.available() is True


def test_find_url_handles_provider_shapes():
    assert genmedia._find_url({"video": {"url": "http://x/v.mp4"}}) == "http://x/v.mp4"
    assert genmedia._find_url(
        {"images": [{"url": "http://x/i.png"}]}) == "http://x/i.png"
    assert genmedia._find_url("http://x/bare.mp4") == "http://x/bare.mp4"
    assert genmedia._find_url({"output": ["http://x/o.mp4"]}) == "http://x/o.mp4"
    assert genmedia._find_url({"nothing": "here"}) is None


def test_aspect_ratio_maps_dims():
    assert genmedia._aspect_ratio(1080, 1920) == "9:16"
    assert genmedia._aspect_ratio(1080, 1080) == "1:1"
    assert genmedia._aspect_ratio(1920, 1080) == "16:9"
    assert genmedia._aspect_ratio(0, 0) == "9:16"   # degenerate -> default


# ------------------------------------------------------------------- generate

def test_generate_video_none_without_key(monkeypatch):
    monkeypatch.setattr(config, "GENMEDIA_API_KEY", "")
    assert genmedia.generate_video("a cat") is None


def test_generate_video_downloads_and_caches(monkeypatch):
    _enable(monkeypatch)
    calls = {"fal": 0, "dl": 0}

    def fake_fal(model, payload):
        calls["fal"] += 1
        assert payload["prompt"] == "ocean waves"
        assert payload["aspect_ratio"] == "9:16"
        return {"video": {"url": "http://x/out.mp4"}}

    def fake_dl(url, out_path):
        calls["dl"] += 1
        out_path.write_bytes(b"FAKEMP4")
        return str(out_path)

    monkeypatch.setattr(genmedia, "_fal", fake_fal)
    monkeypatch.setattr(genmedia, "_download", fake_dl)

    p1 = genmedia.generate_video("ocean waves", width=1080, height=1920)
    assert p1 and p1.endswith(".mp4")
    # Second identical call hits the on-disk cache: no second fal/download.
    p2 = genmedia.generate_video("ocean waves", width=1080, height=1920)
    assert p2 == p1
    assert calls == {"fal": 1, "dl": 1}


def test_generate_image_uses_image_model(monkeypatch):
    _enable(monkeypatch)
    seen = {}

    def fake_fal(model, payload):
        seen["model"] = model
        return {"images": [{"url": "http://x/p.png"}]}

    monkeypatch.setattr(genmedia, "_fal", fake_fal)
    monkeypatch.setattr(genmedia, "_download",
                        lambda url, out: (out.write_bytes(b"PNG"), str(out))[1])
    p = genmedia.generate_image("neon city", seed=7)
    assert p and p.endswith(".png")
    assert seen["model"] == config.GENMEDIA_IMAGE_MODEL


def test_generate_video_provider_error_returns_none(monkeypatch):
    _enable(monkeypatch)

    def boom(model, payload):
        raise RuntimeError("provider 500")

    monkeypatch.setattr(genmedia, "_fal", boom)
    assert genmedia.generate_video("anything") is None   # never raises


def test_generate_video_empty_response_returns_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(genmedia, "_fal", lambda m, p: {"status": "no media"})
    assert genmedia.generate_video("anything") is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
