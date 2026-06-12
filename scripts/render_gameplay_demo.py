"""Render the ONE extra landing/README demo state that render_landing_demo.py
doesn't: the split-screen "brainrot" format — MrBeast-styled clip on top,
muted Minecraft-parkour gameplay on the bottom (the recognizable TikTok look).

Reuses the SAME session/source as render_landing_demo.py (outputs/landing_demo_src
.mp4) so it composes the real pipeline output, then writes chat/static/demo/
demo_g.mp4 (mb + gameplay). Run after render_landing_demo.py, or any time the
source session already exists.

Gameplay footage: "Minecraft Parkour Gameplay No Copyright 4K" by Orbital - No
Copyright Gameplay (YouTube, CC-BY) — attribution is REQUIRED wherever the output
is published (landing footer + README). Minecraft visuals © Mojang, permitted
under Mojang's commercial usage guidelines. See assets/gameplay/CREDITS.txt.

Run:  uv run python scripts/render_gameplay_demo.py   (from the repo root)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chat import tools  # noqa: E402
from chat.session import Session  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "outputs" / "landing_demo_src.mp4"
OUT = ROOT / "chat" / "static" / "demo"


def compress(inp: str, outp: Path) -> None:
    """540x960 H.264 web copy with faststart, AAC audio (matches the other demos)."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", inp, "-vf", "scale=540:960",
         "-c:v", "libx264", "-crf", "26", "-preset", "slow",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "aac", "-b:a", "96k", str(outp)],
        check=True, capture_output=True)


def main() -> None:
    if not SRC.exists():
        raise SystemExit(
            f"{SRC} missing — run scripts/render_landing_demo.py first.")

    sess = Session.load_or_create(str(SRC), build_proxy=False)
    clip = sess.clip(1)

    # Reset to the baseline manual cut (same contract as render_landing_demo.reset).
    clip["stages"] = [st for st in clip["stages"] if st["name"] == "cut"]
    clip["current"] = clip["stages"][0]["output"]
    sess.save()

    # MrBeast style (captions + jumpcut + zoom + sfx + music), THEN stack the
    # muted Minecraft gameplay underneath — the full split-screen brainrot look.
    r = tools.apply_style(sess, 1, "mrbeast")
    if not r.get("ok"):
        raise RuntimeError(f"apply_style failed -> {r.get('error')}")
    r = tools.add_gameplay_background(sess, 1, pack="minecraft",
                                      layout=0.6, where="full")
    if not r.get("ok"):
        raise RuntimeError(f"add_gameplay_background failed -> {r.get('error')}")

    cur = sess.clip(1)["current"]
    outp = OUT / "demo_g.mp4"
    compress(cur, outp)
    size = outp.stat().st_size
    print(f"  demo_g.mp4  {size/1024:8.1f} KB  (mb + minecraft split-screen)")


if __name__ == "__main__":
    main()
