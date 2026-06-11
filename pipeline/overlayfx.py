"""V4.2 — Overlay effects: texture blends, green-screen reactions, stickers.

Three event types in ONE encode (the agency toolkit, per research):
- blend:       film grain / light leak / dust loop blended over the frame
               (screen/overlay/softlight at 20-40% opacity)
- greenscreen: meme/reaction clip chroma-keyed and placed for 0.5-1.5s
- sticker:     PNG (logo/emoji/arrow) overlaid at a position for a window

All are core `overlay`/`blend`/`chromakey` filters — no libass/drawtext.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg

BLEND_MODES = {"screen", "overlay", "softlight", "lighten", "addition",
               "multiply"}


def apply_overlays(clip_path: str, events: list[dict],
                   out_path: str | None = None) -> str:
    """Composite all overlay events in one pass. Audio untouched.

    events: [{"type": "blend", "path", "mode": "screen", "opacity": 0.3,
              "start": 0, "end": null},
             {"type": "greenscreen", "path", "start", "end",
              "width_ratio": 0.45, "x_ratio": 0.5, "y_ratio": 0.78},
             {"type": "sticker", "path", "start", "end",
              "width_ratio": 0.25, "x_ratio": 0.5, "y_ratio": 0.2}]
    x_ratio/y_ratio = the overlay's CENTER as a fraction of the frame.
    """
    src = Path(clip_path)
    events = [e for e in events if Path(e.get("path", "")).exists()]
    if not events:
        return clip_path
    info = ffprobe_info(clip_path)
    w, h, dur = info["width"], info["height"], info["duration"]

    inputs: list[str] = ["-i", str(src.resolve())]
    steps: list[str] = []
    label = "0:v"

    for i, e in enumerate(events, start=1):
        kind = e.get("type", "sticker")
        s = max(0.0, float(e.get("start", 0)))
        t = float(e["end"]) if e.get("end") else dur
        t = min(t, dur)
        en = f"enable='between(t,{s:.3f},{t:.3f})'"
        nxt = f"v{i}"

        if kind == "blend":
            mode = e.get("mode", "screen")
            if mode not in BLEND_MODES:
                mode = "screen"
            op = max(0.05, min(0.8, float(e.get("opacity", 0.3))))
            inputs += ["-stream_loop", "-1", "-i", str(Path(e["path"]).resolve())]
            steps.append(
                f"[{i}:v]trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
                f"scale={w}:{h},setsar=1,format=yuv420p[ov{i}]")
            steps.append(
                f"[{label}][ov{i}]blend=all_mode={mode}:"
                f"all_opacity={op:.2f}:{en}[{nxt}]")

        elif kind == "greenscreen":
            ow = int(w * max(0.2, min(0.9, float(e.get("width_ratio", 0.45)))))
            xr = float(e.get("x_ratio", 0.5))
            yr = float(e.get("y_ratio", 0.78))
            inputs += ["-i", str(Path(e["path"]).resolve())]
            steps.append(
                f"[{i}:v]scale={ow}:-2,chromakey=green:0.12:0.08,"
                f"setpts=PTS-STARTPTS+{s:.3f}/TB[gs{i}]")
            steps.append(
                f"[{label}][gs{i}]overlay=x={int(w * xr)}-overlay_w/2:"
                f"y={int(h * yr)}-overlay_h/2:eof_action=pass:{en}[{nxt}]")

        else:  # sticker
            ow = int(w * max(0.06, min(0.8, float(e.get("width_ratio", 0.25)))))
            xr = float(e.get("x_ratio", 0.5))
            yr = float(e.get("y_ratio", 0.2))
            inputs += ["-i", str(Path(e["path"]).resolve())]
            steps.append(f"[{i}:v]scale={ow}:-2[st{i}]")
            steps.append(
                f"[{label}][st{i}]overlay=x={int(w * xr)}-overlay_w/2:"
                f"y={int(h * yr)}-overlay_h/2:{en}[{nxt}]")
        label = nxt

    out = out_path or str(src.with_name(src.stem + "_ovl.mp4"))
    run_ffmpeg([
        *inputs,
        "-filter_complex", ";".join(steps),
        "-map", f"[{label}]", "-map", "0:a?",
        "-c:v", config.VIDEO_ENCODER, "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out


def emphasis_fx(clip_path: str, events: list[dict],
                out_path: str | None = None) -> str:
    """Flash and/or shake hits on exact moments — the 'agency edit' accent.

    events: [{"time": s, "kind": "flash"|"shake"|"flashshake"}]
    Flash = 2-3 frame brightness pop; shake = 0.4s crop jitter (frame is
    cropped ~1.5% throughout and scaled back, so non-shake moments are
    visually identical).
    """
    src = Path(clip_path)
    if not events:
        return clip_path
    info = ffprobe_info(clip_path)
    w, h = info["width"], info["height"]
    mx, my = max(8, w // 70), max(8, h // 70)

    chains: list[str] = []
    shakes = [e for e in events if e.get("kind") in ("shake", "flashshake")]
    flashes = [e for e in events if e.get("kind") in ("flash", "flashshake")]

    if shakes:
        on = "+".join(
            f"between(t,{float(e['time']):.3f},{float(e['time']) + 0.4:.3f})"
            for e in shakes)
        chains.append(
            f"crop=w=iw-{2 * mx}:h=ih-{2 * my}:"
            f"x='{mx}+{mx - 2}*sin(t*47)*({on})':"
            f"y='{my}+{my - 2}*sin(t*61)*({on})',scale={w}:{h}")
    for e in flashes:
        ts = float(e["time"])
        chains.append(
            f"eq=brightness=0.38:saturation=0.6:"
            f"enable='between(t,{ts:.3f},{ts + 0.09:.3f})'")

    out = out_path or str(src.with_name(src.stem + "_fx.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-vf", ",".join(chains),
        "-c:v", config.VIDEO_ENCODER, "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out
