"""NLE timeline export (Pro Faz 6) — DaVinci Resolve first-class.

Exports a clip's edit decisions as an editable timeline referencing the ORIGINAL
source file, so a pro editor can relink and refine in an NLE. Two formats, both
hand-written (no deps):

  - FCP7 XML ("xmeml" v4): Resolve & Premiere both import it. Carries cut
    clipitems (video + linked audio) and sequence markers. When the clip was
    reframed (e.g. 9:16), the sequence opens at that canvas and each clip gets a
    best-effort Basic Motion scale-to-fill so the footage lands center-cropped to
    the vertical frame — a sane starting point the editor refines (the exact
    tracked pan is render-side and not reproduced here).
  - CMX3600 EDL: universal fallback. Cuts as B (V+A1) events, markers as
    `* LOC:` comment lines (Resolve reads them).

Faz 0.3: alongside the timeline we also write a caption .srt sidecar (the SAME
segmenter as the burned captions, in the burned language) so the editor doesn't
re-type the words. The burned-in looks/zooms still travel as the rendered MP4.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from pipeline.timebase import Timebase

_EDL_COLORS = {"green": "GREEN", "red": "RED", "blue": "BLUE",
               "yellow": "YELLOW", "cyan": "CYAN", "magenta": "MAGENTA"}


def _marker_color(m: dict) -> str:
    return _EDL_COLORS.get(str(m.get("color", "")).lower(), "YELLOW")


# ----------------------------------------------------------------- FCP7 XML
def _xml_rate(tb: Timebase) -> str:
    ntsc = "TRUE" if tb.den == 1001 else "FALSE"
    return (f"<rate><timebase>{tb.nominal}</timebase>"
            f"<ntsc>{ntsc}</ntsc></rate>")


def _fill_scale(sw: int, sh: int, w: int, h: int) -> float:
    """FCP7 Basic Motion scale (%) that makes a source (sw×sh) FILL a frame
    (w×h), center-cropping the overflow — the reframe approximation. scale=100 is
    the source at native size; max(w/sw, h/sh) fills both axes."""
    if sw <= 0 or sh <= 0:
        return 100.0
    return round(max(w / sw, h / sh) * 100.0, 2)


def _basic_motion(scale: float) -> str:
    """A Basic Motion filter pinning Scale (centered). Premiere & Resolve both
    read the FCP7 `basic`/motion effect; center stays (0,0) for a center crop."""
    return (
        "<filter><effect><name>Basic Motion</name><effectid>basic</effectid>"
        "<effectcategory>motion</effectcategory><effecttype>motion</effecttype>"
        "<mediatype>video</mediatype>"
        "<parameter><parameterid>scale</parameterid><name>Scale</name>"
        f"<min>0</min><max>1000</max><value>{scale}</value></parameter>"
        "<parameter><parameterid>center</parameterid><name>Center</name>"
        "<value><horiz>0</horiz><vert>0</vert></value></parameter>"
        "<parameter><parameterid>rotation</parameterid><name>Rotation</name>"
        "<value>0</value></parameter></effect></filter>"
    )


def to_xmeml(name: str, source: dict, kept: list[tuple[float, float]],
             markers: list[dict], tb: Timebase,
             canvas: "tuple[int, int] | None" = None) -> str:
    """FCP7 XML v4 sequence: one video + one linked audio track of cuts.

    canvas=(w,h) sets the sequence/output frame (e.g. 1080×1920 for a reframed
    9:16 clip); when it differs from the source video size each video clipitem
    gets a Basic Motion scale-to-fill so the footage center-crops to that frame.
    Default (canvas=None) keeps the sequence at the source size — byte-for-byte
    the historical timing-only export.
    """
    rate = _xml_rate(tb)
    src_path = Path(source["path"]).resolve()
    pathurl = "file://" + quote(str(src_path))
    src_frames = tb.to_frames(float(source.get("duration", 0.0)))
    total = sum(e - s for s, e in kept)

    sw, sh = int(source.get("width", 1920)), int(source.get("height", 1080))
    seq_w, seq_h = canvas if canvas else (sw, sh)
    # Scale-to-fill only when the output frame differs from the source.
    motion = ""
    if (seq_w, seq_h) != (sw, sh):
        motion = _basic_motion(_fill_scale(sw, sh, seq_w, seq_h))

    file_full = (
        f'<file id="src1"><name>{escape(src_path.name)}</name>'
        f"<pathurl>{escape(pathurl)}</pathurl>{rate}"
        f"<duration>{src_frames}</duration>"
        f"<media><video><samplecharacteristics>"
        f"<width>{sw}</width><height>{sh}</height>"
        f"</samplecharacteristics></video>"
        f"<audio><channelcount>2</channelcount></audio></media></file>"
    )

    vitems, aitems = [], []
    rec = 0.0
    for i, (s, e) in enumerate(kept, 1):
        sf, ef = tb.to_frames(s), tb.to_frames(e)
        rf, rf2 = tb.to_frames(rec), tb.to_frames(rec + (e - s))
        fileref = file_full if i == 1 else '<file id="src1"/>'
        common = (
            f"<name>{escape(name)} {i:02d}</name><enabled>TRUE</enabled>"
            f"<duration>{ef - sf}</duration>{rate}"
            f"<start>{rf}</start><end>{rf2}</end>"
            f"<in>{sf}</in><out>{ef}</out>"
        )
        vitems.append(
            f'<clipitem id="v{i}">{common}{fileref}'
            f'<link><linkclipref>v{i}</linkclipref><mediatype>video</mediatype>'
            f"<trackindex>1</trackindex><clipindex>{i}</clipindex></link>"
            f'<link><linkclipref>a{i}</linkclipref><mediatype>audio</mediatype>'
            f"<trackindex>1</trackindex><clipindex>{i}</clipindex></link>"
            f"{motion}</clipitem>")
        aitems.append(
            f'<clipitem id="a{i}">{common}<file id="src1"/>'
            f"<sourcetrack><mediatype>audio</mediatype>"
            f"<trackindex>1</trackindex></sourcetrack></clipitem>")
        rec += e - s

    seq_markers = "".join(
        f"<marker><name>{escape(str(m.get('label', 'marker')))}</name>"
        f"<comment/><in>{tb.to_frames(float(m['t']))}</in><out>-1</out>"
        f"</marker>"
        for m in markers)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
        f'<xmeml version="4"><sequence id="seq1">'
        f"<name>{escape(name)}</name>"
        f"<duration>{tb.to_frames(total)}</duration>{rate}"
        f"<timecode>{rate}<string>00:00:00:00</string>"
        f"<frame>0</frame><displayformat>NDF</displayformat></timecode>"
        f"<media><video><format><samplecharacteristics>"
        f"<width>{seq_w}</width><height>{seq_h}</height>"
        f"</samplecharacteristics></format>"
        f"<track>{''.join(vitems)}</track></video>"
        f"<audio><track>{''.join(aitems)}</track></audio></media>"
        f"{seq_markers}</sequence></xmeml>\n"
    )


# ----------------------------------------------------------------- EDL
def to_edl(name: str, source: dict, kept: list[tuple[float, float]],
           markers: list[dict], tb: Timebase) -> str:
    """CMX3600 EDL: B (V+A1) cut events + LOC marker comments."""
    lines = [f"TITLE: {name}", "FCM: NON-DROP FRAME", ""]
    rec = 0.0
    src_name = Path(source["path"]).name
    for i, (s, e) in enumerate(kept, 1):
        lines.append(
            f"{i:03d}  AX       B     C        "
            f"{tb.tc(s)} {tb.tc(e)} {tb.tc(rec)} {tb.tc(rec + (e - s))}")
        lines.append(f"* FROM CLIP NAME: {src_name}")
        rec += e - s
    if markers:
        lines.append("")
        for m in markers:
            label = str(m.get("label", "marker")).replace("\n", " ")
            lines.append(f"* LOC: {tb.tc(float(m['t']))} "
                         f"{_marker_color(m)} {label}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------- caption sidecar
def _reframe_canvas(clip: dict) -> "tuple[int, int] | None":
    """The output (w,h) the clip was reframed to, or None if it wasn't. Read
    from the reframe stage's aspect via the same ASPECTS table the renderer uses,
    so the exported sequence opens at the short's real frame size."""
    params = next((st["params"] for st in clip.get("stages", [])
                   if st["name"] == "reframe"), None)
    if params is None:
        return None
    from pipeline.tracking import ASPECTS
    spec = ASPECTS.get(params.get("aspect", "9:16"))
    return (int(spec[1]), int(spec[2])) if spec else None


