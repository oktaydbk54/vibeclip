"""DNN (YuNet) face detection for active-speaker reframe.

The detection SOURCE swaps Haar -> YuNet, but everything must degrade
gracefully to the historical Haar path when the model is unavailable
(offline / airgapped self-host) and must keep the same (cx,cy,w,h) pixel
convention and the same build_reframe_vf crop-x keyframe interface.
"""

import numpy as np

from pipeline import tracking


# --------------------------------------------------------------------------- #
# Graceful Haar fallback when the YuNet model can't be obtained.
# --------------------------------------------------------------------------- #
def test_face_detector_falls_back_to_haar_when_no_model(monkeypatch):
    # Download disabled / unavailable -> _yunet_model_path returns None.
    monkeypatch.setattr(tracking, "_yunet_model_path", lambda: None)
    det = tracking._face_detector()
    assert isinstance(det, tracking._HaarDetector)


def test_yunet_model_path_disabled(monkeypatch):
    monkeypatch.setattr(tracking.config, "YUNET_DISABLE", True)
    assert tracking._yunet_model_path() is None


def test_yunet_model_path_download_failure(monkeypatch, tmp_path):
    # No cached model + a failing download -> None (Haar fallback), no crash.
    monkeypatch.setattr(tracking.config, "YUNET_DISABLE", False)
    monkeypatch.setattr(tracking.config, "YUNET_MODEL_PATH",
                        tmp_path / "missing.onnx")

    def _boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert tracking._yunet_model_path() is None


# --------------------------------------------------------------------------- #
# _largest_face is detector-agnostic and returns pixel (cx,cy,w,h).
# --------------------------------------------------------------------------- #
class _FakeDetector:
    def __init__(self, faces):
        self._faces = faces

    def detect(self, frame_bgr):
        return list(self._faces)


def test_largest_face_picks_biggest_returns_pixels():
    # Two faces; the second is larger and must win.
    det = _FakeDetector([(100.0, 50.0, 40.0, 40.0),
                         (600.0, 300.0, 120.0, 120.0)])
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    cx, cy, w, h = tracking._largest_face(det, frame)
    assert (cx, cy, w, h) == (600.0, 300.0, 120.0, 120.0)


def test_largest_face_none_when_empty():
    det = _FakeDetector([])
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert tracking._largest_face(det, frame) is None


# --------------------------------------------------------------------------- #
# detect_face_track stays dense / gap-free with the new detector source.
# --------------------------------------------------------------------------- #
def test_detect_face_track_dense_with_fake_detector(monkeypatch):
    monkeypatch.setattr(tracking, "ffprobe_info",
                        lambda p: {"width": 1280, "height": 720,
                                   "duration": 2.0})

    class _FakeCap:
        def __init__(self, *a):
            self._i = 0

        def get(self, prop):
            import cv2
            if prop == cv2.CAP_PROP_FPS:
                return 30.0
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return 60
            return 0

        def set(self, *a):
            return True

        def read(self):
            self._i += 1
            return True, np.zeros((720, 1280, 3), dtype=np.uint8)

        def release(self):
            pass

    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a: _FakeCap())
    # Fixed face center so every sampled frame yields a known box.
    monkeypatch.setattr(tracking, "_face_detector",
                        lambda *a, **k: _FakeDetector([(640.0, 360.0, 200.0, 200.0)]))

    track = tracking.detect_face_track("x.mp4", fps_sample=5.0)
    assert len(track) >= 2
    # Dense + gap-free: every entry has all keys.
    for p in track:
        assert {"t", "cx", "cy", "w", "h"} <= set(p)
        assert p["cx"] == 640.0


# --------------------------------------------------------------------------- #
# build_reframe_vf centers the crop on a known fake-detected box.
# --------------------------------------------------------------------------- #
def test_build_reframe_vf_crops_on_detected_center(monkeypatch):
    # Landscape source so the tracked crop path runs.
    monkeypatch.setattr(tracking, "ffprobe_info",
                        lambda p: {"width": 1920, "height": 1080,
                                   "duration": 4.0})
    monkeypatch.setattr(tracking, "classify_scene_type", lambda p, **k: "single")

    # Fake face hard to the right edge; the crop x should track toward it.
    fake_cx = 1500.0
    track = [{"t": t, "cx": fake_cx, "cy": 540.0, "w": 200.0, "h": 200.0}
             for t in (0.0, 1.0, 2.0, 3.0, 4.0)]
    monkeypatch.setattr(tracking, "detect_face_track", lambda p, **k: track)
    monkeypatch.setattr(tracking, "analyze_audio_energy", lambda p, **k: [])
    monkeypatch.setattr(tracking, "_mouth_motion", lambda t, p: {})

    vf = tracking.build_reframe_vf("x.mp4")  # default 9:16
    # Output contract unchanged.
    assert "scale=1080:1920" in vf
    assert "crop=w=" in vf and "x='" in vf
    # crop_w for 9:16 of a 1080-high frame = 607; centered on 1500 -> ~1196.
    crop_w = int(round(1080 * (9 / 16)))
    expected_x = fake_cx - crop_w / 2.0
    # The piecewise x-expr should mention a value near the expected crop x.
    assert f"{expected_x:.2f}" in vf
