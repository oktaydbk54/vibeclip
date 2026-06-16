"""TTS provider layer: per-model routing of instructions/speed, graceful retry
when a model/SDK rejects the new kwargs, and provider isolation. A fake OpenAI
client captures the kwargs — no network, no real audio."""

import sys
import types

import pipeline.tts as tts


class _FakeSpeech:
    def __init__(self, sink, reject_kw=None, raise_generic_on_kw=False):
        self.sink = sink
        self.reject_kw = reject_kw          # kwarg name that triggers TypeError
        self.raise_generic_on_kw = raise_generic_on_kw

    def create(self, **kw):
        if self.reject_kw and self.reject_kw in kw:
            raise TypeError(f"unexpected kwarg {self.reject_kw}")
        if self.raise_generic_on_kw and ("instructions" in kw or "speed" in kw):
            raise RuntimeError("API rejected field")
        self.sink["kw"] = kw
        # Mimic the legacy create().write_to_file path.
        sink = self.sink

        class _Resp:
            def write_to_file(self, path):
                open(path, "wb").write(b"RIFFfake")
        return _Resp()


def _install_fake_openai(monkeypatch, speech):
    client = types.SimpleNamespace(
        audio=types.SimpleNamespace(speech=speech))
    fake_mod = types.ModuleType("openai")
    fake_mod.OpenAI = lambda *a, **k: client
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    monkeypatch.setattr(tts.config, "llm_settings",
                        lambda tier="fast": ("k", None, "m"))


def test_gpt4o_mini_tts_gets_instructions_not_speed(monkeypatch, tmp_path):
    sink = {}
    _install_fake_openai(monkeypatch, _FakeSpeech(sink))
    monkeypatch.setattr(tts.config, "TTS_MODEL", "gpt-4o-mini-tts")
    monkeypatch.setattr(tts.config, "TTS_USE_INSTRUCTIONS", True)
    out = tts.synthesize("hello", str(tmp_path / "a.wav"),
                         instructions="Speak warmly.", speed=1.5)
    assert out and sink["kw"].get("instructions") == "Speak warmly."
    assert "speed" not in sink["kw"]


def test_tts1_gets_clamped_speed_not_instructions(monkeypatch, tmp_path):
    sink = {}
    _install_fake_openai(monkeypatch, _FakeSpeech(sink))
    monkeypatch.setattr(tts.config, "TTS_MODEL", "tts-1")
    out = tts.synthesize("hello", str(tmp_path / "a.wav"),
                         instructions="ignored here", speed=99.0)
    assert out and sink["kw"].get("speed") == 4.0   # clamped to [0.25, 4.0]
    assert "instructions" not in sink["kw"]


def test_instructions_suppressed_when_flag_off(monkeypatch, tmp_path):
    sink = {}
    _install_fake_openai(monkeypatch, _FakeSpeech(sink))
    monkeypatch.setattr(tts.config, "TTS_MODEL", "gpt-4o-mini-tts")
    monkeypatch.setattr(tts.config, "TTS_USE_INSTRUCTIONS", False)
    tts.synthesize("hello", str(tmp_path / "a.wav"), instructions="x")
    assert "instructions" not in sink["kw"]


def test_retries_without_kwarg_on_typeerror(monkeypatch, tmp_path):
    sink = {}
    _install_fake_openai(monkeypatch,
                         _FakeSpeech(sink, reject_kw="instructions"))
    monkeypatch.setattr(tts.config, "TTS_MODEL", "gpt-4o-mini-tts")
    monkeypatch.setattr(tts.config, "TTS_USE_INSTRUCTIONS", True)
    out = tts.synthesize("hello", str(tmp_path / "a.wav"),
                         instructions="Speak warmly.")
    # Retried without the rejected kwarg and still produced the file.
    assert out and "instructions" not in sink["kw"]


def test_retries_plain_on_generic_api_rejection(monkeypatch, tmp_path):
    sink = {}
    _install_fake_openai(monkeypatch,
                         _FakeSpeech(sink, raise_generic_on_kw=True))
    monkeypatch.setattr(tts.config, "TTS_MODEL", "gpt-4o-mini-tts")
    monkeypatch.setattr(tts.config, "TTS_USE_INSTRUCTIONS", True)
    out = tts.synthesize("hello", str(tmp_path / "a.wav"),
                         instructions="Speak warmly.")
    assert out and sink["kw"] == {"model": "gpt-4o-mini-tts", "voice": "alloy",
                                  "input": "hello", "response_format": "wav"}


class _StreamingSpeech:
    """A fake exposing the CURRENT SDK's with_streaming_response.create path,
    so the production-primary code branch is actually exercised."""

    def __init__(self, sink):
        self.sink = sink
        self.with_streaming_response = self  # same object exposes create()

    def create(self, **kw):
        self.sink["kw"] = kw
        sink = self.sink

        class _Ctx:
            def __enter__(self_):
                class _Resp:
                    def stream_to_file(self__, path):
                        open(path, "wb").write(b"RIFFfake")
                return _Resp()

            def __exit__(self_, *a):
                return False
        return _Ctx()


def test_streaming_path_passes_instructions(monkeypatch, tmp_path):
    sink = {}
    _install_fake_openai(monkeypatch, _StreamingSpeech(sink))
    monkeypatch.setattr(tts.config, "TTS_MODEL", "gpt-4o-mini-tts")
    monkeypatch.setattr(tts.config, "TTS_USE_INSTRUCTIONS", True)
    out = tts.synthesize("hi", str(tmp_path / "s.wav"),
                         instructions="Speak calmly.")
    assert out and sink["kw"].get("instructions") == "Speak calmly."


def test_piper_provider_ignores_new_kwargs(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(tts.config, "TTS_PROVIDER", "piper")
    monkeypatch.setattr(tts, "_piper",
                        lambda text, path: called.setdefault("ok", True) or path)
    out = tts.synthesize("hi", str(tmp_path / "p.wav"),
                         instructions="x", speed=2.0)
    assert out and called.get("ok")
