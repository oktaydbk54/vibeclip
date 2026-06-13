"""Faz 4a+ — Active-speaker / motion-tracked 9:16 reframe.

`reframe.py` uses ONE static median-face center for the whole clip, so
multi-speaker podcasts, screen-share, and moving subjects get a wrong or
drifting crop. This module is a drop-in replacement that:

  1. classify_scene_type  -> 'single' | 'multi' | 'screencast'
  2. detect_face_track     -> per-sampled-frame largest-face box (gaps filled)
  3. pick_active_speaker   -> per-window crop center, aligning face presence
                             with audio RMS peaks (mouth-motion tiebreak)
  4. smooth_centers        -> exponentially-smoothed cx to kill jitter
  5. reframe_vertical_tracked -> time-varying ffmpeg `crop` x-expression,
                             with screencast letterbox fallback.

Same scale=1080:1920 + pad tail as `reframe.reframe_vertical`, so it is a
drop-in for orchestrate step 3 (replace `reframe_vertical(path)` with
`reframe_vertical_tracked(path)`).

Audio energy is computed self-contained here (decode PCM via ffmpeg + numpy
windowed RMS) — there is no shared `structure` module in this tree, and we
must not edit other files. numpy + opencv only (already used). Heavy libs are
lazy-imported inside functions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg

TARGET_RATIO = 9 / 16  # width / height for vertical

# Supported export aspect ratios -> (width/height ratio, output canvas w, h).
# 9:16 keeps the historical 1080x1920 contract byte-for-byte.
ASPECTS: dict[str, tuple[float, int, int]] = {
    "9:16": (9 / 16, 1080, 1920),
    "1:1": (1.0, 1080, 1080),
    "16:9": (16 / 9, 1920, 1080),
}

# Scene-classification thresholds.
_SCREENCAST_EDGE_RATIO = 0.11   # Canny "on" pixel fraction above this => text/UI heavy
_FACE_CLUSTER_GAP = 0.18        # face centers within this frac of W are the same speaker
_MULTI_MIN_PRESENCE = 0.20      # a second cluster must appear in >=20% of sampled frames

_HAAR = "haarcascade_frontalface_default.xml"
_YUNET_SCORE_THRESH = 0.6  # min YuNet confidence to accept a detection


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def _cascade():
    import cv2
    return cv2.CascadeClassifier(cv2.data.haarcascades + _HAAR)


def _yunet_model_path() -> str | None:
    """Path to the cached YuNet .onnx, downloading it once if needed.

    Best-effort: returns the cached path if present, otherwise tries a single
    short download into CACHE_DIR. On ANY failure (disabled, no network, bad
    response, missing FaceDetectorYN) returns None so callers fall back to Haar.
    The cache lives under config.CACHE_DIR (gitignored); subsequent runs reuse it.
    """
    if config.YUNET_DISABLE:
        return None
    try:
        import cv2
        if not hasattr(cv2, "FaceDetectorYN"):
            return None
    except Exception:
        return None

    path = config.YUNET_MODEL_PATH
    if path.exists() and path.stat().st_size > 0:
        return str(path)

    # Download once, best-effort with a short timeout.
    import urllib.request
    tmp = path.with_suffix(".onnx.part")
    try:
        with urllib.request.urlopen(config.YUNET_URL, timeout=10) as resp:
            data = resp.read()
        if not data:
            return None
        tmp.write_bytes(data)
        tmp.replace(path)
        print(f"[reframe] downloaded YuNet face model -> {path}")
        return str(path)
    except Exception as exc:  # offline / airgapped / URL error -> Haar fallback
        print(f"[reframe] YuNet model unavailable ({exc}); using Haar cascade")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None


class _YuNetDetector:
    """YuNet (DNN) face detector returning (cx, cy, w, h) pixel tuples.

    Detects profile / tilted / multiple faces far better than Haar. setInputSize
    must match each frame's (w, h); detections are score-filtered. Any per-frame
    error is swallowed (returns [] for that frame) so a YuNet glitch degrades to
    "no face this frame" — handled by interpolation — rather than crashing.
    """

    def __init__(self, model_path: str):
        import cv2
        self._cv2 = cv2
        self._det = cv2.FaceDetectorYN.create(
            model_path, "", (320, 320), score_threshold=_YUNET_SCORE_THRESH)
        self._size = (320, 320)

    def detect(self, frame_bgr) -> list[tuple[float, float, float, float]]:
        try:
            h, w = frame_bgr.shape[:2]
            if (w, h) != self._size:
                self._det.setInputSize((w, h))
                self._size = (w, h)
            _, faces = self._det.detect(frame_bgr)
            if faces is None or len(faces) == 0:
                return []
            out: list[tuple[float, float, float, float]] = []
            for f in faces:
                x, y, fw, fh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
                if fw <= 0 or fh <= 0:
                    continue
                out.append((x + fw / 2.0, y + fh / 2.0, fw, fh))
            return out
        except Exception:
            return []


class _HaarDetector:
    """Haar-cascade fallback detector with the same detect(frame_bgr) contract."""

    def __init__(self, min_size: int = 50):
        self._cascade = _cascade()
        self._min = min_size

    def detect(self, frame_bgr) -> list[tuple[float, float, float, float]]:
        import cv2
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5,
            minSize=(self._min, self._min))
        out: list[tuple[float, float, float, float]] = []
        for x, y, w, h in faces:
            out.append((x + w / 2.0, y + h / 2.0, float(w), float(h)))
        return out


def _face_detector(min_size: int = 50):
    """Return the active face detector (YuNet if available, else Haar).

    The detector exposes detect(frame_bgr) -> list of (cx, cy, w, h) pixel
    tuples. YuNet is preferred for profile/tilted/multi-face robustness; it
    degrades to the historical Haar cascade whenever the model or the
    FaceDetectorYN API is unavailable, so offline self-hosts never break.
    """
    model = _yunet_model_path()
    if model:
        try:
            return _YuNetDetector(model)
        except Exception as exc:
            print(f"[reframe] YuNet init failed ({exc}); using Haar cascade")
    return _HaarDetector(min_size)


def _largest_face(detector, frame_bgr):
    """Return (cx, cy, w, h) of the largest detected face, or None.

    `detector` is a _face_detector() instance; `frame_bgr` is the COLOR frame
    (YuNet needs BGR — the Haar fallback converts to gray internally).
    """
    faces = detector.detect(frame_bgr)
    if not faces:
        return None
    return max(faces, key=lambda f: f[2] * f[3])


def analyze_audio_energy(video_path: str, hop: float = 0.20) -> list[dict]:
    """Self-contained audio RMS energy envelope.

    Decodes the clip's audio to mono 16 kHz PCM via ffmpeg and returns a list of
    {t, rms} samples, one every `hop` seconds. Returns [] if the clip has no
    audio. (Stands in for a shared structure.analyze_audio_energy.)
    """
    import numpy as np

    sr = 16000
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(Path(video_path).resolve()),
        "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-acodec", "pcm_s16le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return []

    audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return []

    win = max(1, int(sr * hop))
    out: list[dict] = []
    for i in range(0, audio.size, win):
        chunk = audio[i:i + win]
        if chunk.size == 0:
            continue
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        out.append({"t": round(i / sr, 3), "rms": rms})
    return out


# --------------------------------------------------------------------------- #
# 1. Scene classification
# --------------------------------------------------------------------------- #
def classify_scene_type(clip_path: str, samples: int = 24) -> str:
    """Return 'single' | 'multi' | 'screencast'.

    Samples ~`samples` frames. Screencast = high Canny edge/text density.
    Otherwise counts distinct horizontal face clusters: a stable second cluster
    => 'multi', else 'single' (covers no-face too, which crops to center later).
    """
    import cv2
    import numpy as np

    detector = _face_detector()
    cap = cv2.VideoCapture(clip_path)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1
    step = max(1, frame_count // samples)

    edge_ratios: list[float] = []
    face_centers_x: list[float] = []
    sampled = 0
    idx = 0
    while idx < frame_count:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        sampled += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        edges = cv2.Canny(gray, 100, 200)
        edge_ratios.append(float(np.count_nonzero(edges)) / edges.size)

        face = _largest_face(detector, frame)
        if face is not None:
            face_centers_x.append(face[0] / max(1, width))  # normalized 0..1
        idx += step
    cap.release()

    if sampled == 0:
        return "single"

    # Screencast: dense edges AND few/no faces.
    mean_edge = float(np.mean(edge_ratios)) if edge_ratios else 0.0
    face_presence = len(face_centers_x) / sampled
    if mean_edge > _SCREENCAST_EDGE_RATIO and face_presence < 0.5:
        return "screencast"

    # Cluster normalized face centers greedily by horizontal proximity.
    clusters: list[list[float]] = []
    for cx in sorted(face_centers_x):
        for c in clusters:
            if abs(cx - (sum(c) / len(c))) <= _FACE_CLUSTER_GAP:
                c.append(cx)
                break
        else:
            clusters.append([cx])

    big_clusters = [c for c in clusters if len(c) / sampled >= _MULTI_MIN_PRESENCE]
    if len(big_clusters) >= 2:
        return "multi"
    return "single"


# --------------------------------------------------------------------------- #
# 2. Face track
# --------------------------------------------------------------------------- #
def detect_face_track(clip_path: str, fps_sample: float = 5.0) -> list[dict]:
    """Sample the clip at `fps_sample` fps; return largest-face track.

    Each entry: {t, cx, cy, w, h} in PIXELS. Frames with no face are left out
    of detection but interpolated so the returned list is dense and gap-free
    (linear interpolation between known points; ends held flat).
    """
    import cv2

    info = ffprobe_info(clip_path)
    duration = info["duration"]
    width, height = info["width"], info["height"]

    detector = _face_detector()
    cap = cv2.VideoCapture(clip_path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or int(duration * src_fps) or 1

    n_samples = max(2, int(round(duration * fps_sample)))
    times = [round(k * duration / (n_samples - 1), 3) for k in range(n_samples)]

    raw: list[dict | None] = []
    for t in times:
        fidx = min(frame_count - 1, int(round(t * src_fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
        ok, frame = cap.read()
        if not ok:
            raw.append(None)
            continue
        face = _largest_face(detector, frame)
        if face is None:
            raw.append(None)
        else:
            cx, cy, w, h = face
            raw.append({"t": t, "cx": cx, "cy": cy, "w": w, "h": h})
    cap.release()

    # Interpolate gaps. If nothing was ever found, center the whole track.
    known = [i for i, r in enumerate(raw) if r is not None]
    if not known:
        return [{"t": t, "cx": width / 2.0, "cy": height / 2.0,
                 "w": float(width), "h": float(height)} for t in times]

    def _val(i: int, key: str) -> float:
        return raw[i][key]  # type: ignore[index]

    track: list[dict] = []
    for i, t in enumerate(times):
        if raw[i] is not None:
            track.append(raw[i])  # type: ignore[arg-type]
            continue
        # Find bracketing known indices.
        left = max((k for k in known if k < i), default=None)
        right = min((k for k in known if k > i), default=None)
        if left is None:
            src = right
            entry = {k: _val(src, k) for k in ("cx", "cy", "w", "h")}
        elif right is None:
            src = left
            entry = {k: _val(src, k) for k in ("cx", "cy", "w", "h")}
        else:
            frac = (i - left) / (right - left)
            entry = {k: _val(left, k) + (_val(right, k) - _val(left, k)) * frac
                     for k in ("cx", "cy", "w", "h")}
        entry["t"] = t
        track.append(entry)
    return track


# --------------------------------------------------------------------------- #
# 3. Active speaker
# --------------------------------------------------------------------------- #
def pick_active_speaker(track: list[dict], video_path: str,
                        window: float = 1.5) -> list[dict]:
    """Per-window active-speaker crop center.

    Splits the clip into `window`-second windows. Within each window picks the
    face cx weighted toward moments of high audio RMS (someone is talking), with
    mouth-region motion variance as a tiebreak (a moving mouth => active).

    Returns [{t_start, t_end, cx}] (cx in pixels). With no audio it falls back
    to the median face cx per window.
    """
    import numpy as np

    if not track:
        return []

    energy = analyze_audio_energy(video_path)
    t_end = track[-1]["t"]
    motion = _mouth_motion(track, video_path)  # {index: variance}

    def rms_at(t: float) -> float:
        if not energy:
            return 1.0
        # nearest energy sample
        best = min(energy, key=lambda e: abs(e["t"] - t))
        return best["rms"]

    windows: list[dict] = []
    t0 = 0.0
    while t0 < t_end + 1e-6:
        t1 = min(t_end, t0 + window)
        members = [(i, p) for i, p in enumerate(track) if t0 <= p["t"] <= t1 + 1e-6]
        if not members:
            t0 = t1
            if t1 >= t_end:
                break
            continue

        # Weight each sampled face by audio energy * (1 + normalized mouth motion).
        weights = []
        cxs = []
        for i, p in members:
            w = rms_at(p["t"]) + 1e-4
            w *= 1.0 + motion.get(i, 0.0)
            weights.append(w)
            cxs.append(p["cx"])
        weights = np.asarray(weights, dtype=np.float64)
        cxs = np.asarray(cxs, dtype=np.float64)
        if weights.sum() <= 0:
            cx = float(np.median(cxs))
        else:
            cx = float(np.average(cxs, weights=weights))
        windows.append({"t_start": round(t0, 3), "t_end": round(t1, 3), "cx": cx})

        if t1 >= t_end:
            break
        t0 = t1
    return windows


def _mouth_motion(track: list[dict], video_path: str) -> dict:
    """Frame-diff variance in each sampled face's lower (mouth) region.

    Returns {track_index: normalized_variance in 0..1}. Used as an active-speaker
    tiebreak. Cheap: reads only the sampled frames already in the track.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

    prev_patch = None
    raw: dict[int, float] = {}
    for i, p in enumerate(track):
        fidx = min(frame_count - 1, int(round(p["t"] * src_fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape
        # Lower half of the face bbox = mouth region.
        x0 = int(max(0, p["cx"] - p["w"] / 2))
        x1 = int(min(W, p["cx"] + p["w"] / 2))
        y0 = int(max(0, p["cy"]))
        y1 = int(min(H, p["cy"] + p["h"] / 2))
        if x1 <= x0 or y1 <= y0:
            prev_patch = None
            continue
        patch = cv2.resize(gray[y0:y1, x0:x1], (32, 32)).astype(np.float32)
        if prev_patch is not None:
            raw[i] = float(np.var(patch - prev_patch))
        prev_patch = patch
    cap.release()

    if not raw:
        return {}
    mx = max(raw.values()) or 1.0
    return {i: v / mx for i, v in raw.items()}


# --------------------------------------------------------------------------- #
# 4. Smoothing
# --------------------------------------------------------------------------- #
def smooth_centers(centers: list[float], alpha: float = 0.12,
                   fast_alpha: float = 0.40) -> list[float]:
    """Adaptive exponential smoothing of cx values.

    Small frame-to-frame deltas (breathing, detector noise) get heavy smoothing
    (`alpha`) so the pan doesn't jitter; genuine jumps (speaker change, subject
    moves) get `fast_alpha` so the crop catches up instead of lagging ~500ms
    behind the action. The jump threshold adapts to the track's own motion scale.
    """
    if not centers:
        return []
    vals = [float(c) for c in centers]
    diffs = [abs(b - a) for a, b in zip(vals, vals[1:])]
    if diffs:
        diffs_sorted = sorted(diffs)
        med = diffs_sorted[len(diffs_sorted) // 2]
        jump_thresh = max(20.0, 3.0 * med)  # px; floor for near-static tracks
    else:
        jump_thresh = 20.0

    out = [vals[0]]
    for c in vals[1:]:
        a = fast_alpha if abs(c - out[-1]) > jump_thresh else alpha
        out.append(a * c + (1.0 - a) * out[-1])
    return out


# --------------------------------------------------------------------------- #
# 5. Tracked reframe
# --------------------------------------------------------------------------- #
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _build_x_expr(keyframes: list[tuple[float, float]], crop_w: int,
                  width: int) -> str:
    """Build a piecewise ffmpeg crop-x expression from (t, x) keyframes.

    Uses nested if(lt(t,t1), <lerp>, ...) so x ramps linearly between keyframes
    (eased panning), clamped to [0, W-crop_w]. Coordinates are pre-clamped.
    """
    max_x = max(0, width - crop_w)
    kf = [(t, _clamp(x, 0, max_x)) for t, x in keyframes]
    if not kf:
        return f"{max_x // 2}"
    if len(kf) == 1:
        return f"{kf[0][1]:.2f}"

    # Build from the last segment backwards.
    expr = f"{kf[-1][1]:.2f}"  # after last keyframe: hold
    for i in range(len(kf) - 1, 0, -1):
        t0, x0 = kf[i - 1]
        t1, x1 = kf[i]
        dt = max(1e-3, t1 - t0)
        # linear interp on [t0, t1]
        seg = f"({x0:.2f}+({x1 - x0:.4f})*(t-{t0:.3f})/{dt:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    # Before the first keyframe: hold first value.
    expr = f"if(lt(t,{kf[0][0]:.3f}),{kf[0][1]:.2f},{expr})"
    return expr


def build_reframe_vf(clip_path: str, aspect: str = "9:16") -> str:
    """Build the reframe -vf chain WITHOUT rendering (for pass fusion).

    The chain outputs the canvas for `aspect` (default 9:16 -> 1080x1920,
    keeping the historical contract byte-for-byte). The time-varying crop-x
    keyframe panning is unchanged; only the target ratio / output canvas and
    the derived crop width change per aspect. Used by reframe_vertical_tracked
    and by orchestrate's fused render pass.
    """
    target_ratio, canvas_w, canvas_h = ASPECTS.get(aspect, ASPECTS["9:16"])
    info = ffprobe_info(clip_path)
    w, h = info["width"], info["height"]

    scale_pad = (f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
                 f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2")

    crop_w = int(round(h * target_ratio))

    # Source is not wide enough for a full-height crop at this ratio:
    # center-crop height like reframe_vertical does (covers 16:9 of a
    # landscape source -> center-crop height to the wider ratio).
    if crop_w >= w:
        crop_h = int(round(w / target_ratio))
        y = max(0, (h - crop_h) // 2)
        return f"crop={w}:{crop_h}:0:{y},{scale_pad}"

    scene = classify_scene_type(clip_path)
    if scene == "screencast":
        # Full frame, letterboxed — never crop away UI/text.
        return scale_pad

    # single / multi: track active speaker.
    track = detect_face_track(clip_path)
    windows = pick_active_speaker(track, clip_path)

    if not windows:
        center_x = w / 2.0
        x = int(round(_clamp(center_x - crop_w / 2.0, 0, w - crop_w)))
        vf = f"crop={crop_w}:{h}:{x}:0,{scale_pad}"
    else:
        # One keyframe at each window midpoint; smooth the centers.
        raw_cx = [win["cx"] for win in windows]
        smoothed = smooth_centers(raw_cx, alpha=0.15)
        keyframes: list[tuple[float, float]] = []
        for win, cx in zip(windows, smoothed):
            tmid = (win["t_start"] + win["t_end"]) / 2.0
            x = cx - crop_w / 2.0
            keyframes.append((round(tmid, 3), x))
        x_expr = _build_x_expr(keyframes, crop_w, w)
        # The filter parser splits options on ':' — our expr has none. Wrap x in
        # quotes via the standard 'x=...' form.
        vf = f"crop=w={crop_w}:h={h}:x='{x_expr}':y=0,{scale_pad}"
    return vf


def reframe_vertical_tracked(clip_path: str, out_path: str | None = None,
                             aspect: str = "9:16") -> str:
    """Active-speaker / motion-tracked reframe. Drop-in for reframe_vertical.

    - 'screencast': letterbox the full frame (scale-to-fit + pad), no crop.
    - 'single'/'multi': time-varying crop x following the (smoothed) active
      speaker, then scale + pad tail. Returns the output path.

    aspect: one of pipeline.tracking.ASPECTS ('9:16' | '1:1' | '16:9'); default
    keeps the historical 1080x1920 9:16 behavior byte-for-byte.
    """
    src = Path(clip_path)
    out = out_path or str(src.with_name(src.stem + "_tracked.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()), "-vf", build_reframe_vf(clip_path, aspect),
        "-c:v", config.VIDEO_ENCODER, "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out
