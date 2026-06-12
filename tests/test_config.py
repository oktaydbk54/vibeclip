"""LLM-key resolution is the riskiest BYOK logic: a per-user override must win
over the server env key, and the env fallback must keep working untouched. If
this regresses, a user's clips silently run on the wrong key."""

from pipeline import config


def test_env_fallback(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(config, "OPENAI_MODEL", "fast-m")
    monkeypatch.setattr(config, "OPENAI_MODEL_PRO", "pro-m")
    monkeypatch.setattr(config, "LLM_BASE_URL", None)
    assert config.llm_settings("fast") == ("env-key", None, "fast-m")
    assert config.llm_settings("pro") == ("env-key", None, "pro-m")


def test_explicit_override_wins(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "env-key")
    ov = {"api_key": "user-key", "base_url": "https://api.deepseek.com",
          "model": "deepseek-chat"}
    assert config.llm_settings("fast", override=ov) == (
        "user-key", "https://api.deepseek.com", "deepseek-chat")


def test_override_pro_falls_back_to_fast_model(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "env-key")
    ov = {"api_key": "user-key", "model": "only-fast"}
    assert config.llm_settings("pro", override=ov)[2] == "only-fast"


def test_contextvar_override(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(config, "OPENAI_MODEL", "fast-m")
    token = config.set_llm_override({"api_key": "ctx-key", "model": "ctx-m"})
    try:
        assert config.llm_settings("fast") == ("ctx-key", None, "ctx-m")
    finally:
        config.reset_llm_override(token)
    # after reset, env wins again
    assert config.llm_settings("fast") == ("env-key", None, "fast-m")


def test_empty_override_ignored(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(config, "OPENAI_MODEL", "fast-m")
    # an override dict with no api_key must NOT mask the env key
    assert config.llm_settings("fast", override={"model": "x"}) == (
        "env-key", None, "fast-m")


def test_no_key_raises(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    import pytest
    with pytest.raises(RuntimeError):
        config.llm_settings("fast")


def test_json_response_format_only_for_native_providers():
    # OpenAI (no base_url) + DeepSeek honor response_format=json_object.
    assert config.json_response_format(None)["response_format"]["type"] == "json_object"
    assert config.json_response_format("https://api.deepseek.com")
    # Gemini's compat URL contains "/openai/" — must STILL be excluded.
    assert config.json_response_format(
        "https://generativelanguage.googleapis.com/v1beta/openai/") == {}
    assert config.json_response_format("https://api.anthropic.com/v1/") == {}
    # Unknown custom endpoints: be conservative (rely on prompt + extract_json).
    assert config.json_response_format("https://my-llm.local/v1") == {}


def test_extract_json_tolerates_fences_and_prose():
    assert config.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert config.extract_json('Sure: {"events": [1, 2]} done') == {"events": [1, 2]}
    assert config.extract_json("[1, 2, 3]") == [1, 2, 3]
    assert config.extract_json('{"x": true}') == {"x": True}


def test_extract_json_raises_on_garbage():
    import json
    import pytest
    with pytest.raises(json.JSONDecodeError):  # subclass of ValueError
        config.extract_json("not json at all")
