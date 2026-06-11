"""Speech denoise stage (Pro Faz 6) — ffmpeg afftdn, audio-only.

Sits right after the timing stages in CANONICAL so every downstream audio
stage (music ducking, sfx mix, loudnorm) hears clean speech. Video stream is
stream-copied — the stage costs an audio re-encode only.

afftdn (FFT denoiser) ships in every ffmpeg build (no model files, unlike
arnndn) and handles steady background noise (room tone, hiss, hum) well.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.media import run_ffmpeg

# nr = noise reduction amount in dB; tracked noise floor adapts per-window.
STRENGTHS = {"light": 9, "medium": 14, "strong": 22}


def denoise_audio(clip_path: str, strength: str = "medium",
                  out_path: str | None = None) -> str:
    """Reduce steady background noise in the clip's speech track."""
    nr = STRENGTHS.get(strength, STRENGTHS["medium"])
    src = Path(clip_path)
    out = out_path or str(src.with_name(src.stem + "_denoise.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-af", f"afftdn=nr={nr}:nf=-30:tn=1",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        str(Path(out).resolve()),
    ])
    return out
