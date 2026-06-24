"""Tiny shared HTTP helper: JSON request, raw bytes, and file download — with a
bounded retry on TRANSIENT failures (network blips, timeouts, 5xx).

Consolidates the `urllib.request` dance that `genmedia`, `broll`, and `tts` each
re-implemented. One place to tune timeouts/retries/headers. Permanent 4xx
responses (bad request, not-found, unauthorized) are NOT retried, so a wrong
model id or a missing key still fails fast instead of looping.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

_TRANSIENT = (urllib.error.URLError, TimeoutError, ConnectionError)


def _is_transient(exc: Exception) -> bool:
    """A failure worth retrying: a 5xx response, or a network/timeout error.
    A 4xx is the caller's fault and permanent — surface it immediately."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return isinstance(exc, _TRANSIENT)


def request_bytes(url: str, *, headers: "dict | None" = None,
                  data: "bytes | None" = None, method: "str | None" = None,
                  timeout: float = 30, retries: int = 2,
                  backoff: float = 0.5) -> bytes:
    """Fetch raw bytes. POSTs when `data` is given (unless `method` overrides).
    Retries transient failures up to `retries` extra times with linear backoff;
    re-raises the last error once attempts are exhausted or the error is
    permanent (4xx)."""
    verb = method or ("POST" if data is not None else "GET")
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, method=verb,
                                         headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 — classify, then retry or raise
            if attempt >= retries or not _is_transient(exc):
                raise
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError("unreachable")  # the loop always returns or raises


def request_json(url: str, *, headers: "dict | None" = None,
                 body: "dict | None" = None, timeout: float = 30,
                 retries: int = 2) -> dict:
    """GET (or POST when `body` is given) a JSON endpoint and parse the reply."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdr = dict(headers or {})
    if data is not None:
        hdr.setdefault("Content-Type", "application/json")
    raw = request_bytes(url, headers=hdr, data=data, timeout=timeout,
                        retries=retries)
    return json.loads(raw.decode("utf-8"))


def download(url: str, out_path, *, headers: "dict | None" = None,
             timeout: float = 30, retries: int = 2) -> "str | None":
    """Fetch `url` and write it to `out_path`. Returns the path, or None when
    the response is empty."""
    data = request_bytes(url, headers=headers, timeout=timeout, retries=retries)
    if not data:
        return None
    out = Path(out_path)
    out.write_bytes(data)
    return str(out)
