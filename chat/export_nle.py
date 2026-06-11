"""NLE timeline export (Pro Faz 6) — DaVinci Resolve first-class.

Exports a clip's TIMING decisions (cuts + markers) as an editable timeline
referencing the ORIGINAL source file, so a pro editor can relink and refine
in an NLE. Two formats, both hand-written (no deps):

  - FCP7 XML ("xmeml" v4): Resolve & Premiere both import it. Carries cut
    clipitems (video + linked audio) and sequence markers.
  - CMX3600 EDL: universal fallback. Cuts as B (V+A1) events, markers as
    `* LOC:` comment lines (Resolve reads them).

Only timing + markers export — looks/zooms/captions are render-side polish
that a pro re-does in the NLE; the burned MP4 travels separately.
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


def to_xmeml(name: str, source: dict, kept: list[tuple[float, float]],
             markers: list[dict], tb: Timebase) -> str:
    """FCP7 XML v4 sequence: one video + one linked audio track of cuts."""
    rate = _xml_rate(tb)
    src_path = Path(source["path"]).resolve()
    pathurl = "file://" + quote(str(src_path))
    src_frames = tb.to_frames(float(source.get("duration", 0.0)))
    total = sum(e - s for s, e in kept)

    file_full = (
        f'<file id="src1"><name>{escape(src_path.name)}</name>'
        f"<pathurl>{escape(pathurl)}</pathurl>{rate}"
        f"<duration>{src_frames}</duration>"
        f"<media><video><samplecharacteristics>"
        f"<width>{source.get('width', 1920)}</width>"
        f"<height>{source.get('height', 1080)}</height>"
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
            f"</clipitem>")
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
        f"<width>{source.get('width', 1920)}</width>"
        f"<height>{source.get('height', 1080)}</height>"
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


# ----------------------------------------------------------------- entry
def export_timeline(session, clip_id: int, fmt: str) -> Path:
    """Write clip's timeline as .xml (xmeml) or .edl into the workdir."""
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
    text = (to_xmeml(name, source, kept, markers, tb) if fmt == "xml"
            else to_edl(name, source, kept, markers, tb))
    out = session.workdir / f"clip{clip_id:02d}_timeline.{fmt}"
    out.write_text(text, encoding="utf-8")
    return out
