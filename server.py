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

mcp = FastMCP("shorts")


@mcp.tool()
def ping() -> str:
    """Comprobación de salud. Devuelve 'pong' si el servidor está vivo."""
    return "pong"


@mcp.tool()
def media_info(video_path: str) -> dict:
    """Sonda un archivo de vídeo y devuelve duración, resolución, fps y códec."""
    return ffprobe_info(video_path)


@mcp.tool()
def transcribe(video_path: str, model_size: str = "") -> dict:
    """Transcribe un vídeo a texto con timestamps por palabra (cacheado).

    Devuelve {language, duration, segments[], words[]}. `model_size` sobrescribe
    el modelo Whisper configurado (tiny|base|small|medium|large-v3).
    """
    return _transcribe(video_path, model_size or None)


@mcp.tool()
def find_highlights(
    video_path: str,
    platform: str = "youtube_shorts",
    count: int = 5,
    max_duration: float = 60.0,
) -> list[dict]:
    """Encuentra los mejores momentos para clips cortos en un vídeo.

    Transcribe (cacheado) y pide a DeepSeek que elija `count` clips para `platform`
    (youtube_shorts|instagram_reels|tiktok). Devuelve clips con inicio/fin
    ajustados a límites de palabra, ordenados por puntuación viral.
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
    """Corta un clip [start, end] de un vídeo a outputs/.

    precise=False es stream-copy instantáneo (alineado a keyframes); precise=True
    re-codifica para límites frame-exactos. Devuelve la ruta del archivo de salida.
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
    """End-to-end: transcribe, entiende estructura, elige clips, corta/edita. Manifiesto.

    structure_aware=True (por defecto) construye un índice de momentos escena+voz+energía
    y saca clips de segmentos de tema reales. Modo básico: solo vertical/subtítulos.
    auto_edit=True aplica el tratamiento completo por clip: recorte de silencios (jumpcut),
    reencuadre vertical tracked con speaker activo, transiciones intra-clip opcionales,
    zoom punch-in planificado por LLM, subtítulos karaoke, música auto a juego de energía +
    ambience (auto_soundbed) o music_path manual, SFX temporizados, y fades.
    Requiere clave LLM en .env.
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
    """Recorta un clip landscape a vertical 9:16 (1080x1920), centrado en caras.

    Fallback a center crop si no se detecta cara. Devuelve la nueva ruta.
    """
    return _reframe_vertical(clip_path)


@mcp.tool()
def burn_subtitles(clip_path: str, words: list[dict], clip_start: float = 0.0,
                   karaoke: bool = False) -> str:
    """Quema subtítulos sincronizados por palabra en un clip.

    `words` es una lista de {start, end, word} en tiempo del source; `clip_start` es
    el offset del clip en el vídeo fuente. karaoke=True resalta cada palabra hablada.
    Devuelve la ruta del clip subtitulado.
    """
    return _burn_subtitles(clip_path, words, clip_start, karaoke=karaoke)


@mcp.tool()
def remove_silences(clip_path: str, words: list[dict], clip_start: float = 0.0,
                    max_pause: float = 0.5, keep_pause: float = 0.15) -> str:
    """Jump-cut: elimina aire muerto más largo que `max_pause` usando timings de palabras."""
    return _remove_silences(clip_path, words, clip_start, max_pause, keep_pause)


@mcp.tool()
def add_music(clip_path: str, music_path: str, music_volume: float = 0.18,
              duck: bool = True) -> str:
    """Mezcla una cama de música de fondo bajo el clip; duck=True la baja bajo la voz."""
    return _add_music(clip_path, music_path, music_volume, duck)


@mcp.tool()
def normalize_loudness(clip_path: str) -> str:
    """Aplica normalización de sonoridad EBU R128 (niveles listos para plataforma)."""
    return _normalize(clip_path)


@mcp.tool()
def punch_zoom(clip_path: str, windows: list[list[float]], zoom: float = 1.18) -> str:
    """Aplica zoom punch-in durante cada ventana [start, end]."""
    return _punch_zoom(clip_path, [(w[0], w[1]) for w in windows], zoom)


@mcp.tool()
def transition(clip_a: str, clip_b: str, kind: str = "fade",
               duration: float = 0.5) -> str:
    """Une clip_a -> clip_b con transición xfade (fade|slideleft|wipeleft|...)."""
    return _transition(clip_a, clip_b, kind, duration)


@mcp.tool()
def fade_in_out(clip_path: str, fade: float = 0.4) -> str:
    """Fade a/desde negro al inicio y final del clip (vídeo + audio)."""
    return _fade(clip_path, fade)


@mcp.tool()
def add_sfx(clip_path: str, events: list[dict]) -> str:
    """Mezcla efectos de sonido en un clip. events: [{time, path, volume}]."""
    return _add_sfx(clip_path, events)


@mcp.tool()
def analyze_structure(video_path: str, platform: str = "youtube_shorts") -> list[dict]:
    """Entiende la estructura de un vídeo: fusiona cortes de escena + energía de audio +
    segmentación de temas en momentos puntuados (hook/flow/valor). Devuelve el índice de momentos."""
    return _analyze_structure(video_path, _transcribe(video_path), platform=platform)


@mcp.tool()
def reframe_vertical_tracked(clip_path: str) -> str:
    """Reencuadre vertical 9:16 con tracking de speaker activo: clasifica tipo de escena
    y panea el crop siguiendo al hablante (suavizado), letterboxea screencasts. Devuelve la nueva ruta."""
    return _reframe_tracked(clip_path)


def _selftest(video_path: str) -> None:
    print("[selftest] ping ->", ping())
    print(f"[selftest] media_info({video_path}) ->")
    info = media_info(video_path)
    for k, v in info.items():
        print(f"    {k}: {v}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        _selftest(sys.argv[2])
    else:
        mcp.run()