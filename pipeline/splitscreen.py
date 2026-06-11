"""Split-screen "gameplay background" — the brainrot / doom-scroll format.

The main content fills the TOP of the vertical frame; a looping, muted
"background" (gameplay or oddly-satisfying footage) fills the BOTTOM. The
constant secondary motion holds attention through slower narration ("dual
attention anchoring"), which is why the format lifts retention.

Composited in ONE ffmpeg pass with `vstack` — the same single-pass philosophy
as the captions/b-roll stages. The main video's audio passes through untouched;
the background is always muted.

COPYRIGHT: every background shipped under assets/gameplay/ is copyright-free
(CC0 / royalty-free) — see assets/gameplay/CREDITS.txt. NEVER ship real Subway
Surfers footage (SYBO-copyrighted, especially with game audio); use a CC0
runner lookalike instead.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg

GAMEPLAY_DIR = config.ROOT / "assets" / "gameplay"

# Background packs the app can offer. Each value is a filename under
# assets/gameplay/. A pack is only "available" once its file is on disk, so the
# library can grow one verified CC0 clip at a time without code changes.
PACKS: dict[str, str] = {
    "minecraft": "minecraft_parkour.mp4",   # calm parkour — the classic
    "satisfying": "satisfying.mp4",          # oddly-satisfying glitter-in-water
    "runner": "runner.mp4",                  # fast forward FPV — endless-runner feel
    "ramp": "car_ramp.mp4",                  # racing-car POV — high-speed driving
}

DEFAULT_TOP_RATIO = 0.60   # top 60% content / bottom 40% background
MIN_TOP_RATIO = 0.40
MAX_TOP_RATIO = 0.80


def pack_path(pack: str) -> Path | None:
    """Resolve a pack name to its bundled asset path (None if not installed)."""
    fn = PACKS.get(pack)
    if not fn:
        return None
    p = GAMEPLAY_DIR / fn
    return p if p.exists() else None


def available_packs() -> list[str]:
    """Pack names whose footage is actually present on disk."""
    return [name for name in PACKS if pack_path(name)]


def _even(n: int) -> int:
    """ffmpeg/h264 needs even dimensions."""
    return int(n) - (int(n) % 2)


def _analyze_main(clip_path: str, frame_h: int) -> tuple[str, float]:
    """Decide HOW the main content sits in the top region — the "smart merge".

    Returns (mode, face_cy_ratio):
      'cover'   — a clean, dominant talking head: crop to fill the top, but
                  position the crop window on the FACE so it is never cut.
      'contain' — a screencast / slides-with-small-webcam / no clear face:
                  fit the WHOLE frame (nothing cropped) with a blurred fill, so
                  the face and everything else stay visible.

    Driven by face geometry + edge density (generic, not tuned to one layout):
    a big, consistently-present face on non-busy footage -> cover; a small or
    absent face, or text/UI-dense frames -> contain.
    """
    try:
        import cv2
        import numpy as np
        from pipeline import tracking as trk
    except Exception:
        return ("contain", 0.5)   # safest default: never hide anything

    cap = cv2.VideoCapture(clip_path)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    n = 9
    step = max(1, frame_count // n)
    cascade = trk._cascade()
    edge_ratios: list[float] = []
    face_cys: list[float] = []
    face_hs: list[float] = []
    sampled, idx = 0, 0
    while idx < frame_count and sampled < n:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        sampled += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        e = cv2.Canny(gray, 100, 200)
        edge_ratios.append(float(np.count_nonzero(e)) / e.size)
        f = trk._largest_face(cascade, gray)
        if f is not None:
            _cx, cy, _w, fh = f
            face_cys.append(cy)
            face_hs.append(fh)
        idx += step
    cap.release()

    if sampled == 0 or not face_cys:
        return ("contain", 0.5)

    presence = len(face_cys) / sampled
    mean_edge = sum(edge_ratios) / len(edge_ratios) if edge_ratios else 0.0
    face_cys.sort()
    med_cy = face_cys[len(face_cys) // 2]
    cy_ratio = med_cy / float(frame_h)

    # The strongest signal is the face's VERTICAL position, not its size (a
    # webcam face and a talking head are similar in size). A face sitting LOW
    # means primary content (slides/screen) is ABOVE it — cropping would throw
    # that away, so keep everything (contain). A text/UI-dense frame likewise
    # keeps its content. Otherwise the face IS the subject -> cover, cropped on
    # the face. The presence floor avoids cropping on a single fluke detection.
    face_low = cy_ratio > 0.72
    screencast = mean_edge > trk._SCREENCAST_EDGE_RATIO
    if presence >= 0.25 and not face_low and not screencast:
        return ("cover", cy_ratio)
    return ("contain", cy_ratio)


def quiet_spans(clip_path: str, min_len: float = 1.5, rel: float = 0.40,
                merge_gap: float = 1.0, hook_guard: float = 3.0,
                max_spans: int = 6) -> list[list[float]]:
    """Low-energy / quiet windows where a gameplay background earns its keep.

    Uses the clip's RMS envelope: a window counts as "quiet" when its loudness
    sits below `rel` x the clip's speech level (70th-percentile RMS). Runs are
    merged across gaps up to `merge_gap`, the first `hook_guard` seconds (the
    hook) stay full-frame, only runs >= `min_len` survive, and at most
    `max_spans` of them are kept (the LONGEST lulls) so the layout never
    toggles too often. Returns [s, e] pairs in clip-local seconds, ordered in
    time (empty if the clip is wall-to-wall loud or mute).
    """
    from pipeline.tracking import analyze_audio_energy

    hop = 0.15
    env = analyze_audio_energy(clip_path, hop=hop)
    if not env:
        return []
    rmss = sorted(e["rms"] for e in env)
    ref = rmss[min(len(rmss) - 1, int(len(rmss) * 0.70))] or max(rmss) or 0.0
    thr = ref * rel

    runs: list[list[float]] = []
    start = last = None
    for e in env:
        if e["rms"] < thr:
            if start is None:
                start = e["t"]
            last = e["t"]
        elif start is not None:
            runs.append([start, last + hop])
            start = None
    if start is not None:
        runs.append([start, last + hop])

    merged: list[list[float]] = []
    for s, e in runs:
        if merged and s - merged[-1][1] <= merge_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    kept = [[round(max(s, hook_guard), 2), round(e, 2)]
            for s, e in merged if e - max(s, hook_guard) >= min_len]
    # Keep only the longest lulls so the full<->split layout never flickers.
    kept.sort(key=lambda se: se[1] - se[0], reverse=True)
    kept = kept[:max_spans]
    kept.sort(key=lambda se: se[0])
    return kept


def apply_splitscreen(clip_path: str, params: dict,
                      out_path: str | None = None) -> str:
    """Stack the clip (top) over a looping muted background (bottom).

    params:
      path       — background mp4 (a bundled gameplay/satisfying clip)
      top_ratio  — fraction of frame height for the content (0.4-0.8, def 0.6)
      fit        — auto|cover|contain (auto = face-aware smart composition)
      spans      — optional [[s,e],...]; when given, the clip stays FULL-FRAME
                   and the split only appears during these windows (see
                   quiet_spans). Omitted/empty = split the WHOLE clip.

    Resolution-agnostic: reads the clip's real frame so it works identically on
    the 540p editing proxy and the full-res export. Empty params (no path) is a
    no-op pass-through, which is how the stage is "removed".
    """
    src = Path(clip_path)
    bg = params.get("path")
    if not bg or not Path(bg).exists():
        return clip_path

    info = ffprobe_info(clip_path)
    w, h = info["width"], info["height"]
    fps = info["fps"] or 30.0
    dur = info["duration"]

    top_ratio = float(params.get("top_ratio", DEFAULT_TOP_RATIO))
    top_ratio = min(max(top_ratio, MIN_TOP_RATIO), MAX_TOP_RATIO)

    w = _even(w)
    top_h = _even(round(h * top_ratio))
    bot_h = _even(h) - top_h          # both even -> output height stays even
    if top_h <= 0 or bot_h <= 0:
        return clip_path

    # "Smart merge": decide how the main content fills the top region so a face
    # is never buried under the gameplay. 'fit' param overrides the auto choice
    # (auto|cover|contain).
    fit = str(params.get("fit", "auto")).lower()
    if fit in ("cover", "contain"):
        mode, cy_ratio = fit, 0.5
    else:
        mode, cy_ratio = _analyze_main(clip_path, h)

    if mode == "cover":
        crop_y = _even(round(cy_ratio * h - top_h / 2))
        crop_y = max(0, min(crop_y, _even(h) - top_h))

    def _top_chain(src: str) -> str:
        if mode == "cover":
            # Talking head: crop to fill the top, window centered on the FACE
            # (clamped in-frame) so the head is never cropped off.
            return (f"[{src}]crop={w}:{top_h}:0:{crop_y},setsar=1,"
                    f"fps={fps:g},format=yuv420p[top]")
        # Screencast / slides+webcam / no clear face: fit the WHOLE frame inside
        # the top region (nothing cropped) over a blurred, zoomed copy of itself
        # so the bars read as a soft background instead of empty space.
        return (f"[{src}]split=2[mbg][mfg];"
                f"[mbg]scale={w}:{top_h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{top_h},boxblur=18:1,setsar=1[bg];"
                f"[mfg]scale={w}:{top_h}:force_original_aspect_ratio=decrease,"
                f"setsar=1[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,"
                f"fps={fps:g},format=yuv420p[top]")

    # Gameplay always COVERS the bottom region (center-cropped, no bars).
    bottom = (f"[1:v]scale={w}:{bot_h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{bot_h},setsar=1,fps={fps:g},format=yuv420p[bot]")

    out_h = top_h + bot_h
    spans = [[float(s), float(e)] for s, e in (params.get("spans") or [])
             if float(e) > float(s)]
    if spans:
        # "Smart spans": the clip stays FULL-FRAME, and the split (gameplay on
        # the bottom) only appears during the given low-energy/quiet windows —
        # gameplay fills attention when the talk goes quiet, the speaker owns the
        # frame when it doesn't. Hard cuts read fine because they land in pauses.
        enable = "+".join(f"between(t\\,{s:.2f}\\,{e:.2f})" for s, e in spans)
        fc = (f"[0:v]split=2[base][msrc];"
              f"{_top_chain('msrc')};{bottom};"
              f"[top][bot]vstack=inputs=2[split];"
              f"[base]crop={w}:{out_h}:0:0,fps={fps:g},format=yuv420p[baseN];"
              f"[baseN][split]overlay=0:0:enable='{enable}'[v]")
    else:
        fc = f"{_top_chain('0:v')};{bottom};[top][bot]vstack=inputs=2[v]"

    out = out_path or str(src.with_name(src.stem + "_split.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        # -stream_loop must precede the input it loops; the background plays on
        # repeat for the whole clip. -t caps output at the clip's duration so
        # the infinite loop can't run away.
        "-stream_loop", "-1", "-i", str(Path(bg).resolve()),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "0:a?",
        "-t", f"{dur:.3f}",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out
