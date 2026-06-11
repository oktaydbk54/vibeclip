"""Faz 5c — Visual effects: punch-in zoom, transitions, fades.

punch_zoom: shows a zoomed copy of the frame during chosen time windows (LLM
picks emphasis lines). Single-pass: split -> zoom one branch -> overlay it only
during the windows. Subject stays centered.

transition: join two clips with an xfade crossfade (+ audio acrossfade).
fade_in_out: clean fade from/to black with matching audio fades.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg


def _smoothstep_ramp(s: float, e: float, ramp: float) -> str:
    """ffmpeg expr: 0->1 smoothstep after `s`, 1->0 smoothstep before `e`."""
    up = f"clip((it-{s:.3f})/{ramp:.3f},0,1)"
    down = f"clip(({e:.3f}-it)/{ramp:.3f},0,1)"
    return f"(pow({up},2)*(3-2*{up}))*(pow({down},2)*(3-2*{down}))"


def build_zoom_vf(windows: list[tuple], width: int, height: int, fps: float,
                  zoom: float = 1.18, ease: float = 0.25) -> str:
    """Build the eased zoompan -vf string WITHOUT rendering (for pass fusion).

    Returns "" when there is nothing to zoom. width/height/fps must describe the
    frame the filter will actually run on (post-reframe when fused).

    A window may carry a 4th element — a Ken-Burns MOTION direction:
      "center" (default static punch), "left"/"right" (horizontal drift),
      "up"/"down" (vertical drift). The drift magnitude scales with (zoom-1),
      so it is 0 at the window edges where zoom returns to 1 — no snap.
    """
    z_terms, x_terms, y_terms = [], [], []
    for win in windows:
        s, e = float(win[0]), float(win[1])
        z = float(win[2]) if len(win) > 2 else zoom
        motion = str(win[3]) if len(win) > 3 and win[3] else "center"
        if e <= s:
            continue
        ramp = min(ease, (e - s) / 2)
        z_terms.append(f"+{z - 1:.4f}*{_smoothstep_ramp(s, e, ramp)}")
        if motion in ("left", "right", "up", "down"):
            # progress 0->1 across the window; pan from one side to the other.
            # delta is gated to the window and scaled by the live (iw-iw/zoom),
            # which is ~0 at the edges, so the drift eases in/out with the zoom.
            prog = f"clip((it-{s:.3f})/{(e - s):.3f},0,1)"
            gate = f"gte(it\\,{s:.3f})*lt(it\\,{e:.3f})"
            sign = "-1" if motion in ("left", "up") else "1"
            if motion in ("left", "right"):
                x_terms.append(
                    f"+({gate})*({sign})*(iw-iw/zoom)*({prog}-0.5)")
            else:
                y_terms.append(
                    f"+({gate})*({sign})*(ih-ih/zoom)*({prog}-0.5)")
    if not z_terms:
        return ""
    z_expr = "1" + "".join(z_terms)
    x_expr = "iw/2-(iw/zoom/2)" + "".join(x_terms)
    y_expr = "ih/2-(ih/zoom/2)" + "".join(y_terms)
    return (
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
        f":d=1:s={width}x{height}:fps={fps:g}"
    )


def punch_zoom(
    clip_path: str,
    windows: list[tuple],
    zoom: float = 1.18,
    ease: float = 0.25,
    out_path: str | None = None,
) -> str:
    """Apply an EASED punch-in zoom during each window. Returns path.

    windows: [(start, end)] or [(start, end, zoom)] — a per-window zoom level
    overrides the default (e.g. a stronger 1.26 punch on the hook).
    The zoom ramps in/out over `ease` seconds with a smoothstep curve, so the
    punch feels organic instead of snapping on like a switch.
    """
    src = Path(clip_path)
    info = ffprobe_info(clip_path)
    vf = build_zoom_vf(windows, info["width"], info["height"],
                       info["fps"] or 30, zoom=zoom, ease=ease)
    if not vf:
        return clip_path

    out = out_path or str(src.with_name(src.stem + "_zoom.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-vf", vf,
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out


# Per-platform mastering targets (research: YT normalizes to -14 LUFS;
# TikTok/Reels mixes run hotter, ~-11; TP headroom survives transcode).
PLATFORM_LOUDNESS = {
    "youtube_shorts": (-14.0, -1.5),
    "tiktok": (-11.0, -1.0),
    "instagram_reels": (-11.0, -1.0),
}


def _loudnorm_measure(path: str, lufs: float, tp: float) -> dict | None:
    """First loudnorm pass: measure input stats for linear two-pass mode."""
    import json as _json
    import subprocess
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(Path(path).resolve()),
             "-af", f"loudnorm=I={lufs:g}:TP={tp:g}:LRA=11:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=300)
        m = p.stderr[p.stderr.rfind("{"):p.stderr.rfind("}") + 1]
        data = _json.loads(m)
        return {k: data[k] for k in
                ("input_i", "input_tp", "input_lra", "input_thresh",
                 "target_offset")}
    except Exception:
        return None


def fade_in_out(clip_path: str, fade: float = 0.4, out_path: str | None = None,
                normalize: bool = False, lufs: float = -14.0,
                tp: float = -1.5) -> str:
    """Fade from black at the start and to black at the end (video + audio).

    normalize=True appends EBU R128 loudnorm — used as the chain's SINGLE
    normalization point (per-stage loudnorm was removed; stacking it crushed
    dynamics). lufs/tp set the platform mastering target. Two-pass: a measure
    run feeds linear-mode loudnorm, which hits the target within ~0.5 LU
    (single-pass drifted ~1+ LU on short clips).
    """
    src = Path(clip_path)
    dur = ffprobe_info(clip_path)["duration"]
    fo = max(0.0, dur - fade)
    afilter = f"afade=t=in:st=0:d={fade},afade=t=out:st={fo:.3f}:d={fade}"
    if normalize:
        ln = f"loudnorm=I={lufs:g}:TP={tp:g}:LRA=11"
        m = _loudnorm_measure(str(src), lufs, tp)
        if m:
            ln += (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
                   f":measured_LRA={m['input_lra']}"
                   f":measured_thresh={m['input_thresh']}"
                   f":offset={m['target_offset']}:linear=true")
        afilter += "," + ln
    out = out_path or str(src.with_name(src.stem + "_fade.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-vf", f"fade=t=in:st=0:d={fade},fade=t=out:st={fo:.3f}:d={fade}",
        "-af", afilter,
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "aac", "-b:a", "192k",
        str(Path(out).resolve()),
    ])
    return out


def transition(
    clip_a: str,
    clip_b: str,
    kind: str = "fade",
    duration: float = 0.5,
    out_path: str | None = None,
) -> str:
    """Join clip_a -> clip_b with an xfade transition. Returns output path.

    kind: any xfade type (fade, slideleft, wipeleft, circleopen, dissolve, ...).
    Both clips must share resolution/fps (our pipeline keeps them consistent).
    """
    a, b = Path(clip_a), Path(clip_b)
    dur_a = ffprobe_info(clip_a)["duration"]
    offset = max(0.0, dur_a - duration)

    filtergraph = (
        f"[0:v][1:v]xfade=transition={kind}:duration={duration}:offset={offset:.3f}[v];"
        f"[0:a][1:a]acrossfade=d={duration}[a]"
    )

    out = out_path or str(a.with_name(a.stem + "_to_" + b.stem + ".mp4"))
    run_ffmpeg([
        "-i", str(a.resolve()),
        "-i", str(b.resolve()),
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", "[a]",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "aac", "-b:a", "192k",
        str(Path(out).resolve()),
    ])
    return out
