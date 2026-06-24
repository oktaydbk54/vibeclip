"""shorts-mcp — MCP server that turns long videos into short clips.

Run standalone for a quick self-test:
    uv run server.py --selftest /path/to/video.mp4

Or register with an MCP client (Claude Desktop / Claude Code) and call the tools.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from pipeline.media import ffprobe_info
from pipeline.transcribe import transcribe as _transcribe
from pipeline.highlights import find_highlights as _find_highlights
from pipeline.cut import cut_clip as _cut_clip
from pipeline.orchestrate import make_short as _make_short
from pipeline.reframe import reframe_vertical as _reframe_vertical
from pipeline.subtitle import burn_subtitles as _burn_subtitles
from pipeline.jumpcut import remove_silences as _remove_silences
from pipeline.audio import add_background_music as _add_music, normalize_loudness as _normalize
from pipeline.effects import punch_zoom as _punch_zoom, transition as _transition, fade_in_out as _fade
from pipeline.sfx import add_sfx as _add_sfx
from pipeline.structure import analyze_structure as _analyze_structure
from pipeline.tracking import reframe_vertical_tracked as _reframe_tracked
from chat.mcp_bridge import register_session_tools

mcp = FastMCP("shorts")

# Expose the full session-aware editing toolset (chat.tools REGISTRY) so an
# external agent can open a project, generate clips, edit the timeline, and
# export — the same toolset the web UI and chat agent drive. The stateless
# pipeline primitives below remain for one-shot, project-less calls.
_SESSION_TOOLS = register_session_tools(mcp)


@mcp.tool()
def ping() -> str:
    """Health check. Returns 'pong' if the server is alive."""
    return "pong"


@mcp.tool()
def media_info(video_path: str) -> dict:
    """Probe a video file and return duration, resolution, fps, and codec."""
    return ffprobe_info(video_path)


@mcp.tool()
def transcribe(video_path: str, model_size: str = "") -> dict:
    """Transcribe a video to word-timestamped text (cached).

    Returns {language, duration, segments[], words[]}. `model_size` overrides
    the configured Whisper model (tiny|base|small|medium|large-v3).
    """
    return _transcribe(video_path, model_size or None)


@mcp.tool()
def find_highlights(
    video_path: str,
    platform: str = "youtube_shorts",
    count: int = 5,
    max_duration: float = 60.0,
) -> list[dict]:
    """Find the best short-clip moments in a video.

    Transcribes (cached) then asks DeepSeek to pick `count` clips for `platform`
    (youtube_shorts|instagram_reels|tiktok). Returns clips with start/end
    snapped to word boundaries, sorted by viral score.
    """
    transcript = _transcribe(video_path)
    return _find_highlights(transcript, platform, count, max_duration)


@mcp.tool()
def cut_clip(
    video_path: str,
    start: float,
    end: float,
    title: str = "",
    precise: bool = False,
) -> str:
    """Cut a single [start, end] clip from a video into outputs/.

    precise=False is an instant stream-copy (keyframe-aligned); precise=True
    re-encodes for frame-accurate boundaries. Returns the output file path.
    """
    return _cut_clip(video_path, start, end, title=title, precise=precise)


@mcp.tool()
def make_short(
    video_path: str,
    platform: str = "youtube_shorts",
    count: int = 5,
    max_duration: float = 60.0,
    vertical: bool = False,
    subtitles: bool = False,
    auto_edit: bool = False,
    jumpcut: bool = True,
    zoom: bool = True,
    sfx: bool = False,
    music_path: str = "",
    structure_aware: bool = True,
    tracked_reframe: bool = True,
    internal_transitions: bool = False,
    auto_soundbed: bool = True,
) -> dict:
    """End-to-end: transcribe, understand structure, pick clips, cut/edit. Manifest.

    structure_aware=True (default) builds a scene+speech+energy moment index and
    draws clips from real topic segments. Basic mode: vertical/subtitles only.
    auto_edit=True applies the full treatment per clip: silence trim (jumpcut),
    active-speaker tracked vertical reframe, optional intra-clip transitions,
    LLM-planned punch-in zoom, karaoke captions, auto energy-matched music +
    ambience (auto_soundbed) or a manual music_path, timed SFX, and fades.
    Requires an LLM key in .env.
    """
    return _make_short(
        video_path, platform, count, max_duration,
        precise=True, vertical=vertical, subtitles=subtitles,
        auto_edit=auto_edit, jumpcut=jumpcut, zoom=zoom, sfx=sfx,
        music_path=music_path or None,
        structure_aware=structure_aware, tracked_reframe=tracked_reframe,
        internal_transitions=internal_transitions, auto_soundbed=auto_soundbed,
    )


@mcp.tool()
def reframe_vertical(clip_path: str) -> str:
    """Crop a landscape clip to vertical 9:16 (1080x1920), centered on faces.

    Falls back to a center crop if no face is detected. Returns the new path.
    """
    return _reframe_vertical(clip_path)


@mcp.tool()
def burn_subtitles(clip_path: str, words: list[dict], clip_start: float = 0.0,
                   karaoke: bool = False) -> str:
    """Burn word-synced captions onto a clip.

    `words` is a list of {start, end, word} in source time; `clip_start` is the
    clip's offset in the source video. karaoke=True highlights each spoken word.
    Returns the captioned clip path.
    """
    return _burn_subtitles(clip_path, words, clip_start, karaoke=karaoke)


@mcp.tool()
def remove_silences(clip_path: str, words: list[dict], clip_start: float = 0.0,
                    max_pause: float = 0.5, keep_pause: float = 0.15) -> str:
    """Jump-cut: remove dead air longer than `max_pause` using word timings."""
    return _remove_silences(clip_path, words, clip_start, max_pause, keep_pause)


@mcp.tool()
def add_music(clip_path: str, music_path: str, music_volume: float = 0.18,
              duck: bool = True) -> str:
    """Mix a background music bed under the clip; duck=True dips it under speech."""
    return _add_music(clip_path, music_path, music_volume, duck)


@mcp.tool()
def normalize_loudness(clip_path: str) -> str:
    """Apply EBU R128 loudness normalization (platform-ready levels)."""
    return _normalize(clip_path)


@mcp.tool()
def punch_zoom(clip_path: str, windows: list[list[float]], zoom: float = 1.18) -> str:
    """Apply a punch-in zoom during each [start, end] window."""
    return _punch_zoom(clip_path, [(w[0], w[1]) for w in windows], zoom)


@mcp.tool()
def transition(clip_a: str, clip_b: str, kind: str = "fade",
               duration: float = 0.5) -> str:
    """Join clip_a -> clip_b with an xfade transition (fade|slideleft|wipeleft|...)."""
    return _transition(clip_a, clip_b, kind, duration)


@mcp.tool()
def fade_in_out(clip_path: str, fade: float = 0.4) -> str:
    """Fade from/to black at the clip's start and end (video + audio)."""
    return _fade(clip_path, fade)


@mcp.tool()
def add_sfx(clip_path: str, events: list[dict]) -> str:
    """Mix sound effects into a clip. events: [{time, path, volume}]."""
    return _add_sfx(clip_path, events)


@mcp.tool()
def analyze_structure(video_path: str, platform: str = "youtube_shorts") -> list[dict]:
    """Understand a video's structure: fuse scene cuts + audio energy + topic
    segmentation into scored moments (hook/flow/value). Returns the moment index."""
    return _analyze_structure(video_path, _transcribe(video_path), platform=platform)


@mcp.tool()
def reframe_vertical_tracked(clip_path: str) -> str:
    """Active-speaker tracked 9:16 reframe: classifies scene type and pans the crop
    to follow the speaker (eased), letterboxes screencasts. Returns the new path."""
    return _reframe_tracked(clip_path)


def _selftest(video_path: str) -> None:
    print("[selftest] ping ->", ping())
    print(f"[selftest] session-aware editing tools registered -> {_SESSION_TOOLS}")
    print(f"[selftest] media_info({video_path}) ->")
    info = media_info(video_path)
    for k, v in info.items():
        print(f"    {k}: {v}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        _selftest(sys.argv[2])
    else:
        mcp.run()
