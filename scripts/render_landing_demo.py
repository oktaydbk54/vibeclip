"""Pre-render the landing-page A/B demo assets from the REAL KESİM pipeline.

Runs our actual edit pipeline offline over one short vertical clip and writes 10
reachable A/B states as static MP4s under chat/static/demo/, plus a poster
frame. The landing page swaps these real outputs with the studio's compare UI —
no backend needed.

SOURCE / LICENSE: the demo footage is a Creative-Commons-Attribution (CC-BY)
clip — "Three tips to talk to camera like a pro" by Andy Dickinson
(https://www.youtube.com/watch?v=hu0JYxDHpQI), YouTube CC-BY (reuse allowed).
It is a landscape 1280x720 talking-head; we pre-cropped it to a centered 9:16
720x1280 master (dickinson_vertical_src.mp4) so the pipeline's manual cut +
540x960 compress stay distortion-free without a face-aware reframe stage.
CC-BY requires visible attribution — see chat/static/CREDITS.txt and the landing
page footer credit.

State model (a SET, replayed in canonical order so click-order is irrelevant):
    style: None | "mrbeast"   s: silences cut   c: captions   p: punchier
mrbeast subsumes s+c (apply_style sets jumpcut+subtitles+zoom+sfx+fade+music in
one batched pass), so once style is applied only the "p" axis remains free:
    base s c p sc sp cp scp  + mb mbp   = 10 files.

Why a fresh session: Session.load_or_create keys sessions by file STEM, and a
session for LGFG_intro_demo_v9 ALREADY EXISTS at outputs/sessions/. We COPY the
source to outputs/landing_demo_src.mp4 first so this script gets its OWN session
dir (outputs/sessions/landing_demo_src/) and never clobbers the existing one.

build_proxy=False => cut_source() reads the full-res 720x1280 source (no proxy
job), and the clip is built MANUALLY (not via generate_clips, whose DEFAULT_STAGES
would bake jumpcut/subtitles into the "original" A).

Run:  uv run python scripts/render_landing_demo.py   (from the repo root)

Point DEMO_SRC_MP4 at your own CC-BY 9:16 source clip (defaults to
outputs/dickinson_vertical_src.mp4 inside the repo).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Run from anywhere: ensure the repo root (which holds the chat/ + pipeline/
# packages) is importable. server.py relies on cwd; scripts/ does not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chat import tools  # noqa: E402
from chat.session import Session  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "outputs" / "landing_demo_src.mp4"
# CC-BY source, pre-cropped to centered 9:16 720x1280 (see module docstring).
ORIG = os.getenv("DEMO_SRC_MP4", str(ROOT / "outputs" / "dickinson_vertical_src.mp4"))
OUT = ROOT / "chat" / "static" / "demo"
# 38.5–52.5s: a clean sentence onset (speech resumes at 38.5) through ~52s, with
# ~3 natural pauses inside so cut_silences shows a visible duration delta.
SEG = (38.5, 52.5)  # ~14s window


def probe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def compress(inp: str, outp: Path) -> None:
    """540x960 H.264 web copy with faststart, AAC audio (matches spec)."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", inp, "-vf", "scale=540:960",
         "-c:v", "libx264", "-crf", "26", "-preset", "slow",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "aac", "-b:a", "96k", str(outp)],
        check=True, capture_output=True)


def main() -> None:
    SRC.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ORIG, SRC)
    OUT.mkdir(parents=True, exist_ok=True)

    # Fresh session keyed on the copied stem (does NOT touch LGFG_intro_demo_v9).
    sess = Session.load_or_create(str(SRC), build_proxy=False)
    sess.data["clips"] = [{
        "id": 1, "title": "demo", "start": SEG[0], "end": SEG[1],
        "score": 0, "status": "pending", "stages": [], "current": None,
    }]
    # Manual cut — the ONLY baseline stage. "original" A is a plain trim, no
    # jumpcut/subtitles baked in (that's the whole point of not using
    # generate_clips / DEFAULT_STAGES).
    sess.set_stage(1, "cut", {"start": SEG[0], "end": SEG[1]})
    sess.save()

    def reset() -> None:
        """Strip every stage except the baseline cut, then re-point current."""
        clip = sess.clip(1)
        clip["stages"] = [st for st in clip["stages"] if st["name"] == "cut"]
        clip["current"] = clip["stages"][0]["output"]
        sess.save()

    # REAL tool signatures (verified against chat/tools.py):
    #   cut_silences(session, clip_id, max_pause=0.5)
    #   set_subtitles(session, clip_id, karaoke=None, ...)
    #   auto_zoom(session, clip_id, density=0.25, strength=1.18)
    #   auto_pace(session, clip_id, max_static=5.0)
    #   apply_style(session, clip_id, style)
    def S() -> dict:
        return tools.cut_silences(sess, 1, max_pause=0.4)

    def C() -> dict:
        return tools.set_subtitles(sess, 1, karaoke=True)

    def P() -> list[dict]:
        return [tools.auto_zoom(sess, 1, density=0.35, strength=1.2),
                tools.auto_pace(sess, 1, max_static=3.0)]

    def MB() -> dict:
        return tools.apply_style(sess, 1, "mrbeast")

    STATES: dict[str, list] = {
        "base": [],
        "s": [S], "c": [C], "p": [P],
        "sc": [S, C], "sp": [S, P], "cp": [C, P], "scp": [S, C, P],
        "mb": [MB], "mbp": [MB, P],
    }

    results: list[tuple[str, float, int]] = []
    for key, fns in STATES.items():
        reset()
        for fn in fns:
            r = fn()
            # P() returns a list of two tool results; the rest a single dict.
            for one in (r if isinstance(r, list) else [r]):
                if not one.get("ok"):
                    raise RuntimeError(
                        f"state {key}: tool failed -> {one.get('error')}")
        cur = sess.clip(1)["current"]
        outp = OUT / f"demo_{key}.mp4"
        compress(cur, outp)
        dur = probe_duration(str(outp))
        size = outp.stat().st_size
        results.append((key, dur, size))
        print(f"  demo_{key}.mp4  {dur:6.2f}s  {size/1024:8.1f} KB")

    # Poster from the base clip's first frame.
    poster = OUT / "poster.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(OUT / "demo_base.mp4"),
         "-frames:v", "1", "-q:v", "4", str(poster)],
        check=True, capture_output=True)
    print(f"  poster.jpg  {poster.stat().st_size/1024:8.1f} KB")

    base_dur = dict((k, d) for k, d, _ in results)["base"]
    print(f"\nbase duration = {base_dur:.2f}s")
    print("silence-cut delta (base - s)  = "
          f"{base_dur - dict((k, d) for k, d, _ in results)['s']:.2f}s")
    print("mrbeast   delta (base - mb)   = "
          f"{base_dur - dict((k, d) for k, d, _ in results)['mb']:.2f}s")


if __name__ == "__main__":
    main()
