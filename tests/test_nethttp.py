"""nethttp: the shared retry-capable HTTP helper that genmedia/broll/tts share.

Asserts the retry policy — transient errors (timeout/5xx/network) get retried,
permanent 4xx do not — plus the json/download conveniences. Network is faked at
urllib.request.urlopen; backoff is forced to 0 so the suite stays fast.
"""

import io
import json
import urllib.error

import pytest

from pipeline import nethttp


class _Resp(io.BytesIO):
    """A urlopen() context-manager returning fixed bytes."""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _patch_urlopen(monkeypatch, side_effects):
    """side_effects: list of either bytes (success) or an Exception to raise.
    Each urlopen call consumes the next entry."""
    calls = {"n": 0}
    seq = list(side_effects)

    def fake(req, timeout=None):
        i = calls["n"]
        calls["n"] += 1
        eff = seq[i]
        if isinstance(eff, Exception):
            raise eff
        return _Resp(eff)

    monkeypatch.setattr("urllib.request.urlopen", fake)
    return calls


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(nethttp.time, "sleep", lambda *_: None)


def test_request_bytes_success_first_try(monkeypatch):
    calls = _patch_urlopen(monkeypatch, [b"hello"])
    assert nethttp.request_bytes("http://x") == b"hello"
    assert calls["n"] == 1


def test_retries_transient_then_succeeds(monkeypatch):
    calls = _patch_urlopen(monkeypatch, [TimeoutError("slow"), b"ok"])
    assert nethttp.request_bytes("http://x", retries=2) == b"ok"
    assert calls["n"] == 2  # one retry consumed


def test_retries_5xx_then_succeeds(monkeypatch):
    err = urllib.error.HTTPError("http://x", 503, "busy", {}, None)
    calls = _patch_urlopen(monkeypatch, [err, b"ok"])
    assert nethttp.request_bytes("http://x", retries=2) == b"ok"
    assert calls["n"] == 2


def test_4xx_is_not_retried(monkeypatch):
    err = urllib.error.HTTPError("http://x", 404, "nope", {}, None)
    calls = _patch_urlopen(monkeypatch, [err, b"unreached"])
    with pytest.raises(urllib.error.HTTPError):
        nethttp.request_bytes("http://x", retries=3)
    assert calls["n"] == 1  # failed fast, no retry


def test_exhausts_retries_then_raises(monkeypatch):
    boom = TimeoutError("down")
    calls = _patch_urlopen(monkeypatch, [boom, boom, boom, boom])
    with pytest.raises(TimeoutError):
        nethttp.request_bytes("http://x", retries=2)
    assert calls["n"] == 3  # initial + 2 retries


def test_request_json_posts_body_and_parses(monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen["method"] = req.method
        seen["data"] = req.data
        seen["ctype"] = req.headers.get("Content-type")
        return _Resp(json.dumps({"ok": 1}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake)
    out = nethttp.request_json("http://x", body={"a": 2})
    assert out == {"ok": 1}
    assert seen["method"] == "POST"
    assert json.loads(seen["data"]) == {"a": 2}
    assert seen["ctype"] == "application/json"  # auto-set


def test_request_json_get_when_no_body(monkeypatch):
    def fake(req, timeout=None):
        assert req.method == "GET" and req.data is None
        return _Resp(b'{"v": 3}')

    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert nethttp.request_json("http://x") == {"v": 3}


def test_download_writes_file(monkeypatch, tmp_path):
    _patch_urlopen(monkeypatch, [b"\x00\x01\x02"])
    out = tmp_path / "f.bin"
    res = nethttp.download("http://x", out)
    assert res == str(out) and out.read_bytes() == b"\x00\x01\x02"


def test_download_empty_returns_none(monkeypatch, tmp_path):
    _patch_urlopen(monkeypatch, [b""])
    assert nethttp.download("http://x", tmp_path / "f.bin") is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
