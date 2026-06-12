"""At-rest encryption for user-supplied secrets (BYOK LLM keys).

Fernet (AES-128-CBC + HMAC-SHA256) under a per-instance key persisted to
`.llm_secret` (chmod 600, gitignored). Stored keys are useless without that
file. The key is generated on first use, so nothing to configure.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from pipeline import config

_KEY_PATH = config.CACHE_DIR / ".llm_secret"
_cached: Fernet | None = None


def _fernet() -> Fernet:
    global _cached
    if _cached is not None:
        return _cached
    if _KEY_PATH.exists():
        key = _KEY_PATH.read_bytes()
    else:
        key = Fernet.generate_key()
        _KEY_PATH.write_bytes(key)
        try:
            _KEY_PATH.chmod(0o600)
        except OSError:
            pass
    _cached = Fernet(key)
    return _cached


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str | None:
    """Return the plaintext, or None if the token is missing/tampered/foreign."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
