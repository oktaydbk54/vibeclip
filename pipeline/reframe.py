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

# Supported export aspect ratios -> (width/height ratio, output canvas w, h).
# 9:16 keeps the historical 1080x1920 contract byte-for-byte.
ASPECTS: dict[str, tuple[float, int, int]] = {
    "9:16": (9 / 16, 1080, 1920),
    "1:1": (1.0, 1080, 1080),
    "16:9": (16 / 9, 1920, 1080),
}


def _median_face_center_x(video_path: str, width: int, samples: int = 24) -> float | None:
    import cv2
    import numpy as np

    from pipeline.tracking import _face_detector, _largest_face

    # DNN (YuNet) detector when its model is available; degrades to the Haar
    # cascade otherwise so this stays robust offline. Same median-cx contract.
    detector = _face_detector(min_size=60)

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
        face = _largest_face(detector, frame)
        if face is not None:
            centers.append(face[0])  # cx in pixels
        idx += step
        if idx >= frame_count:
            break
    cap.release()

    if not centers:
        return None
    return float(np.median(centers))


def reframe_vertical(clip_path: str, out_path: str | None = None,
                     aspect: str = "9:16") -> str:
    """Crop `clip_path` to `aspect`, centered on detected faces. Returns path.

    aspect: one of pipeline.reframe.ASPECTS ('9:16' | '1:1' | '16:9'). The
    default keeps the historical 1080x1920 9:16 behavior byte-for-byte.
    """
    target_ratio, canvas_w, canvas_h = ASPECTS.get(aspect, ASPECTS["9:16"])
    src = Path(clip_path)
    info = ffprobe_info(clip_path)
    w, h = info["width"], info["height"]

    # Target crop width for full-height window at this ratio.
    crop_w = int(round(h * target_ratio))
    if crop_w >= w:
        # Source is not wide enough; pad-crop height instead (center).
        crop_h = int(round(w / target_ratio))
        y = max(0, (h - crop_h) // 2)
        crop_filter = f"crop={w}:{crop_h}:0:{y}"
    else:
        center_x = _median_face_center_x(clip_path, w) or (w / 2.0)
        x = int(round(center_x - crop_w / 2.0))
        x = max(0, min(x, w - crop_w))  # clamp inside frame
        crop_filter = f"crop={crop_w}:{h}:{x}:0"

    scale_pad = (f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
                 f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2")
    out = out_path or str(src.with_name(src.stem + "_vertical.mp4"))
    run_ffmpeg([
        "-i", clip_path,
        "-vf", f"{crop_filter},{scale_pad}",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "copy",
        out,
    ])
    return out