def write_caption_sidecar(session, clip_id: int) -> "Path | None":
    """Write a clipNN.srt next to the timeline, matching the burned captions
    (same segmenter + language). Returns the path, or None if there are no words.
    Never raises — a caption failure must not sink the timeline export."""
    try:
        from pipeline.captions import build_caption_segments, to_srt
        clip = session.clip(clip_id)
        words = session.words_for(clip)
        if not words:
            return None
        lang = next((st["params"].get("lang") for st in clip.get("stages", [])
                     if st["name"] == "subtitles"), None)
        if lang:
            from pipeline.translate import translate_captions
            words = translate_captions(words, lang)
        segments = build_caption_segments(words, clip_start=0.0)
        if not segments:
            return None
        out = session.workdir / f"clip{clip_id:02d}.srt"
        out.write_text(to_srt(segments), encoding="utf-8")
        return out
    except Exception:  # noqa: BLE001 — sidecar is additive, never a hard gate
        return None


# ----------------------------------------------------------------- entry
def export_timeline(session, clip_id: int, fmt: str) -> Path:
    """Write clip's timeline as .xml (xmeml) or .edl into the workdir.

    Also writes a clipNN.srt caption sidecar next to it (best-effort). The xmeml
    sequence opens at the reframed canvas with a scale-to-fill so the framing
    survives the round-trip; the EDL stays timing-only.
    """
    fmt = fmt.lower().lstrip(".")
    if fmt not in ("xml", "edl"):
        raise ValueError("format must be 'xml' or 'edl'.")
    clip = session.clip(clip_id)
    tmap = session.timemap_for(clip)
    kept = tmap.kept_spans()
    if not kept:
        raise ValueError("Clip has no kept timing spans to export.")
    source = dict(session.data["source"])
    # Own-clips: each clip references its OWN footage as the reel in the XML/EDL,
    # not the nominal shared source. Legacy/long-video clips have no source_path.
    if clip.get("source_path"):
        from pipeline.media import ffprobe_info
        source = {**source, "path": clip["source_path"],
                  **ffprobe_info(clip["source_path"])}
    tb = Timebase.from_rate(str(source.get("fps", 30)))
    name = f"clip{clip_id:02d}_{clip.get('title', 'timeline')}"[:60]
    markers = clip.get("markers", [])
    if fmt == "xml":
        text = to_xmeml(name, source, kept, markers, tb,
                        canvas=_reframe_canvas(clip))
    else:
        text = to_edl(name, source, kept, markers, tb)
    out = session.workdir / f"clip{clip_id:02d}_timeline.{fmt}"
    out.write_text(text, encoding="utf-8")
    write_caption_sidecar(session, clip_id)  # additive, best-effort
    return out
