"""The app boots and serves its public, no-LLM-needed surfaces. Catches import
errors and broken routes before they hit a release."""

from fastapi.testclient import TestClient

from chat.app import app

client = TestClient(app)


def test_landing_ok():
    assert client.get("/").status_code == 200


def test_blog_index_ok():
    r = client.get("/blog")
    assert r.status_code == 200
    assert "vibeclip" in r.text.lower()


def test_seo_files_ok():
    assert client.get("/robots.txt").status_code == 200
    assert client.get("/sitemap.xml").status_code == 200


def test_self_host_has_no_analytics(monkeypatch):
    # With no GA id configured, no analytics script should be injected.
    monkeypatch.delenv("GA_MEASUREMENT_ID", raising=False)
    assert "googletagmanager" not in client.get("/").text


def test_settings_requires_auth():
    # Unauthenticated settings page redirects to login (302, no auto-follow).
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/login" in r.headers.get("location", "")
