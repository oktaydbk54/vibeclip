"""Storyboard-first generation (Faz 2.2) — script -> scenes -> generated short.

The inverse of the long-video->clips flow: turn a written script or topic into a
vertical short made entirely of GENERATED footage. This matches the "script to
storyboard in minutes" workflow the AI-native editors lead with, but stays in
our cross-platform, self-hosted, BYOK lane.

- plan_storyboard: LLM breaks the script into ordered scenes, each a narration
  line (the on-screen caption) + a concrete visual prompt for generation.
- assemble_storyboard: per scene, generate footage (pipeline.genmedia), burn the
  narration caption, then concatenate into one short.

Everything degrades gracefully: no LLM key -> a naive sentence split; no
GENMEDIA key / a generation miss -> that scene is skipped; zero usable scenes ->
None (the caller reports it). Style consistency comes from a shared style suffix
appended to every scene's visual prompt (and an optional shared seed).
"""

from __future__ import annotations

import re
from pathlib import Path

from pipeline import config
from pipeline.media import run_ffmpeg

_SYSTEM = """You are a short-form video director. Break the user's script or \
topic into {max_scenes} or fewer ORDERED scenes for a vertical short. For each \
scene give:
- "narration": one short on-screen line (<= 12 words), in the script's language.
- "visual": a concrete ENGLISH text-to-video prompt describing what is SEEN \
(subject, setting, action, shot) — no text/captions in the image.
Return ONLY JSON: {{"scenes": [{{"narration": "...", "visual": "..."}}]}}"""


def plan_storyboard(script: str, max_scenes: int = 6) -> list[dict]:
    """Script/topic -> [{narration, visual}] ordered scenes.

    Uses the LLM when a key is configured; otherwise falls back to splitting the
    script into sentences (narration = sentence, visual = sentence) so the flow
    still works offline (generation just gets terser prompts)."""
    script = (script or "").strip()
    if not script:
        return []
    max_scenes = max(1, min(12, int(max_scenes)))
    try:
        api_key, base_url, model = config.llm_settings()
        if not api_key:
            raise RuntimeError("no llm key")
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
            else OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system",
                       "content": _SYSTEM.format(max_scenes=max_scenes)},
                      {"role": "user", "content": script}],
            temperature=0.5,
            **config.json_response_format(base_url),
        )
        data = config.extract_json(resp.choices[0].message.content) or {}
        scenes = []
        for s in data.get("scenes", []):
            narration = str(s.get("narration", "")).strip()
            visual = str(s.get("visual", "")).strip()
            if visual:
                scenes.append({"narration": narration, "visual": visual})
        if scenes:
            return scenes[:max_scenes]
    except Exception:  # noqa: BLE001 — fall through to the offline split
        pass
    # Offline fallback: sentence split.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
    return [{"narration": s, "visual": s} for s in sentences[:max_scenes]]


def _scene_words(narration: str, duration: float) -> list[dict]:
    """Spread a narration line evenly across [0, duration] as caption words."""
    toks = [w for w in narration.split() if w]
    if not toks or duration <= 0:
        return []
    step = duration / len(toks)
    return [{"start": round(i * step, 3), "end": round((i + 1) * step, 3),
             "word": w} for i, w in enumerate(toks)]


def _concat(paths: list[str], out_path: str) -> str:
    """Concatenate same-size/fps pieces with hard cuts (filter concat)."""
    inputs: list[str] = []
    for p in paths:
        inputs += ["-i", str(Path(p).resolve())]
    n = len(paths)
    streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    fg = f"{streams}concat=n={n}:v=1:a=1[v][a]"
    run_ffmpeg([
        *inputs, "-filter_complex", fg,
        "-map", "[v]", "-map", "[a]",
        "-c:v", config.VIDEO_ENCODER, "-c:a", "aac",
        str(Path(out_path).resolve()),
    ])
    return out_path


def assemble_storyboard(scenes: list[dict], out_path: str, *,
                        width: int = 1080, height: int = 1920, fps: float = 30.0,
                        seconds_per_scene: float = 5.0, style: str = "",
                        seed: int | None = None,
                        workdir: "Path | None" = None) -> "dict | None":
    """Generate + caption + concatenate scenes into one short at out_path.

    Returns {"path", "scenes": [{narration, visual, ok}]} or None if NO scene
    could be generated. style is appended to every visual prompt for a
    consistent look; seed (when set) is shared across scenes likewise.
    """
    from pipeline import genmedia
    from pipeline.broll import normalize_media
    from pipeline.subtitle import burn_subtitles
    if not scenes or not genmedia.available():
        return None
    wd = workdir or Path(out_path).parent
    wd.mkdir(parents=True, exist_ok=True)

    pieces: list[str] = []
    report: list[dict] = []
    for i, sc in enumerate(scenes):
        visual = sc["visual"] + (f", {style}" if style else "")
        raw = genmedia.generate_video(visual, width=width, height=height,
                                      seconds=seconds_per_scene, seed=seed)
        ok = bool(raw)
        if ok:
            norm = normalize_media(raw, width=width, height=height, fps=fps,
                                   still_duration=seconds_per_scene)
            words = _scene_words(sc.get("narration", ""), seconds_per_scene)
            if words:
                piece = str(wd / f"_sb_{i:02d}.mp4")
                norm = burn_subtitles(norm, words, karaoke=False,
                                      out_path=piece)
            pieces.append(norm)
        report.append({"narration": sc.get("narration", ""),
                       "visual": sc["visual"], "ok": ok})

    if not pieces:
        return None
    final = _concat(pieces, out_path) if len(pieces) > 1 else \
        _single(pieces[0], out_path)
    return {"path": final, "scenes": report}


def _single(src: str, out_path: str) -> str:
    """One-scene storyboard: copy the single piece to the output path."""
    import shutil
    shutil.copyfile(src, out_path)
    return out_path
