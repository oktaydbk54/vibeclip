"""BYOK keys are encrypted at rest; a tampered/foreign token must decrypt to
None (never raise, never leak)."""

from chat import secretbox


def test_roundtrip():
    tok = secretbox.encrypt("sk-secret-abcdef123456")
    assert tok != "sk-secret-abcdef123456"      # actually encrypted
    assert secretbox.decrypt(tok) == "sk-secret-abcdef123456"


def test_garbage_returns_none():
    assert secretbox.decrypt("not-a-valid-token") is None
    assert secretbox.decrypt("") is None
