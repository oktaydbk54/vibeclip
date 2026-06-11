"""Serialize a clip's edit stack into multi-track timeline JSON.

Pure derivative of the session state — reads stage params (all already in
clip-local / player seconds) and emits tracks the UI draws. No rendering, no
transcription beyond the cached words the caption track needs.
"""

from __future__ import annotations

from pathlib import Path

# Track display order (top → bottom) and labels.
TRACK_LABELS = {
    "zoom": "Zoom", "splitscreen": "Gameplay BG", "broll": "B-roll",
    "overlay": "Overlay", "brand": "Brand",
    "subtitles": "Captions", "fx": "FX", "sfx": "SFX", "music": "Music",
    "ambience": "Ambience", "fade": "Master",
}
TRACK_ORDER = list(TRACK_LABELS.keys())


def _stage(clip: dict, name: str) -> dict | None:
    return next((st for st in clip["stages"] if st["name"] == name), None)


def _basename(path: str) -> str:
    return Path(path).stem if path else ""


def serialize(clip: dict, words: list[dict], duration: float,
              fps: float, speed: float = 1.0) -> dict:
    """Build the timeline payload for one clip.

    `speed` is the clip's constant-speed factor: every track except captions
    already stores its times in the sped (player) timeline, but captions derive
    from PRE-speed words, so their segment times are divided by `speed` here.
    """
    from pipeline.captions import build_caption_segments

    tracks: list[dict] = []

    def add(key: str, kind: str, items: list[dict],
            editable: bool = False) -> None:
        if items:
            if editable:
                for i, it in enumerate(items):
                    it["idx"] = i
            tracks.append({"key": key, "label": TRACK_LABELS[key],
                           "kind": kind, "items": items, "editable": editable})

    # zoom — range items, label = strength (editable: drag/resize/retune)
    z = _stage(clip, "zoom")
    if z:
        _MGLYPH = {"center": "", "left": " ←", "right": " →",
                   "up": " ↑", "down": " ↓"}
        items = []
        for w in z["params"].get("windows", []):
            motion = w[3] if len(w) > 3 else "center"
            items.append({"start": w[0], "end": w[1],
                          "label": f"{w[2]:.2f}×{_MGLYPH.get(motion, '')}",
                          "value": round(w[2], 3), "motion": motion})
        add("zoom", "range", items, editable=True)

    # splitscreen — whole-clip span, label = pack name
    ss = _stage(clip, "splitscreen")
    if ss and ss["params"].get("path"):
        pack = ss["params"].get("pack", "gameplay")
        top = ss["params"].get("top_ratio", 0.6)
        add("splitscreen", "span", [{"start": 0.0, "end": duration,
                                     "label": f"{pack} · {int(top*100)}/"
                                              f"{int(round((1-top)*100))}"}])

    # broll — range items, label = query (editable)
    b = _stage(clip, "broll")
    if b:
        add("broll", "range", [
            {"start": e["start"], "end": e["end"],
             "label": e.get("query", "b-roll")}
            for e in b["params"].get("events", [])], editable=True)

    # overlay — range items, label = type (editable)
    o = _stage(clip, "overlay")
    if o:
        add("overlay", "range", [
            {"start": e.get("start", 0.0),
             "end": e.get("end", e.get("start", 0.0) + 0.4),
             "label": e.get("type", "overlay"),
             "value": round(e.get("opacity", 1.0), 3)}
            for e in o["params"].get("events", [])], editable=True)

    # brand — title (range at head) + watermark (span)
    br = _stage(clip, "brand")
    if br:
        items = []
        t = br["params"].get("title")
        if t:
            items.append({"start": 0.0, "end": t.get("duration", 2.0),
                          "label": t.get("text", "title")[:18]})
        wm = br["params"].get("watermark")
        if wm:
            items.append({"start": 0.0, "end": duration,
                          "label": "logo", "span": True})
        add("brand", "range", items)

    # subtitles — caption segments derived from words (PRE-speed → ÷ speed)
    if _stage(clip, "subtitles") and words:
        sp = speed if speed and speed > 0 else 1.0
        segs = build_caption_segments(words)
        add("subtitles", "range", [
            {"start": s["start"] / sp, "end": s["end"] / sp,
             "label": s["text"][:22]}
            for s in segs])

    # fx — point items (flash/shake), label = kind (editable: drag/delete)
    f = _stage(clip, "fx")
    if f:
        add("fx", "point", [
            {"start": e["time"], "label": e.get("kind", "fx")}
            for e in f["params"].get("events", [])], editable=True)

    # sfx — point items, label = sound name (editable)
    s = _stage(clip, "sfx")
    if s:
        add("sfx", "point", [
            {"start": e["time"],
             "label": _basename(e.get("path", "")) or "sfx",
             "value": round(e.get("volume", 1.0), 3)}
            for e in s["params"].get("events", [])], editable=True)

    # music / ambience — whole-clip spans
    m = _stage(clip, "music")
    if m and m["params"].get("path"):
        add("music", "span", [{"start": 0.0, "end": duration,
                               "label": _basename(m["params"]["path"])}])
    am = _stage(clip, "ambience")
    if am and am["params"].get("path"):
        add("ambience", "span", [{"start": 0.0, "end": duration,
                                  "label": _basename(am["params"]["path"])}])

    # fade — span, the final loudness master
    fd = _stage(clip, "fade")
    if fd:
        add("fade", "span", [{"start": 0.0, "end": duration,
                              "label": "loudnorm"}])

    return {
        "clip_id": clip["id"],
        "duration": round(duration, 3),
        "fps": fps,
        "locked": bool(clip.get("locked")),
        "tracks": tracks,
        "markers": clip.get("markers", []),
    }
