"""Faz 4a — Reframe a landscape clip to vertical 9:16.

Strategy: sample frames across the clip, detect faces (OpenCV Haar cascade),
take the median face center-x, then crop a 9:16 window centered on that point
(clamped to frame bounds). A static, face-aware crop is robust and avoids the
jitter of naive per-frame panning. Falls back to center crop if no face found.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg

TARGET_RATIO = 9 / 16  # width / height for vertical


def _median_face_center_x(video_path: str, width: int, samples: int = 24) -> float | None:
    import cv2
    import numpy as np

    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)

    cap = cv2.VideoCapture(video_path)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    step = max(1, frame_count // samples)

    centers: list[float] = []
    idx = 0
    while True:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(60, 60))
        if len(faces):
            # Largest face in this frame.
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            centers.append(x + w / 2.0)
        idx += step
        if idx >= frame_count:
            break
    cap.release()

    if not centers:
        return None
    return float(np.median(centers))


def reframe_vertical(clip_path: str, out_path: str | None = None) -> str:
    """Crop `clip_path` to 9:16, centered on detected faces. Returns output path."""
    src = Path(clip_path)
    info = ffprobe_info(clip_path)
    w, h = info["width"], info["height"]

    # Target crop width for full-height 9:16 window.
    crop_w = int(round(h * TARGET_RATIO))
    if crop_w >= w:
        # Already tall enough; pad-crop height instead (center).
        crop_h = int(round(w / TARGET_RATIO))
        y = max(0, (h - crop_h) // 2)
        crop_filter = f"crop={w}:{crop_h}:0:{y}"
    else:
        center_x = _median_face_center_x(clip_path, w) or (w / 2.0)
        x = int(round(center_x - crop_w / 2.0))
        x = max(0, min(x, w - crop_w))  # clamp inside frame
        crop_filter = f"crop={crop_w}:{h}:{x}:0"

    out = out_path or str(src.with_name(src.stem + "_vertical.mp4"))
    run_ffmpeg([
        "-i", clip_path,
        "-vf", f"{crop_filter},scale=1080:1920:force_original_aspect_ratio=decrease,"
               f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "copy",
        out,
    ])
    return out
