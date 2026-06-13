"""BYOK provider presets + per-user override resolution. All four providers must
route through the OpenAI-compatible client (OpenAI/DeepSeek native, Gemini/Claude
via their compat endpoints)."""

import json

import pytest

from chat import auth, secretbox
from pipeline import config


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


# --- SERVER_KEY_ADMINS_ONLY gate: a public instance must not lend its key ----

def _row(*, has_key=False, is_admin=0):
    prof = {}
    if has_key:
        prof["llm"] = {"key_enc": secretbox.encrypt("sk-user-own")}
    return {"profile_json": json.dumps(prof), "is_admin": is_admin}


def _resolve(monkeypatch, override):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-SERVER-KEY")
    tok = config.set_llm_override(override)
    try:
        return config.llm_settings()
    finally:
        config.reset_llm_override(tok)


def test_gate_off_everyone_uses_server_key(monkeypatch):
    """Default (self-host): a logged-in user with no key falls back to env."""
    monkeypatch.delenv("SERVER_KEY_ADMINS_ONLY", raising=False)
    assert auth.user_llm_override(_row(is_admin=0)) is None
    key, _, _ = _resolve(monkeypatch, None)
    assert key == "sk-SERVER-KEY"


def test_gate_on_non_admin_without_key_is_forced_to_byok(monkeypatch):
    monkeypatch.setenv("SERVER_KEY_ADMINS_ONLY", "true")
    ov = auth.user_llm_override(_row(is_admin=0))
    assert ov == {"require_byok": True}
    with pytest.raises(RuntimeError):
        _resolve(monkeypatch, ov)


def test_gate_on_admin_still_uses_server_key(monkeypatch):
    monkeypatch.setenv("SERVER_KEY_ADMINS_ONLY", "true")
    assert auth.user_llm_override(_row(is_admin=1)) is None
    key, _, _ = _resolve(monkeypatch, None)
    assert key == "sk-SERVER-KEY"


def test_gate_on_user_with_own_key_unaffected(monkeypatch):
    monkeypatch.setenv("SERVER_KEY_ADMINS_ONLY", "true")
    ov = auth.user_llm_override(_row(has_key=True, is_admin=0))
    assert ov["api_key"] == "sk-user-own"
    key, _, _ = _resolve(monkeypatch, ov)
    assert key == "sk-user-own"
