"""BYOK provider presets + per-user override resolution. All four providers must
route through the OpenAI-compatible client (OpenAI/DeepSeek native, Gemini/Claude
via their compat endpoints)."""

import json

from chat import auth, secretbox


def test_all_providers_present():
    for p in ("openai", "gemini", "claude", "deepseek", "custom"):
        assert p in auth._PROVIDER_DEFAULTS


def test_gemini_and_claude_have_compat_base_urls():
    assert "googleapis.com" in auth._PROVIDER_DEFAULTS["gemini"]["base_url"]
    assert "anthropic.com" in auth._PROVIDER_DEFAULTS["claude"]["base_url"]


def test_user_llm_override_decrypts():
    row = {"profile_json": json.dumps({"llm": {
        "provider": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash", "model_pro": "gemini-2.5-pro",
        "key_enc": secretbox.encrypt("AIza-real-key"),
    }})}
    ov = auth.user_llm_override(row)
    assert ov["api_key"] == "AIza-real-key"
    assert "googleapis.com" in ov["base_url"]
    assert ov["model"] == "gemini-2.5-flash"


def test_user_llm_override_none_when_unset():
    assert auth.user_llm_override({"profile_json": "{}"}) is None
    assert auth.user_llm_override(None) is None


def test_base_url_validation_rejects_bad_scheme():
    ok, _ = auth._validate_base_url("ftp://example.com")
    assert not ok
    ok, _ = auth._validate_base_url("https://api.anthropic.com/v1/")
    assert ok
