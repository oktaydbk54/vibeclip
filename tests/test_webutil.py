"""Analytics must be opt-in: GA injected only when GA_MEASUREMENT_ID is set,
stripped entirely on a self-host instance."""

from chat import webutil

_HTML = (
    "<head>"
    "<!-- Google Analytics (GA4) -->"
    '<script async src="https://www.googletagmanager.com/gtag/js?id=G-VLDS78ZF9K"></script>'
    "<script>"
    "  window.dataLayer = window.dataLayer || [];"
    "  function gtag(){dataLayer.push(arguments);}"
    "  gtag('js', new Date());"
    "  gtag('config', 'G-VLDS78ZF9K');"
    "</script>"
    "<title>x</title></head>"
)


def test_ga_stripped_when_unset(monkeypatch):
    monkeypatch.delenv("GA_MEASUREMENT_ID", raising=False)
    out = webutil.inject_head(_HTML)
    assert "googletagmanager" not in out
    assert "<title>x</title>" in out      # rest of head preserved


def test_ga_id_swapped_when_set(monkeypatch):
    monkeypatch.setenv("GA_MEASUREMENT_ID", "G-TEST123456")
    out = webutil.inject_head(_HTML)
    assert "G-TEST123456" in out
    assert "G-VLDS78ZF9K" not in out


_PAGE = '<!doctype html><html lang="en"><head><title>x</title></head><body></body></html>'


def test_hosted_studio_default_true(monkeypatch):
    monkeypatch.delenv("HOSTED_STUDIO", raising=False)
    assert webutil.hosted_studio() is True
    assert 'data-hosted-studio="1"' in webutil.inject_head(_PAGE)


def test_hosted_studio_false_marketing(monkeypatch):
    for v in ("false", "0", "no", "off", ""):
        monkeypatch.setenv("HOSTED_STUDIO", v)
        assert webutil.hosted_studio() is False
    assert 'data-hosted-studio="0"' in webutil.inject_head(_PAGE)


def test_hosted_studio_flag_stamps_html_once(monkeypatch):
    monkeypatch.setenv("HOSTED_STUDIO", "true")
    out = webutil.inject_head(_PAGE)
    assert out.count("data-hosted-studio") == 1
    assert '<html data-hosted-studio="1" lang="en">' in out
