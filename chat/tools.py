"""Chat tool registry: OpenAI function-calling specs + implementations.

Each impl is `fn(session, **args) -> dict` returning compact JSON the model can
narrate from. Mutating tools snapshot the session first so `undo` works.
"""

from __future__ import annotations

from pathlib import Path

from chat.session import Session, open_in_player

DEFAULT_STAGES = ("jumpcut", "reframe", "subtitles")

# While a plan awaits approval these are blocked, so the model can't "helpfully"
# replay plan steps one by one and break single-undo atomicity.
MUTATING_TOOLS = frozenset({
    "generate_clips", "set_music", "set_subtitles", "add_zoom",
    "cut_silences", "set_fade", "add_sound_effect", "apply_style",
    "remove_fillers", "remove_section", "set_speed", "set_cut", "auto_zoom",
    "add_broll", "set_watermark", "set_title_card",
    "add_meme_text", "duplicate_clip", "pick_variant", "join_clips",
    "set_look", "add_overlay", "add_reaction", "add_sticker", "add_emphasis",
    "auto_pace", "set_loudness", "add_gameplay_background", "fix_transcript",
    "edit_event", "delete_event",
    "set_aspect",  # reframe_aspect_tool: re-reframes the canvas (a render)
    "remove_phrase",  # transcript_timeline: content-described trim (a render)
    "set_caption_language",  # translated captions (re-renders subtitles)
    "set_dub",  # translated voice-over (re-renders the dub stage + audio tail)
})

# Valid export aspect ratios (mirrors pipeline.tracking.ASPECTS keys).
ASPECT_CHOICES = ("9:16", "1:1", "16:9")


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _lang_slug(lang: str) -> str:
    """A filesystem-safe token for a language label, for sidecar filenames
    (e.g. 'Spanish'->'spanish', 'pt-BR'->'pt-br'). Falls back to 'xx' if the
    label has no usable alphanumerics."""
    s = "".join(c if c.isalnum() else "-" for c in (lang or "").lower())
    s = "-".join(p for p in s.split("-") if p)
    return s or "xx"


# ----------------------------------------------------------------- impls
# Adaptive candidate-count bounds (Phase 2). A 30-min source should surface
# ~10-20 ranked candidates; a short demo clip should surface only a few.
_CAND_MIN = 5
_CAND_MAX = 20
_SECONDS_PER_CANDIDATE = 105.0  # ~1 candidate per 90-120s of source


def adaptive_candidate_count(duration_s: float,
                             min_n: int = _CAND_MIN,
                             max_n: int = _CAND_MAX) -> int:
    """How many candidates to surface for a source of `duration_s` seconds.

    ~1 candidate per `_SECONDS_PER_CANDIDATE`s of source, clamped to
    [min_n, max_n]. A very short clip (e.g. the 42s demo) returns a small
    number; a 30-min source returns ~17 -> clamped into 10-20 range.
    Sources shorter than one full window still get at least 1 candidate, but
    never fewer than the clamp floor unless the source is genuinely tiny.
    """
    if duration_s <= 0:
        return min_n
    raw = max(1, round(duration_s / _SECONDS_PER_CANDIDATE))
    # For sub-window sources (shorter than min_n windows) don't force min_n —
    # there isn't enough material; cap at raw. Otherwise clamp into [min,max].
    if raw < min_n:
        return raw
    return max(min_n, min(max_n, raw))


def generate_clips(session: Session, count: int | None = None,
                   max_duration: float | None = None,
                   model: str | None = None) -> dict:
    """count=None (default): the candidate count adapts to the source duration
    (~1 per 90-120s, clamped to 5..20). Pass an explicit int when the user/LLM
    asks for a specific number (e.g. "give me 3 clips").

    max_duration=None (default): the LLM reads the content's structure and
    picks each clip's natural length — no fixed cap. Pass a number only when
    the user explicitly asks for one ("en fazla 30 saniye").

    model: optional faster-whisper size override for the ANALYSIS transcription
    (e.g. 'tiny'/'base' for a quick first candidate list). None keeps the
    configured default (config.WHISPER_MODEL). Each size caches under its own
    key, so a later default-model pass for final captions is independent."""
    from pipeline.highlights import find_highlights
    from pipeline.transcribe import transcribe

    # Own-clips guard: these projects hold the user's already-finished uploaded
    # clips. Auto-clipping would wipe session.data['clips'] and replace them with
    # candidates cut from the (nominal) shared source — refuse BEFORE that wipe.
    if session.data.get("intake", {}).get("mode") == "own_clips":
        return _err("This project uses your own uploaded clips — "
                    "auto-clipping would replace them.")

    session.snapshot()
    # ANALYSIS pass: transcribe the cheap 540p proxy, not the full-res source.
    # Phase 0 builds the proxy with the source's exact fps/timebase preserved
    # 1:1, so a word timestamp computed on the proxy maps onto the source with
    # no drift — clip cuts (which run against source['path']) stay correct.
    # proxy_or_source() returns the source if the proxy isn't built yet, so
    # pre-proxy sessions still work. The fast batched path drops wall-time.
    src = session.proxy_or_source()
    transcript = transcribe(src, model_size=model, batched=True)

    # Adaptive candidate count when the caller didn't pin one. Source duration
    # comes from the transcript (proxy timing is 1:1 with the source).
    if count is None:
        count = adaptive_candidate_count(float(transcript.get("duration", 0.0)))

    structure = None
    try:
        from pipeline.structure import analyze_structure
        structure = analyze_structure(src, transcript,
                                      platform=session.data["platform"])
    except Exception:
        pass

    found = find_highlights(transcript, session.data["platform"],
                            count, max_duration, structure=structure)
    if not found:
        return _err("No clip-worthy moments found.")

    session.data["clips"] = []
    results = []
    for i, c in enumerate(found, start=1):
        clip = {"id": i, "title": c["title"], "start": c["start"],
                "end": c["end"], "score": c["score"], "stages": [],
                "current": None,
                # Phase 3 — review-queue status (additive; old sessions without
                # it read as "pending" via Session.clip_status).
                "status": "pending",
                # virality transparency for the clip card (A3) — copied from
                # find_highlights; absent fields degrade gracefully in the UI.
                "hook": c.get("hook", ""), "reason": c.get("reason", ""),
                "scores": c.get("scores")}
        session.data["clips"].append(clip)
        # LAZY: record the stage recipe (params only) WITHOUT rendering. A 20-min
        # source yields its full candidate list in seconds; each clip's cut→
        # default-stage chain is materialized on first open via render_clip (or
        # on its first edit, which set_stages replays from cut). Old behaviour
        # eagerly encoded every clip here — 12 clips × 4 stages ≈ 23 min.
        clip["stages"] = [{"name": "cut",
                           "params": {"start": c["start"], "end": c["end"]}}]
        for stage in DEFAULT_STAGES:
            clip["stages"].append({"name": stage, "params": {}})
        results.append({"id": i, "title": c["title"], "score": c["score"],
                        "range": f"{c['start']:.1f}-{c['end']:.1f}s",
                        "file": None})
    session.save()
    return _ok(clips=results)


def render_clip(session: Session, clip_id: int, upto: str = "") -> dict:
    """Materialize a lazily-created clip — render its recorded stage recipe
    (cut→jumpcut→reframe→subtitles) against the proxy so it becomes playable.
    generate_clips no longer renders up front; the studio calls this the first
    time a clip is opened. Idempotent (an already-rendered clip is a cache hit).
    Not A/B-gated and takes no undo snapshot — it produces the clip's baseline,
    it doesn't change an approved edit.

    `upto` (optional stage name, e.g. "reframe") renders only up to that stage
    for a fast captionless preview; a follow-up call with no upto finishes the
    deferred tail, re-using the head stages from cache. Progressive open."""
    try:
        out = session.render_clip(clip_id, upto=upto or None)
    except ValueError as e:
        return _err(str(e))
    return _ok(file=out, clip_id=clip_id)


def list_clips(session: Session) -> dict:
    return _ok(state=session.summary())


def set_clip_status(session: Session, clip_id: int, status: str) -> dict:
    """Set a clip's review-queue status (Phase 3): pending|approved|skipped|
    exported. Skipping prunes a candidate from the top of the queue WITHOUT
    deleting its render artifacts — it just sets the flag and saves. This is a
    bookkeeping change, not a render, so it does NOT go through the A/B gate or
    take an undo snapshot."""
    status = (status or "").strip().lower()
    if status not in Session.CLIP_STATUSES:
        return _err("status must be one of "
                    + "|".join(Session.CLIP_STATUSES))
    clip = session.clip(clip_id)          # raises ValueError on unknown id
    clip["status"] = status
    session.save()
    return _ok(clip_id=clip_id, status=status)


def preview_clip(session: Session, clip_id: int) -> dict:
    clip = session.clip(clip_id)
    if not clip.get("current"):
        return _err(f"Clip #{clip_id} has no rendered file yet.")
    open_in_player(clip["current"])
    return _ok(msg=f"Opened clip #{clip_id} in the player.",
               file=clip["current"])


def _resolve_track(name_or_path: str) -> str:
    """Accept an absolute path OR a bare track name from the library."""
    if not name_or_path or Path(name_or_path).exists():
        return name_or_path
    from pipeline import config
    hits = list((config.ROOT / "assets").rglob(Path(name_or_path).name))
    return str(hits[0]) if hits else name_or_path


def set_music(session: Session, clip_id: int, mood: str = "",
              file: str = "", volume: float = 0.18) -> dict:
    session.snapshot()
    clip = session.clip(clip_id)
    music = _resolve_track(file)
    if not music:
        from pipeline.soundbed import select_music
        ref = clip.get("current") or clip["stages"][-1]["output"]
        music = select_music(ref, mood_hint=mood or None)
    if not music or not Path(music).exists():
        return _err("No matching music track found. Drop files into "
                    "assets/music/{calm,neutral,energetic}/ or pass a file path.")
    out = session.set_stage(clip_id, "music", {"path": music, "volume": volume})
    return _ok(file=out, track=Path(music).name, mood=mood or "auto")


def set_subtitles(session: Session, clip_id: int, karaoke: bool | None = None,
                  scale: float | None = None, y_ratio: float | None = None,
                  text_color: str = "", highlight_color: str = "",
                  **extra) -> dict:
    """Merge over the EXISTING subtitle params so a partial ask ("altyazıyı
    büyüt") keeps the current style's font/stroke/colors. **extra absorbs
    style-level keys (font, stroke, uppercase…) the planner may echo back."""
    session.snapshot()
    clip = session.clip(clip_id)
    params = dict(next((st["params"] for st in clip["stages"]
                        if st["name"] == "subtitles"), {}))
    if karaoke is not None:
        params["karaoke"] = karaoke
    if scale is not None:
        params["scale"] = scale
    if y_ratio is not None:
        params["y_ratio"] = y_ratio
    if text_color:
        params["text_color"] = text_color
    if highlight_color:
        params["highlight_color"] = highlight_color
    params.update({k: v for k, v in extra.items() if v is not None})
    params.setdefault("karaoke", True)
    params.setdefault("scale", 1.0)
    params.setdefault("y_ratio", 0.68)
    out = session.set_stage(clip_id, "subtitles", params)
    return _ok(file=out, **{k: params[k] for k in ("karaoke", "scale", "y_ratio")})


def list_music(session: Session) -> dict:
    """Enumerate the local music + ambience library so the agent can suggest."""
    from pipeline import config

    lib: dict[str, list[str]] = {}
    music_root = config.ROOT / "assets" / "music"
    if music_root.exists():
        for bucket in sorted(d for d in music_root.iterdir() if d.is_dir()):
            tracks = sorted(f.name for f in bucket.iterdir()
                            if f.suffix.lower() in
                            (".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg"))
            if tracks:
                lib[bucket.name] = tracks
    amb_root = config.ROOT / "assets" / "ambience"
    ambience = sorted(f.name for f in amb_root.iterdir()
                      if f.is_file()) if amb_root.exists() else []
    if not lib and not ambience:
        return _err("Music library is empty. Drop files into "
                    "assets/music/{calm,neutral,energetic}/ and assets/ambience/.")
    return _ok(music=lib, ambience=ambience)


ZOOM_MOTIONS = ("center", "left", "right", "up", "down")


def add_zoom(session: Session, clip_id: int, time: float,
             duration: float = 1.5, strength: float = 1.18,
             motion: str = "center") -> dict:
    # 1.0 means "no zoom" — models sometimes send it for "subtle". Clamp to a
    # visible-but-tasteful range. motion = Ken-Burns drift direction.
    strength = max(1.08, min(1.5, float(strength)))
    motion = motion if motion in ZOOM_MOTIONS else "center"
    session.snapshot()
    clip = session.clip(clip_id)
    existing = next((st["params"].get("windows", [])
                     for st in clip["stages"] if st["name"] == "zoom"), [])
    windows = existing + [[time, time + duration, strength, motion]]
    out = session.set_stage(clip_id, "zoom", {"windows": windows})
    return _ok(file=out, zoom_windows=windows)


def cut_silences(session: Session, clip_id: int,
                 max_pause: float = 0.5) -> dict:
    session.snapshot()
    out = session.set_stage(clip_id, "jumpcut", {"max_pause": max_pause})
    return _ok(file=out)


def set_fade(session: Session, clip_id: int, fade: float = 0.3) -> dict:
    session.snapshot()
    out = session.set_stage(clip_id, "fade", {"fade": fade})
    return _ok(file=out, fade=fade)


def add_sound_effect(session: Session, clip_id: int, time: float,
                     kind: str = "ding", volume: float = 0.6,
                     file: str = "") -> dict:
    if file:
        if not Path(file).exists():
            return _err(f"SFX file not found: {file}")
        path = file
    else:
        from pipeline.orchestrate import SFX_LIBRARY
        if kind not in SFX_LIBRARY:
            return _err(f"Unknown sfx '{kind}'. Available: {list(SFX_LIBRARY)}")
        path = SFX_LIBRARY[kind]
    session.snapshot()
    clip = session.clip(clip_id)
    existing = next((st["params"].get("events", [])
                     for st in clip["stages"] if st["name"] == "sfx"), [])
    events = existing + [{"time": time, "path": path, "volume": volume}]
    out = session.set_stage(clip_id, "sfx", {"events": events})
    return _ok(file=out, events=len(events))


def _caption_summary(sub: dict) -> str:
    """One-line description of a style's caption look from its subtitle block."""
    if not sub:
        return ""
    bits: list[str] = []
    case = "UPPERCASE" if sub.get("uppercase") else "mixed-case"
    bits.append("karaoke" if sub.get("karaoke") else "static")
    bits.append(case)
    if sub.get("text_color"):
        bits.append(f"text {sub['text_color']}")
    if sub.get("highlight_color"):
        bits.append(f"highlight {sub['highlight_color']}")
    pill = sub.get("pill")
    if pill:
        bits.append(f"pill {pill}" if isinstance(pill, str) else "pill")
    anim = sub.get("animation")
    if anim and anim != "none":
        bits.append(f"{anim} animation")
    if sub.get("auto_emoji"):
        bits.append("auto-emoji")
    return ", ".join(bits)


def list_styles(session: Session) -> dict:
    """The caption-template gallery: each style's label, prose description and a
    short summary of its caption look (font/color/highlight/animation)."""
    from pipeline.styles import load_styles
    gallery = {
        k: {
            "label": v.get("label", ""),
            "description": v.get("description", v.get("label", "")),
            "caption": _caption_summary(v.get("subtitle", {})),
        }
        for k, v in load_styles().items()
    }
    return _ok(styles=gallery)


def apply_style(session: Session, clip_id: int, style: str) -> dict:
    """Apply a named style preset to a clip in ONE batched replay."""
    from pipeline.styles import (SFX_DENSITY_CAP, get_style, jumpcut_params,
                                 load_styles, look_params, subtitle_params)

    sty = get_style(style)
    if not sty:
        return _err(f"Unknown style '{style}'. "
                    f"Available: {sorted(load_styles())}")
    session.snapshot(f"style:{style}")
    clip = session.clip(clip_id)
    pc = sty.get("pacing", {})
    au = sty.get("audio", {})

    updates: list[tuple[str, dict]] = [
        ("jumpcut", jumpcut_params(sty)),
        ("subtitles", subtitle_params(sty)),
        ("zoom", {"auto": True, "density": pc.get("zoom_density", 0.25),
                  "strength": pc.get("zoom_strength", 1.18), "windows": []}),
        ("sfx", {"auto": True,
                 "cap": SFX_DENSITY_CAP.get(au.get("sfx_density", "off"), 0),
                 "events": []}),
        ("fade", {"fade": au.get("fade", 0.3)}),
    ]

    # Optional color grade (meme/vivid styles). Legacy styles declare no `look`,
    # so look_params returns None and the lut stage is left untouched for them.
    lut = look_params(sty)
    if lut:
        updates.append(("lut", lut))

    from pipeline.soundbed import select_music
    ref = clip.get("current") or clip["stages"][-1]["output"]
    music = select_music(ref, mood_hint=au.get("music_mood"))
    if music and Path(music).exists():
        updates.append(("music", {"path": music,
                                  "volume": au.get("music_volume", 0.18)}))

    out = session.set_stages(clip_id, updates)
    clip["style"] = style
    session.save()
    return _ok(file=out, style=style, label=sty.get("label", ""),
               music=Path(music).name if music else None,
               notes=session.last_notes)


def remove_fillers(session: Session, clip_id: int,
                   aggressive: bool = False, preview: bool = False) -> dict:
    """Cut hesitation sounds (um/uh/ee/ıı...). aggressive=True also judges
    Turkish discourse fillers (yani/şey/hani) per occurrence via LLM.

    preview=True returns the candidate words/ranges that WOULD be cut, without
    mutating the clip — the transcript UI uses this to show a count and let the
    user confirm before rendering.
    """
    clip = session.clip(clip_id)
    if preview:
        from pipeline.jumpcut import FILLER_WORDS, _norm_word
        words = session.words_for(clip)
        cands = [{"i": i, "start": w["start"], "end": w["end"],
                  "word": w["word"]}
                 for i, w in enumerate(words)
                 if _norm_word(w["word"]) in FILLER_WORDS]
        discourse = []
        if aggressive:
            from pipeline.fillers import classify_filler_ranges
            discourse = [{"start": s, "end": e}
                         for s, e in classify_filler_ranges(words)]
        return _ok(preview=True, candidates=cands, discourse=discourse,
                   count=len(cands) + len(discourse))
    session.snapshot("remove_fillers")
    existing = next((st["params"] for st in clip["stages"]
                     if st["name"] == "jumpcut"), {})
    out = session.set_stage(clip_id, "jumpcut",
                            {**existing, "remove_fillers": True,
                             "aggressive_fillers": bool(aggressive)})
    return _ok(file=out, aggressive=bool(aggressive),
               notes=session.last_notes)


def save_style(session: Session, name: str, from_clip: int) -> dict:
    """Snapshot a clip's current look as a reusable named style preset."""
    import re as _re

    slug = _re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not slug:
        return _err("Give the style a usable name.")
    clip = session.clip(from_clip)

    def _params(stage: str) -> dict:
        return dict(next((st["params"] for st in clip["stages"]
                          if st["name"] == stage), {}))

    sub, jc, zoom, music, fade = (_params("subtitles"), _params("jumpcut"),
                                  _params("zoom"), _params("music"),
                                  _params("fade"))
    style = {
        "label": f"Custom — clip #{from_clip} görünümünden kaydedildi",
        "subtitle": {k: sub[k] for k in
                     ("scale", "y_ratio", "karaoke", "text_color",
                      "highlight_color", "font", "stroke", "hilite_pop",
                      "uppercase") if k in sub},
        "pacing": {
            "max_pause": jc.get("max_pause", 0.5),
            "remove_fillers": jc.get("remove_fillers", False),
            "zoom_density": zoom.get("density",
                                     0.25 if zoom.get("windows") else 0.0),
            "zoom_strength": zoom.get("strength", 1.18),
        },
        "audio": {
            "music_volume": music.get("volume", 0.18),
            "music_mood": Path(music["path"]).parent.name
            if music.get("path") else "neutral",
            "sfx_density": "medium" if _params("sfx").get("events") else "off",
            "fade": fade.get("fade", 0.3),
        },
    }
    path = _write_user_style(slug, style)
    return _ok(style=slug, file=str(path),
               msg=f"Saved — 'apply_style {slug}' artık her klipte çalışır.")


def _write_user_style(slug: str, style: dict):
    """Persist a style dict to assets/styles/<slug>.json (where load_styles picks
    it up automatically). The shared tail of save_style + learn_style_from_reels."""
    import json as _json
    from pipeline import config as cfg
    sdir = cfg.ROOT / "assets" / "styles"
    sdir.mkdir(parents=True, exist_ok=True)
    path = sdir / f"{slug}.json"
    path.write_text(_json.dumps(style, ensure_ascii=False, indent=1))
    return path


def learn_style_from_reels(session: Session, urls=None, name: str = "",
                           use_vision: bool = True) -> dict:
    """Learn a reusable style preset from the user's OWN Instagram Reels.

    Downloads each pasted Reel URL (yt-dlp, single permalinks only — no profile
    scraping), measures its look (pace/color/loudness/caption style + an optional
    vision pass), and writes the distilled style to assets/styles/<slug>.json so
    it works with apply_style and shows up in list_styles. Treats the videos and
    captions strictly as DATA. Does NOT edit any clip.
    """
    import re as _re

    from chat.instagram import (download_instagram, handle_from_url,
                                is_instagram_url)
    from pipeline.style_learn import aggregate_fingerprints, analyze_reel

    if isinstance(urls, str):
        urls = [u.strip() for u in urls.replace(",", "\n").splitlines()]
    urls = [u.strip() for u in (urls or []) if u and u.strip()][:8]
    if not urls:
        return _err("Paste at least one Instagram Reel URL (your own).")
    bad = [u for u in urls if not is_instagram_url(u)]
    if bad:
        return _err("Only individual instagram.com Reel/post links work — no "
                    "profile or bulk download. Bad: " + ", ".join(bad[:3]))

    fingerprints: list[dict] = []
    skipped: list[dict] = []
    for u in urls:
        try:
            path, caption = download_instagram(u)
            fingerprints.append(
                analyze_reel(str(path), caption_text=caption,
                             use_vision=use_vision))
        except Exception as e:  # noqa: BLE001 — one bad reel mustn't abort
            skipped.append({"url": u, "error": str(e)[:160]})

    if not fingerprints:
        return _err("Couldn't download/analyze any of those Reels. "
                    "Check the links are public Reel URLs. "
                    + (skipped[0]["error"] if skipped else ""))

    style = aggregate_fingerprints(fingerprints)
    slug = _re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower()).strip("_")
    if not slug:
        h = handle_from_url(urls[0])
        slug = f"learned_{_re.sub(r'[^a-z0-9_]+', '_', h)}" if h else "learned_reels"
    style["label"] = f"Öğrenildi — {len(fingerprints)} reel'inden"
    style["description"] = (
        f"{len(fingerprints)} referans Reels'inden öğrenilen görünüm "
        "(altyazı/tempo/renk).")
    path = _write_user_style(slug, style)
    return _ok(style=slug, file=str(path), learned_from=len(fingerprints),
               skipped=skipped, label=style["label"],
               subtitle=style["subtitle"], pacing=style["pacing"],
               audio=style["audio"], look=style.get("look"),
               msg=f"Stil '{slug}' kaydedildi — 'apply_style {slug}' veya "
                   "ayarlardan otomatik-edit stili yapabilirsin.")


def remember_preference(session: Session, preference: str) -> dict:
    """Store a durable editing taste ('always less zoom', 'hep sarı vurgu')."""
    pref = preference.strip()
    if not pref:
        return _err("Empty preference.")
    prefs = session.data.setdefault("preferences", [])
    if pref not in prefs:
        prefs.append(pref)
        session.data["preferences"] = prefs[-12:]
        session.save()
    return _ok(preferences=session.data["preferences"])


def forget_preferences(session: Session) -> dict:
    session.data["preferences"] = []
    session.save()
    return _ok(msg="All stored preferences cleared.")


def remove_section(session: Session, clip_id: int, start: float,
                   end: float) -> dict:
    """Remove a [start, end] span (current-timeline seconds) from a clip."""
    clip = session.clip(clip_id)
    start, end = float(start), float(end)
    if end <= start:
        return _err("end must be greater than start.")
    from pipeline.media import ffprobe_info
    dur = ffprobe_info(clip["current"])["duration"] if clip.get("current") else 0
    if dur and (end - start) > 0.5 * dur:
        return _err(f"That removes {end - start:.1f}s of a {dur:.1f}s clip "
                    "(more than half). If intended, use set_cut to re-cut "
                    "the clip instead.")

    # The UI selection is in the SPED (player) timeline; the trim stage runs
    # PRE-speed, so map the span back to pre-speed time (u = p * factor) before
    # matching words / storing the range. anchor_text re-resolves regardless.
    f = session.speed_factor(clip)
    ps, pe = start * f, end * f
    words = session.words_for(clip)
    seg = [w for w in words if w["end"] > ps and w["start"] < pe]
    anchor = " ".join(w["word"].strip() for w in seg)

    session.snapshot("remove_section")
    existing = next((st["params"].get("ranges", []) for st in clip["stages"]
                     if st["name"] == "trim"), [])
    ranges = existing + [{"start": ps, "end": pe, "anchor_text": anchor}]
    out = session.set_stage(clip_id, "trim", {"ranges": ranges})
    return _ok(file=out, removed=f"{start:.1f}-{end:.1f}s",
               removed_text=anchor or "(silence)", notes=session.last_notes)


def _rescale_event_times(stage_name: str, params: dict, scale: float) -> dict:
    """Multiply a post-speed stage's time-anchored fields by `scale`.

    Used when the speed factor changes: each event's player-time anchor moves so
    it keeps pointing at the SAME underlying content (p_new = p_old·oldF/newF).
    Zoom windows are [s,e,strength,(motion)] lists; everything else is a dict
    with 'time' (points) or 'start'/'end' (ranges)."""
    p = dict(params)
    if stage_name == "zoom":
        out = []
        for w in p.get("windows", []):
            w = list(w)
            w[0] = round(w[0] * scale, 4)
            w[1] = round(w[1] * scale, 4)
            out.append(w)
        if out:
            p["windows"] = out
        return p
    events = p.get("events")
    if not events:
        return p
    scaled = []
    for e in events:
        e = dict(e)
        if "time" in e:
            e["time"] = round(e["time"] * scale, 4)
        if "start" in e:
            e["start"] = round(e["start"] * scale, 4)
        if "end" in e:
            e["end"] = round(e["end"] * scale, 4)
        scaled.append(e)
    p["events"] = scaled
    return p


def set_speed(session: Session, clip_id: int, factor: float) -> dict:
    """Set a clip's constant playback speed (1.0 = normal, 2.0 = 2× faster,
    0.5 = half-speed slow-mo). Captions stay in sync — their word timing is
    rescaled with the footage. Range 0.25×–4×."""
    new_f = max(0.25, min(4.0, float(factor)))
    clip = session.clip(clip_id)
    if clip.get("locked"):
        return _err("Clip is picture-locked — unlock it first to change speed.")
    old_f = session.speed_factor(clip)
    # Snapshot BEFORE touching markers — they're mutated in place below, and a
    # post-mutation snapshot would make undo restore rescaled markers against
    # the old speed (desync).
    session.snapshot(f"speed {new_f:g}×")

    # Rescale every post-speed event + split-marker so they keep pointing at the
    # same content as the player timeline stretches/compresses (oldF → newF).
    updates: list[tuple[str, dict]] = [("speed", {"factor": new_f})]
    if abs(old_f - new_f) > 1e-6:
        from chat.session import SPED_EVENT_STAGES
        scale = old_f / new_f
        for st in clip["stages"]:
            if st["name"] in SPED_EVENT_STAGES and st["params"]:
                ru = _rescale_event_times(st["name"], st["params"], scale)
                if ru != st["params"]:
                    updates.append((st["name"], ru))
        for m in clip.get("markers", []):
            if "t" in m:
                m["t"] = round(m["t"] * scale, 3)

    out = session.set_stages(clip_id, updates)
    return _ok(file=out, factor=new_f,
               msg=f"Speed set to {new_f:g}× (captions kept in sync).",
               notes=session.last_notes)


def set_cut(session: Session, clip_id: int, start: float, end: float) -> dict:
    """Re-cut a clip from the SOURCE video (source-time seconds)."""
    start, end = float(start), float(end)
    if end <= start:
        return _err("end must be greater than start.")
    session.snapshot("set_cut")
    clip = session.clip(clip_id)
    out = session.set_stage(clip_id, "cut", {"start": start, "end": end})
    clip["start"], clip["end"] = start, end
    session.save()
    return _ok(file=out, range=f"{start:.1f}-{end:.1f}s",
               notes=session.last_notes)


def auto_zoom(session: Session, clip_id: int, density: float = 0.25,
              strength: float = 1.18) -> dict:
    """Let the LLM place punch-in zooms on the clip's emphatic phrases."""
    strength = max(1.08, min(1.5, float(strength)))
    density = max(0.05, min(0.5, float(density)))
    clip = session.clip(clip_id)
    words = session.words_for(clip)
    if not words:
        return _err("No speech found to plan zooms from.")

    from pipeline.editplan import plan_clip_edits
    plan = plan_clip_edits(words, 0.0, words[-1]["end"] + 1.0,
                           density=density, sfx_cap=0)
    # words are PRE-speed; zoom runs on the sped video → map times ÷ factor.
    f = session.speed_factor(clip)
    windows = [[e["start"] / f, e["end"] / f, strength]
               for e in plan["emphasis"]]
    if not windows:
        return _err("The planner found no zoom-worthy moments.")

    session.snapshot("auto_zoom")
    out = session.set_stage(clip_id, "zoom",
                            {"auto": True, "density": density,
                             "strength": strength, "windows": windows})
    return _ok(file=out, zoom_windows=windows)


def get_transcript(session: Session, clip_id: int) -> dict:
    clip = session.clip(clip_id)
    words = session.words_for(clip)
    text, t = [], None
    for w in words:
        if t is None or w["start"] - t > 2.0:
            text.append(f"\n[{w['start']:.1f}s]")
        text.append(w["word"])
        t = w["end"]
    return _ok(transcript=" ".join(text).strip())


_FIND_MOMENT_SYSTEM = """You locate moments in ONE short-video clip's transcript.
You get a word-timestamped transcript (clip-local seconds, format "[s-e] word")
and a description of WHAT is said/happens. Return the {limit} time spans that
best match the description, ranked best-first. A span should tightly cover the
matching words (not the whole clip). If nothing matches, return an empty list.

Return ONLY JSON:
{{
  "candidates": [
    {{"start": <s>, "end": <s>, "quote": "<the matched words>", "confidence": <0-1>}}
  ]
}}"""


def _snap_words(s: float, e: float, words: list[dict]) -> tuple[float, float]:
    """Snap a span to enclosing word boundaries (mirror editplan._snap)."""
    inside = [w for w in words if w["end"] > s and w["start"] < e]
    if not inside:
        return s, e
    return inside[0]["start"], inside[-1]["end"]


def _keyword_moments(words: list[dict], description: str,
                     limit: int) -> list[dict]:
    """Non-LLM fallback: slide a ~6s window and score matched description tokens.

    Returns top `limit` non-overlapping pre-speed {start,end,quote,confidence}
    spans (player-time conversion happens in the caller).
    """
    from pipeline.jumpcut import _norm_word

    tokens = {t for t in (_norm_word(w) for w in description.split())
              if len(t) >= 3}
    if not tokens or not words:
        return []

    win = 6.0
    scored = []
    for i, w in enumerate(words):
        ws, we = w["start"], w["start"] + win
        span = [x for x in words[i:] if x["start"] < we]
        if not span:
            continue
        matched = 0
        for x in span:
            nx = _norm_word(x["word"])
            if any(nx == t or nx.startswith(t) or t.startswith(nx)
                   for t in tokens):
                matched += 1
        if matched:
            scored.append((matched, ws, span[-1]["end"], span))
    if not scored:
        return []

    scored.sort(key=lambda r: (-r[0], r[1]))
    best = scored[0][0]
    out: list[dict] = []
    for matched, s, e, span in scored:
        if any(not (e <= o["start"] or s >= o["end"]) for o in out):
            continue
        s, e = _snap_words(s, e, words)
        out.append({"start": round(s, 2), "end": round(e, 2),
                    "quote": " ".join(x["word"].strip() for x in span).strip(),
                    "confidence": round(matched / best, 2)})
        if len(out) >= limit:
            break
    return out


def _find_moment_core(session: Session, clip: dict, description: str,
                      limit: int = 3) -> list[dict]:
    """Semantic in-clip lookup -> ranked clip-local PLAYER-time spans.

    words_for is PRE-speed; consuming tools (add_zoom/add_broll/remove_section)
    speak the player timeline, so every returned span is divided by the clip's
    speed factor here (the single conversion point — see auto_zoom).
    """
    words = session.words_for(clip)
    if not words:
        return []
    limit = max(1, min(10, int(limit)))
    f = session.speed_factor(clip)
    bound = words[-1]["end"]

    cands: list[dict] = []
    try:
        from pipeline import config

        api_key, base_url, model = config.llm_settings(
            getattr(session, "_tier", "fast"))
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
            else OpenAI(api_key=api_key)
        transcript = "\n".join(
            f"[{w['start']:.2f}-{w['end']:.2f}] {w['word']}" for w in words)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system",
                 "content": _FIND_MOMENT_SYSTEM.format(limit=limit)},
                {"role": "user",
                 "content": (f"Description: {description}\n\n"
                             f"Transcript:\n{transcript}")},
            ],
            temperature=0.1,
            **config.json_response_format(base_url),
        )
        data = config.extract_json(resp.choices[0].message.content)
        for c in data.get("candidates", []):
            try:
                s, e = float(c["start"]), float(c["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if e <= s:
                continue
            s, e = _snap_words(s, e, words)
            s = max(0.0, min(s, bound))
            e = max(0.0, min(e, bound))
            if e <= s:
                continue
            try:
                conf = float(c.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            cands.append({"start": round(s, 2), "end": round(e, 2),
                          "quote": str(c.get("quote", "")).strip(),
                          "confidence": round(max(0.0, min(1.0, conf)), 2)})
    except Exception:
        cands = []

    if not cands:
        cands = _keyword_moments(words, description, limit)
    cands = cands[:limit]

    return [{"start": round(c["start"] / f, 2), "end": round(c["end"] / f, 2),
             "quote": c["quote"], "confidence": c["confidence"]}
            for c in cands]


def find_moment(session: Session, clip_id: int, description: str,
                limit: int = 3) -> dict:
    """Semantic in-clip moment lookup by description (read-only)."""
    try:
        clip = session.clip(clip_id)
    except ValueError as exc:
        return _err(str(exc))
    try:
        cands = _find_moment_core(session, clip, description, limit)
    except ValueError:
        return _err("Render or open the clip first.")
    if not cands:
        return _err("No moment matching that description was found in this clip.")
    return _ok(candidates=cands, timeline="player",
               note="start/end are clip-local CURRENT-player-timeline seconds — "
                    "pass them directly to add_zoom/add_broll/remove_section/"
                    "add_emphasis.")


# A candidate must clear this confidence floor before remove_phrase will delete
# it unattended; below it we _err so the agent re-confirms with the user rather
# than guessing a span to cut.
_PHRASE_CONF_FLOOR = 0.4


def remove_phrase(session: Session, clip_id: int, description: str,
                  occurrence: str = "first") -> dict:
    """Delete the sentence/part where the speaker says X (content-described).

    Resolves the span(s) with the SAME semantic lookup as find_moment
    (_find_moment_core), then deletes them through the EXISTING trim pipeline —
    snapping to words, mapping player->pre-speed, storing anchor_text and
    enforcing the >50%-of-clip guard, exactly like remove_section.

    occurrence='first' (default) removes the single best-matching span;
    occurrence='all' removes EVERY candidate above the confidence floor in ONE
    atomic trim update (a single snapshot -> one undo entry).
    """
    try:
        clip = session.clip(clip_id)
    except ValueError as exc:
        return _err(str(exc))
    if clip.get("locked"):
        return _err("Clip is picture-locked — unlock it first.")

    occ = (occurrence or "first").strip().lower()
    if occ not in ("first", "all"):
        return _err("occurrence must be 'first' or 'all'.")

    # find_moment depends on a rendered/transcribable clip; _find_moment_core
    # calls words_for which raises ValueError when the clip isn't opened yet.
    try:
        cands = _find_moment_core(session, clip, description, limit=5)
    except ValueError:
        return _err("Render or open the clip first.")
    cands = [c for c in cands if c["confidence"] >= _PHRASE_CONF_FLOOR]
    if not cands:
        return _err(f"No part matching “{description}” cleared the confidence "
                    "floor. Try find_moment to inspect candidates, or give a "
                    "more specific quote.")

    chosen = cands if occ == "all" else cands[:1]

    # Map every chosen player-time span back to PRE-speed (u = p * factor) and
    # anchor it to its words — mirrors remove_section, but we accumulate ALL
    # ranges and commit them in ONE set_stage so undo stays atomic.
    f = session.speed_factor(clip)
    words = session.words_for(clip)
    new_ranges = []
    quotes = []
    total_player = 0.0
    for c in chosen:
        ps, pe = c["start"] * f, c["end"] * f
        seg = [w for w in words if w["end"] > ps and w["start"] < pe]
        anchor = " ".join(w["word"].strip() for w in seg)
        new_ranges.append({"start": ps, "end": pe, "anchor_text": anchor})
        quotes.append(c.get("quote") or anchor)
        total_player += c["end"] - c["start"]

    # >50% guard on the COMBINED removal (cumulative across all spans), same
    # spirit as remove_section's single-span guard.
    from pipeline.media import ffprobe_info
    dur = ffprobe_info(clip["current"])["duration"] if clip.get("current") else 0
    if dur and total_player > 0.5 * dur:
        return _err(f"That removes {total_player:.1f}s of a {dur:.1f}s clip "
                    "(more than half). If intended, use set_cut to re-cut "
                    "the clip instead.")

    session.snapshot("remove_phrase")
    existing = next((st["params"].get("ranges", []) for st in clip["stages"]
                     if st["name"] == "trim"), [])
    ranges = existing + new_ranges
    out = session.set_stage(clip_id, "trim", {"ranges": ranges})
    removed = [f"{c['start']:.1f}-{c['end']:.1f}s" for c in chosen]
    return _ok(file=out, removed=removed, count=len(chosen),
               removed_text=quotes, notes=session.last_notes)


def _frame_of(clip: dict) -> tuple[int, int, float]:
    from pipeline.media import ffprobe_info
    ref = clip.get("current") or clip["stages"][-1]["output"]
    info = ffprobe_info(ref)
    return info["width"], info["height"], info["fps"]


def add_broll(session: Session, clip_id: int, auto: bool = True,
              query: str = "", start: float = -1, end: float = -1,
              file: str = "") -> dict:
    """Overlay cover footage: a user/local file, or Pexels stock by query."""
    from pipeline.broll import (HOOK_GUARD_S, normalize_media, plan_broll,
                                search_broll)

    clip = session.clip(clip_id)
    words = session.words_for(clip)
    w, h, fps = _frame_of(clip)

    if file:
        if not Path(file).exists():
            return _err(f"Media not found: {file}")
        if start < 0 or end <= start:
            return _err("Local-file b-roll needs start and end seconds.")
        if start < HOOK_GUARD_S:
            return _err(f"B-roll can't cover the hook (first {HOOK_GUARD_S}s).")
        norm = normalize_media(file, width=w, height=h, fps=fps,
                               still_duration=end - start)
        # source_ref: future multicam — an event can point at an alternate
        # camera/source id; None = this footage file itself (Faz 6 insurance).
        events = [{"start": float(start), "end": float(end),
                   "query": Path(file).name, "path": norm,
                   "source_ref": None}]
        misses: list[str] = []
    else:
        if auto and not query:
            planned = plan_broll(words)
            if not planned:
                return _err("No b-roll-worthy moments found in this clip.")
            # words are PRE-speed; broll runs on the sped video → ÷ factor.
            f = session.speed_factor(clip)
            if abs(f - 1.0) > 1e-3:
                planned = [{**e, "start": e["start"] / f, "end": e["end"] / f}
                           for e in planned]
        else:
            if not query or start < 0 or end <= start:
                return _err("Manual b-roll needs query, start and end.")
            if start < HOOK_GUARD_S:
                return _err(
                    f"B-roll can't cover the hook (first {HOOK_GUARD_S}s).")
            planned = [{"start": float(start), "end": float(end),
                        "query": query}]

        events, misses = [], []
        for e in planned:
            try:
                path = search_broll(e["query"], width=w, height=h, fps=fps)
            except RuntimeError as err:
                return _err(str(err))
            if path:
                events.append({**e, "path": path, "source_ref": None})
            else:
                misses.append(e["query"])
        if not events:
            return _err(f"No stock footage found for: {misses}")

    session.snapshot("add_broll")
    existing = next((st["params"].get("events", []) for st in clip["stages"]
                     if st["name"] == "broll"), [])
    out = session.set_stage(clip_id, "broll", {"events": existing + events})
    return _ok(file=out, added=[{k: e[k] for k in ("start", "end", "query")}
                                for e in events],
               not_found=misses or None, notes=session.last_notes)


def add_gameplay_background(session: Session, clip_id: int,
                            pack: str = "minecraft",
                            layout: float = 0.6,
                            where: str = "full") -> dict:
    """Split-screen "brainrot" format: clip on top, looping muted gameplay
    background on the bottom. pack 'off'/'none' removes it. where='auto' shows
    the gameplay only during quiet/low-energy moments; 'full' = whole clip."""
    from pipeline.splitscreen import (PACKS, available_packs, pack_path,
                                      quiet_spans)

    clip = session.clip(clip_id)  # validate id
    pack = (pack or "minecraft").strip().lower()

    if pack in ("off", "none", "remove"):
        session.snapshot("remove gameplay bg")
        out = session.set_stage(clip_id, "splitscreen", {})
        return _ok(file=out, removed=True, notes=session.last_notes)

    bg = pack_path(pack)
    if not bg:
        avail = available_packs()
        if pack not in PACKS:
            return _err(f"Unknown pack '{pack}'. Choose one of: "
                        f"{sorted(PACKS)} (or 'off').")
        return _err(f"Pack '{pack}' isn't installed yet — no footage on disk. "
                    f"Available now: {avail or 'none'}.")

    layout = min(max(float(layout), 0.4), 0.8)
    sp: dict = {"path": str(bg), "pack": pack, "top_ratio": layout}
    where = (where or "full").strip().lower()
    note = None
    if where in ("auto", "smart", "quiet", "spans"):
        ref = clip.get("current") or clip["stages"][-1]["output"]
        spans = quiet_spans(ref)
        if spans:
            sp["spans"] = spans
            note = (f"gameplay shows only in {len(spans)} quiet span(s), "
                    f"full-frame elsewhere")
        else:
            note = "no quiet spans found — applied to the whole clip instead"

    session.snapshot(f"gameplay bg:{pack}")
    out = session.set_stage(clip_id, "splitscreen", sp)
    return _ok(file=out, pack=pack, top_ratio=layout, where=where,
               spans=sp.get("spans"), note=note, notes=session.last_notes)


_FIX_SYSTEM = """You correct speech-to-text (ASR) errors in a transcript. The \
speaker mixes their own language with English tech/business terms, and the ASR \
frequently mangles the English words — e.g. backend->"bekend", frontend->\
"fronted", AI->"EI"/"ay", engineer->"encinir", developer->"developır", \
"machine learning", "deploy", "framework", "startup", "freelance" etc. — and \
sometimes splits or mis-hears a clearly-intended word.

Return ONLY corrections for tokens that are CLEARLY wrong — where the speaker \
obviously meant a specific real word. Rules:
- Each fix maps ONE existing token to its corrected spelling (the SAME word, \
fixed). "from" MUST be an exact token that appears in the transcript.
- Do NOT translate, rephrase, fix grammar, change names, or "improve" text that \
is already a valid word. When unsure, leave it alone.
- Preserve the speaker's language; only repair mis-transcriptions.

Output JSON: {"fixes":[{"from":"<exact token>","to":"<corrected token>"}]}"""


_METADATA_SYSTEM = """You are a short-form video copywriter. Given a clip's \
spoken transcript (and its working title/hook), write publish-ready metadata for \
each requested platform. Write in the SAME language the speaker uses, unless an \
explicit target language is given.

Ground EVERYTHING in the transcript — never invent facts, names, numbers, or \
claims that aren't supported by what was actually said. Per-platform conventions:
- youtube_shorts: a punchy title <=100 chars + a 1-2 sentence keyword-rich \
description. 0-3 hashtags.
- tiktok: a hook-y caption (the first words must stop the scroll) + 3-5 \
on-topic hashtags.
- instagram_reels: a caption with a clear hook + up to 8 relevant hashtags.

Hashtags are single tokens without spaces; the leading '#' is optional (it will \
be added). Keep them topical, not spammy.

Return ONLY JSON of this exact shape (one entry per requested platform key):
{"platforms":{"<platform>":{"title":"...","description":"...",\
"hashtags":["..."]}}}"""


_METADATA_PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels")


def _normalize_hashtags(raw) -> list[str]:
    """Coerce model hashtag output to a capped list of '#'-prefixed tokens."""
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for tag in raw:
        if not isinstance(tag, str):
            continue
        tok = tag.strip().lstrip("#").strip()
        if not tok:
            continue
        out.append("#" + tok.replace(" ", ""))
        if len(out) >= 8:
            break
    return out


def generate_metadata(session: Session, clip_id: int,
                      platforms: list[str] | None = None,
                      language: str = "") -> dict:
    """Write platform-specific publish copy (title/description/hashtags) for a
    clip from its transcript. Read-only — no render, no approval gate, no undo
    snapshot. Stores the result on the clip additively and returns it."""
    aliases = {"youtube": "youtube_shorts", "shorts": "youtube_shorts",
               "instagram": "instagram_reels", "reels": "instagram_reels",
               "ig": "instagram_reels"}
    if platforms:
        wanted, seen = [], set()
        for p in platforms:
            key = aliases.get(str(p).strip().lower(), str(p).strip().lower())
            if key in _METADATA_PLATFORMS and key not in seen:
                seen.add(key)
                wanted.append(key)
        if not wanted:
            return _err("platforms must be a subset of "
                        + "|".join(_METADATA_PLATFORMS))
    else:
        primary = session.data.get("platform", "youtube_shorts")
        wanted = ([primary] if primary in _METADATA_PLATFORMS else []) + [
            p for p in _METADATA_PLATFORMS if p != primary]

    try:
        clip = session.clip(clip_id)
        words = session.words_for(clip)
    except ValueError as e:
        return _err(str(e))
    transcript = " ".join(w.get("word", "") for w in words).strip()
    if not transcript:
        return _err("No transcript words for this clip — open/render it first.")

    from pipeline import config
    api_key, base_url, model = config.llm_settings(
        getattr(session, "_tier", "fast"))
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)
    user = (f"REQUESTED PLATFORMS: {', '.join(wanted)}\n"
            f"WORKING TITLE: {clip.get('title', '')}\n"
            f"HOOK: {clip.get('hook', '')}\n")
    if language.strip():
        user += f"TARGET LANGUAGE: {language.strip()}\n"
    user += "\nTRANSCRIPT:\n" + transcript
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": _METADATA_SYSTEM},
                  {"role": "user", "content": user}],
        temperature=0.7, **config.json_response_format(base_url))
    try:
        data = config.extract_json(resp.choices[0].message.content)
        parsed = data.get("platforms", {}) if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return _err("Metadata generation failed — try again.")
    if not isinstance(parsed, dict):
        return _err("Metadata generation failed — try again.")

    validated: dict[str, dict] = {}
    for key in wanted:
        entry = parsed.get(key)
        if not isinstance(entry, dict):
            continue
        validated[key] = {
            "title": str(entry.get("title", "")).strip(),
            "description": str(entry.get("description", "")).strip(),
            "hashtags": _normalize_hashtags(entry.get("hashtags")),
        }
    if not validated:
        return _err("Metadata generation failed — try again.")

    clip["metadata"] = validated
    session.save()
    return _ok(clip_id=clip_id, metadata=validated)


def _llm_transcript_fixes(words: list[dict], hint: str = "") -> list[dict]:
    """Ask the LLM for ASR corrections over a word list. Returns validated
    [{"from","to"}] whose `from` tokens actually occur in the transcript."""
    from chat.session import _token_parts
    transcript = " ".join(w.get("word", "") for w in words).strip()
    if not transcript:
        return []
    from pipeline import config
    api_key, base_url, model = config.llm_settings()
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)
    user = "TRANSCRIPT:\n" + transcript
    if hint:
        user += f"\n\nThe user pointed out specifically: {hint}"
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": _FIX_SYSTEM},
                  {"role": "user", "content": user}],
        temperature=0.1, **config.json_response_format(base_url))
    try:
        data = config.extract_json(resp.choices[0].message.content)
    except (ValueError, TypeError):
        return []
    cores = {_token_parts(w.get("word", ""))[1].casefold() for w in words}
    seen, out = set(), []
    for f in data.get("fixes", []):
        if not isinstance(f, dict):
            continue
        frm = (f.get("from") or "").strip()
        to = (f.get("to") or "").strip()
        core = _token_parts(frm)[1].casefold()
        if not frm or not to or frm == to or core not in cores or core in seen:
            continue
        seen.add(core)
        out.append({"from": frm, "to": to})
    return out


def fix_transcript(session: Session, clip_id: int, hint: str = "") -> dict:
    """INTENT FIX: re-read the clip's transcript and correct obvious ASR
    mistakes (mis-heard English tech terms etc.). Caption text only — timing is
    untouched. `hint` carries a specific wrong->right the user named."""
    import hashlib

    clip = session.clip(clip_id)
    words = session.words_for(clip)  # already reflects any existing fixes
    fixes = _llm_transcript_fixes(words, hint)
    existing = list(clip.get("word_fixes", []))
    seen = {(f.get("from") or "").casefold() for f in existing}
    new = [f for f in fixes if (f["from"]).casefold() not in seen]
    if not new:
        return _ok(fixed=0,
                   message="No new transcription errors to fix in this clip.")

    session.snapshot("intent fix")
    clip["word_fixes"] = existing + new

    sub = next((st for st in clip["stages"] if st["name"] == "subtitles"), None)
    if sub is None:
        session.save()
        return _ok(fixed=len(new), file=clip.get("current"), fixes=new,
                   note="Stored — this clip has no captions to re-render.")
    # Captions are built from words_for (not stage params), so bump a fix-rev
    # token to bust the subtitle render cache and re-burn with corrected text.
    rev = hashlib.sha1(repr(clip["word_fixes"]).encode()).hexdigest()[:8]
    p = dict(sub["params"])
    p["caption_fix"] = rev
    out = session.set_stage(clip_id, "subtitles", p)
    return _ok(fixed=len(new), file=out, fixes=new,
               note=", ".join(f'{f["from"]}→{f["to"]}' for f in new),
               notes=session.last_notes)


def _brand_params(clip: dict) -> dict:
    return dict(next((st["params"] for st in clip["stages"]
                      if st["name"] == "brand"), {}))


def set_watermark(session: Session, clip_id: int, file: str,
                  corner: str = "tr", opacity: float = 0.85) -> dict:
    """Add/replace a corner watermark (logo image) on a clip."""
    if not Path(file).exists():
        return _err(f"Watermark image not found: {file}")
    if corner not in ("tl", "tr", "bl", "br"):
        return _err("corner must be tl|tr|bl|br")
    session.snapshot("set_watermark")
    clip = session.clip(clip_id)
    params = _brand_params(clip)
    params["watermark"] = {"path": file, "corner": corner,
                           "opacity": max(0.1, min(1.0, float(opacity)))}
    out = session.set_stage(clip_id, "brand", params)
    return _ok(file=out, corner=corner)


def set_title_card(session: Session, clip_id: int, text: str,
                   duration: float = 2.5) -> dict:
    """Show a title card over the first seconds of a clip."""
    if not text.strip():
        return _err("Title text is empty.")
    session.snapshot("set_title_card")
    clip = session.clip(clip_id)
    params = _brand_params(clip)
    params["title"] = {"text": text.strip(),
                       "duration": max(1.0, min(6.0, float(duration)))}
    out = session.set_stage(clip_id, "brand", params)
    return _ok(file=out, title=text.strip())


def add_meme_text(session: Session, clip_id: int, text: str,
                  position: str = "top", bar: bool = True,
                  font: str = "impact", start: float = 0.0,
                  duration: float = 0.0) -> dict:
    """Add an Instagram-style meme headline to a clip.

    bar=True  -> classic white bar with black text (the IG/Reddit meme caption).
    bar=False -> top/bottom white Impact text with a heavy black outline over
                 the video. position: top|bottom. duration<=0 covers the whole
                 clip; otherwise the text shows for `duration`s from `start`.
    """
    if not text.strip():
        return _err("Meme text is empty.")
    if position not in ("top", "bottom"):
        return _err("position must be top|bottom")
    from pipeline.styles import resolve_font
    session.snapshot("add_meme_text")
    clip = session.clip(clip_id)
    params = _brand_params(clip)
    memes = list(params.get("meme_texts", []))
    memes.append({"text": text.strip(), "position": position,
                  "bar": bool(bar), "font_path": resolve_font(font),
                  "start": max(0.0, float(start)),
                  "duration": max(0.0, float(duration))})
    params["meme_texts"] = memes
    out = session.set_stage(clip_id, "brand", params)
    return _ok(file=out, text=text.strip(), position=position, bar=bool(bar))


def set_look(session: Session, clip_id: int, look: str = "",
             file: str = "", strength: float = 0.5) -> dict:
    """Color-grade a clip: built-in look or a .cube LUT, at 0.1-1.0 strength."""
    from pipeline.colorfx import LOOKS
    if file and not Path(file).exists():
        return _err(f"LUT not found: {file}")
    if not file and look not in LOOKS:
        return _err(f"Unknown look '{look}'. Built-ins: {sorted(LOOKS)} "
                    "(or pass file=<path.cube>)")
    session.snapshot(f"look:{look or Path(file).stem}")
    params: dict = {"strength": max(0.1, min(1.0, float(strength)))}
    if file:
        params["cube"] = file
    else:
        params["look"] = look
    out = session.set_stage(clip_id, "lut", params)
    return _ok(file=out, look=look or Path(file).stem,
               strength=params["strength"])


def _append_overlay_event(session: Session, clip_id: int,
                          event: dict, label: str) -> str:
    session.snapshot(label)
    clip = session.clip(clip_id)
    existing = next((st["params"].get("events", []) for st in clip["stages"]
                     if st["name"] == "overlay"), [])
    # source_ref: future multicam — None = the event's own media file.
    event.setdefault("source_ref", None)
    return session.set_stage(clip_id, "overlay",
                             {"events": existing + [event]})


def add_overlay(session: Session, clip_id: int, file: str,
                mode: str = "screen", opacity: float = 0.3,
                start: float = 0, end: float = 0) -> dict:
    """Blend a texture loop (film grain / light leak / dust) over a clip."""
    if not Path(file).exists():
        return _err(f"Overlay media not found: {file}")
    event = {"type": "blend", "path": file, "mode": mode,
             "opacity": opacity, "start": start}
    if end > start:
        event["end"] = end
    out = _append_overlay_event(session, clip_id, event, "add_overlay")
    return _ok(file=out, mode=mode, opacity=opacity)


def add_reaction(session: Session, clip_id: int, file: str, start: float,
                 duration: float = 1.2, width_ratio: float = 0.45,
                 y_ratio: float = 0.78) -> dict:
    """Overlay a green-screen reaction/meme clip for a short window."""
    if not Path(file).exists():
        return _err(f"Reaction clip not found: {file}")
    from pipeline.safearea import clamp_center
    x_ratio, y_ratio = clamp_center(0.5, y_ratio, width_ratio,
                                    height_guess=width_ratio * 0.6)
    event = {"type": "greenscreen", "path": file, "start": float(start),
             "end": float(start) + max(0.4, min(4.0, float(duration))),
             "width_ratio": width_ratio, "x_ratio": x_ratio,
             "y_ratio": y_ratio}
    out = _append_overlay_event(session, clip_id, event, "add_reaction")
    return _ok(file=out, at=start)


def add_sticker(session: Session, clip_id: int, file: str, start: float,
                duration: float = 2.0, x_ratio: float = 0.5,
                y_ratio: float = 0.2, width_ratio: float = 0.25) -> dict:
    """Overlay a PNG sticker/emoji/arrow at a position for a window."""
    if not Path(file).exists():
        return _err(f"Sticker image not found: {file}")
    from pipeline.safearea import clamp_center
    x_ratio, y_ratio = clamp_center(x_ratio, y_ratio, width_ratio)
    event = {"type": "sticker", "path": file, "start": float(start),
             "end": float(start) + max(0.4, min(8.0, float(duration))),
             "width_ratio": width_ratio, "x_ratio": x_ratio,
             "y_ratio": y_ratio}
    out = _append_overlay_event(session, clip_id, event, "add_sticker")
    return _ok(file=out, at=start)


def add_emphasis(session: Session, clip_id: int, time: float,
                 kind: str = "flashshake", with_sfx: bool = True) -> dict:
    """Flash+shake hit on the strongest moment ('agency edit' accent)."""
    if kind not in ("flash", "shake", "flashshake"):
        return _err("kind must be flash|shake|flashshake")
    session.snapshot("add_emphasis")
    clip = session.clip(clip_id)
    existing = next((st["params"].get("events", []) for st in clip["stages"]
                     if st["name"] == "fx"), [])
    out = session.set_stage(
        clip_id, "fx",
        {"events": existing + [{"time": float(time), "kind": kind}]})
    if with_sfx:
        from pipeline.orchestrate import SFX_LIBRARY
        if "ding" in SFX_LIBRARY:
            sfx_existing = next(
                (st["params"].get("events", []) for st in clip["stages"]
                 if st["name"] == "sfx"), [])
            out = session.set_stage(
                clip_id, "sfx",
                {"events": sfx_existing + [
                    {"time": float(time), "path": SFX_LIBRARY["ding"],
                     "volume": 0.6}]})
    return _ok(file=out, at=time, kind=kind)


def auto_pace(session: Session, clip_id: int,
              max_static: float = 5.0) -> dict:
    """Retention pass: fill every static span > max_static with a jittered
    interrupt (zoom / whoosh / shake / ding, cycled)."""
    from pipeline.media import ffprobe_info
    from pipeline.orchestrate import SFX_LIBRARY
    from pipeline.pacing import KIND_CYCLE, longest_static_span, plan_interrupts

    max_static = max(2.5, min(10.0, float(max_static)))
    clip = session.clip(clip_id)
    if not clip.get("current"):
        return _err("Clip has no render yet.")
    words = session.words_for(clip)
    duration = ffprobe_info(clip["current"])["duration"]

    def _params(name: str) -> dict:
        return dict(next((st["params"] for st in clip["stages"]
                          if st["name"] == name), {}))

    zoom_p, sfx_p, fx_p = _params("zoom"), _params("sfx"), _params("fx")
    windows = [list(w) for w in zoom_p.get("windows", [])]
    sfx_events = list(sfx_p.get("events", []))
    fx_events = list(fx_p.get("events", []))
    existing = ([w[0] for w in windows]
                + [e["time"] for e in sfx_events]
                + [e["time"] for e in fx_events]
                + [e["start"] for e in _params("broll").get("events", [])])

    times = plan_interrupts(words, existing, duration, max_static)
    if not times:
        return _ok(added=[], longest_span=round(
            longest_static_span(existing, duration), 1),
            msg="Pacing is already within target — nothing to add.")

    session.snapshot("auto_pace")
    added = []
    for i, t in enumerate(times):
        kind = KIND_CYCLE[i % len(KIND_CYCLE)]
        if kind == "zoom":
            windows.append([t, t + 1.1, 1.16])
        elif kind == "shake":
            fx_events.append({"time": t, "kind": "shake"})
        elif kind in SFX_LIBRARY:
            sfx_events.append({"time": t, "path": SFX_LIBRARY[kind],
                               "volume": 0.45})
        else:  # sfx missing from library -> fall back to a zoom
            kind = "zoom"
            windows.append([t, t + 1.1, 1.16])
        added.append({"time": t, "kind": kind})

    updates: list[tuple[str, dict]] = [
        ("zoom", {**zoom_p, "windows": sorted(windows)}),
        ("sfx", {**sfx_p, "events": sorted(sfx_events,
                                           key=lambda e: e["time"])}),
        ("fx", {**fx_p, "events": sorted(fx_events,
                                         key=lambda e: e["time"])}),
    ]
    out = session.set_stages(clip_id, updates)
    new_existing = existing + [a["time"] for a in added]
    return _ok(file=out, added=added,
               longest_span=round(longest_static_span(new_existing,
                                                      duration), 1),
               notes=session.last_notes)


def set_loudness(session: Session, clip_id: int, platform: str = "") -> dict:
    """Master a clip's loudness for a platform (YT -14 / TikTok-IG -11 LUFS)."""
    from pipeline.effects import PLATFORM_LOUDNESS
    aliases = {"youtube": "youtube_shorts", "shorts": "youtube_shorts",
               "instagram": "instagram_reels", "reels": "instagram_reels"}
    platform = aliases.get(platform.strip().lower(),
                           platform.strip().lower())
    if platform not in PLATFORM_LOUDNESS:
        return _err(f"Unknown platform '{platform}'. "
                    f"Options: {sorted(PLATFORM_LOUDNESS)}")
    session.snapshot(f"loudness:{platform}")
    clip = session.clip(clip_id)
    existing = dict(next((st["params"] for st in clip["stages"]
                          if st["name"] == "fade"), {}))
    existing["platform"] = platform
    out = session.set_stage(clip_id, "fade", existing)
    lufs, tp = PLATFORM_LOUDNESS[platform]
    return _ok(file=out, platform=platform, lufs=lufs, tp=tp)


def duplicate_clip(session: Session, clip_id: int, label: str = "") -> dict:
    """Create a variant of a clip (instant — shares renders until edited)."""
    import copy as _copy
    session.snapshot("duplicate_clip")
    clip = session.clip(clip_id)
    new = _copy.deepcopy(clip)
    new["id"] = max(c["id"] for c in session.data["clips"]) + 1
    new["variant_of"] = clip.get("variant_of", clip_id)
    if label:
        new["title"] = f"{clip['title']} — {label}"
    session.data["clips"].append(new)
    session.save()
    return _ok(new_id=new["id"], variant_of=new["variant_of"],
               msg="Variant created instantly; it shares the original's "
                   "render until you change one of its stages.")


def pick_variant(session: Session, clip_id: int) -> dict:
    """Keep this variant, archive its siblings (and parent)."""
    session.snapshot("pick_variant")
    clip = session.clip(clip_id)
    root = clip.get("variant_of", clip_id)
    siblings = [c for c in session.data["clips"]
                if c["id"] != clip_id
                and (c.get("variant_of") == root or c["id"] == root)]
    if not siblings:
        return _err(f"Clip #{clip_id} has no variants to resolve.")
    clip.pop("variant_of", None)
    archived = session.data.setdefault("archived", [])
    for c in siblings:
        session.data["clips"].remove(c)
        archived.append(c)
    session.save()
    return _ok(kept=clip_id, archived=[c["id"] for c in siblings],
               msg="Siblings archived (files kept on disk; undo restores).")


def join_clips(session: Session, clip_ids: list[int],
               transition: str = "fade", duration: float = 0.5) -> dict:
    """Join several clips into one compilation video with transitions."""
    from pipeline.effects import fade_in_out
    from pipeline.effects import transition as xfade

    if not clip_ids or len(clip_ids) < 2:
        return _err("Need at least 2 clip ids to join.")
    clips = [session.clip(i) for i in clip_ids]
    for c in clips:
        if not c.get("current") or not Path(c["current"]).exists():
            return _err(f"Clip #{c['id']} has no rendered file.")

    duration = max(0.2, min(1.5, float(duration)))
    cur = clips[0]["current"]
    for i, nxt in enumerate(clips[1:], 1):
        cur = xfade(cur, nxt["current"], kind=transition, duration=duration,
                    out_path=str(session.workdir / f"_comp_step{i}.mp4"))

    comps = session.data.setdefault("compilations", [])
    cid = max((c["id"] for c in comps), default=0) + 1
    final = fade_in_out(cur, fade=0.3, normalize=True,
                        out_path=str(session.workdir / f"comp{cid:02d}.mp4"))
    from pipeline.media import ffprobe_info
    comp = {"id": cid, "title": " + ".join(c["title"] for c in clips),
            "clips": list(clip_ids), "file": final,
            "duration": round(ffprobe_info(final)["duration"], 1)}
    comps.append(comp)
    session.save()
    return _ok(compilation=comp,
               msg="Compilation rendered (loudness-normalized once at the end).")


def list_assets(session: Session) -> dict:
    """Show the user's asset library (auto-analyzed catalog)."""
    from pipeline import assets as alib
    cat = alib.catalog_for_llm()
    if not cat:
        return _err("Asset library is empty. Upload files in the web UI or "
                    "use ingest_assets with a file/folder path.")
    return _ok(count=len(cat), assets=cat)


def ingest_assets(session: Session, path: str) -> dict:
    """Ingest a file or folder of user assets (auto-analyze + catalog)."""
    from pipeline import assets as alib
    try:
        rows, errors = alib.ingest_path(path)
    except ValueError as e:
        return _err(str(e))
    return _ok(ingested=[{"id": r["id"], "kind": r["kind"],
                          "description": r["description"]} for r in rows],
               errors=errors or None)


# incremental_preview: which CANONICAL stage each plan action's edit lands on.
# set_stages replays ONCE from the earliest changed stage, and every artifact is
# an on-disk param-keyed cache hit (_out), so a preview only re-encodes stages
# at/after this floor — and a re-preview of the same plan is a pure cache hit.
# This map is purely ADVISORY: it feeds preview['preview_from'] for the UI/
# telemetry, it does NOT drive the (already-correct) replay. Actions touching
# several stages map to the EARLIEST (most upstream) one they invalidate.
_ACTION_STAGE_FLOOR: dict[str, str] = {
    "set_cut": "cut",
    "remove_section": "trim",
    "remove_phrase": "trim",
    "restore_section": "trim",
    "cut_silences": "jumpcut",
    "remove_fillers": "jumpcut",
    "apply_style": "jumpcut",   # touches jumpcut + downstream
    "auto_pace": "jumpcut",     # may re-cut/retime then add interrupts
    "set_aspect": "reframe",
    "set_denoise": "denoise",
    "set_speed": "speed",
    "add_broll": "broll",
    "set_look": "lut",
    "add_zoom": "zoom",
    "auto_zoom": "zoom",
    "add_gameplay_background": "splitscreen",
    "set_subtitles": "subtitles",
    "set_caption_language": "subtitles",
    "fix_transcript": "subtitles",
    "add_overlay": "overlay",
    "add_sticker": "overlay",
    "add_reaction": "overlay",
    "set_title_card": "brand",
    "set_watermark": "brand",
    "add_meme_text": "brand",
    "add_emphasis": "fx",
    "set_dub": "dub",
    "set_music": "music",
    "add_sound_effect": "sfx",
    "set_fade": "fade",
    "set_loudness": "fade",
}

# Actions that edit a single time-localized moment — their args carry the span
# (in current-timeline seconds) the UI should seek to when showing the preview.
_SPAN_ARG_KEYS = (("start", "end"), ("time", "duration"))


def _changed_stage_floor(plan: dict) -> str | None:
    """The earliest CANONICAL stage any of the plan's steps touches.

    Advisory only — set_stages already replays from the earliest change and the
    param-keyed cache makes unchanged stages free; this just surfaces WHERE the
    replay starts so the UI/telemetry can reason about the (latent) saving.
    Returns None when no step maps to a known stage (e.g. metadata-only plans).
    """
    from chat.session import CANONICAL
    order = {n: i for i, n in enumerate(CANONICAL)}
    floors = [_ACTION_STAGE_FLOOR[s["action"]]
              for s in plan.get("steps", [])
              if s.get("action") in _ACTION_STAGE_FLOOR]
    if not floors:
        return None
    return min(floors, key=lambda n: order[n])


def _changed_span(plan: dict) -> list[float] | None:
    """Union [start, end] (current-timeline seconds) of the plan's time-localized
    edits, or None when the plan has no single-moment span (whole-clip edits like
    set_subtitles/apply_style affect everything). Advisory: lets the UI seek to
    the affected region; it does NOT trigger a partial-span re-encode (that would
    break the word-timing origin t=0==clip.start and approved-export parity)."""
    lo: float | None = None
    hi: float | None = None
    for step in plan.get("steps", []):
        args = step.get("args") or {}
        for a, b in _SPAN_ARG_KEYS:
            if a not in args:
                continue
            try:
                s = float(args[a])
            except (TypeError, ValueError):
                continue
            if b == "duration":
                e = s + float(args.get(b, 0.0) or 0.0)
            elif b in args:
                e = float(args[b])
            else:
                e = s
            lo = s if lo is None else min(lo, s)
            hi = e if hi is None else max(hi, e)
            break
    if lo is None or hi is None:
        return None
    return [round(lo, 2), round(hi, 2)]


def _render_plan_preview(session: Session, plan: dict) -> dict | None:
    """Run the plan's steps on a throwaway copy of the session state and
    return the resulting clip artifact, WITHOUT committing anything.

    The artifacts are hash-named, so they survive on disk as cache: if the
    user approves, apply_plan replays the same steps and every render is a
    free cache hit (instant). If they reject, the session is untouched.

    incremental_preview: the preview re-encodes only stages at/after the
    earliest changed stage (set_stages replays from there; upstream + unchanged
    downstream artifacts are param-keyed cache hits). We surface that floor as
    preview['preview_from'] and, for time-localized edits, preview['span'] —
    both ADVISORY (UI/telemetry); neither alters the rendered bytes, the A/B
    gate contract, or approved-export fidelity.
    """
    import copy
    clip_id = plan.get("clip_id")
    if clip_id is None or not plan.get("steps"):
        return None
    backup = copy.deepcopy(session.data)
    session.suppress_snapshots = True
    try:
        result = _stage_plan_preview(session, plan)
        return result
    except Exception:
        return None
    finally:
        session.suppress_snapshots = False
        session.data = backup
        session.save()


def _stage_plan_preview(session: Session, plan: dict) -> dict | None:
    """Run ONE plan's steps in the CURRENT (already suppressed/backed-up)
    session and capture its clip preview. Caller owns backup/restore so the
    composite path can stage several plans before one restore. Returns the
    same {file, clip_id, ...advisory} shape as before (back-compat)."""
    clip_id = plan.get("clip_id")
    if clip_id is None or not plan.get("steps"):
        return None
    for step in plan["steps"]:
        fn = REGISTRY.get(step["action"])
        if fn is None or step["action"] in ("apply_plan", "discard_plan"):
            return None
        r = fn(session, **step["args"])
        if not r.get("ok"):
            return None
    clip = session.clip(clip_id)
    cur = clip.get("current")
    if not (cur and Path(cur).exists()):
        return None
    preview = {"file": cur, "clip_id": clip_id}
    # incremental_preview: advisory hints (additive, never load-bearing).
    # preview_from = earliest stage re-encoded; span = affected region for
    # time-localized edits so the UI can seek there instead of replaying
    # the whole clip. The actual render saving already came from
    # set_stages' earliest-change replay + the param-keyed artifact cache.
    floor = _changed_stage_floor(plan)
    if floor is not None:
        preview["preview_from"] = floor
    span = _changed_span(plan)
    if span is not None:
        preview["span"] = span
    # Ghost-diff: serialize the PREVIEW clip's timeline while its
    # staged state is still live, so the UI can overlay plan-result
    # tracks (dashed ghosts) on the current timeline before approval.
    try:
        from chat import timeline_view
        from pipeline.media import ffprobe_info
        info = ffprobe_info(cur)
        words = session.words_for(clip)
        preview["timeline"] = timeline_view.serialize(
            clip, words, info["duration"],
            session.data["source"].get("fps") or 30,
            speed=session.speed_factor(clip))
    except Exception:  # noqa: BLE001 — ghost is optional polish
        pass
    return preview


def _render_composite_preview(session: Session,
                              composite: dict) -> dict | None:
    """multiclip_plans — stage EVERY plan of a project-scope composite under
    ONE backup/restore and return a per-clip A/B carousel:
    {'scope':'project', 'plans':[{file, clip_id, ...}, ...]}.

    Each plan is replayed in sequence on the throwaway copy (cache makes the N
    clips cheap — incremental_preview's earliest-change replay + param-keyed
    artifacts). A plan whose preview can't be produced is simply omitted from
    the carousel (its steps may not apply to that clip); the others still show.
    Nothing is committed — the backup is restored in `finally`."""
    import copy
    plans = composite.get("plans") or []
    if not plans:
        return None
    backup = copy.deepcopy(session.data)
    session.suppress_snapshots = True
    try:
        previews: list[dict] = []
        for plan in plans:
            try:
                p = _stage_plan_preview(session, plan)
            except Exception:  # noqa: BLE001 — one bad clip mustn't kill the set
                p = None
            if p:
                previews.append(p)
        if not previews:
            return None
        return {"scope": "project", "plans": previews}
    except Exception:
        return None
    finally:
        session.suppress_snapshots = False
        session.data = backup
        session.save()


# visual_perception — give the agent eyes. After a single-clip preview artifact
# exists, OPTIONALLY pull a few keyframes and ask the 'pro' vision model to
# verify the render (crop centered? sticker over captions?). A found problem
# feeds ONE extra round of validator feedback back into the planner and we
# re-preview once. Bounded to a single vision-refine iteration to cap cost, and
# flag-gated (config.VISION_VERIFY) with a graceful no-vision fallback so the
# default path is byte-identical to current behavior.
def _vision_refine(session: Session, plan: dict,
                   preview: dict | None) -> tuple[dict, dict | None]:
    """If VISION_VERIFY is on and the preview shows a visible defect, re-plan
    once with the critique and re-preview. Returns the (possibly refined)
    (plan, preview). On the no-vision path this is a near-instant no-op:
    critique_clip short-circuits to {ok:True} and we return the inputs."""
    from pipeline import config as _cfg
    if not _cfg.VISION_VERIFY or not preview or not preview.get("file"):
        return plan, preview
    from pipeline import perception
    try:
        frames = perception.extract_keyframes(preview["file"])
        critique = perception.critique_clip(frames, plan.get("summary", ""))
    except Exception:  # noqa: BLE001 — perception is best-effort; never break
        return plan, preview
    problems = critique.get("problems") or []
    if critique.get("ok") or not problems:
        return plan, preview
    # One bounded refine: feed the visual critique back as steering for the
    # planner's existing bounded re-plan loop, then re-preview once.
    from chat.planner import propose as _propose
    note = ("A visual review of the PREVIEW render found these defects — "
            "produce a corrected plan that fixes them: "
            + perception.critique_summary(problems))
    try:
        refined = _propose(session, plan["clip_id"], plan["instruction"],
                           extra_note=note)
    except ValueError:
        # Re-plan failed — keep the original plan, but record the critique so
        # the agent can mention it to the user.
        preview["vision_problems"] = problems
        return plan, preview
    new_preview = _render_plan_preview(session, refined)
    refined["vision_refined"] = True
    refined["vision_problems"] = problems
    return refined, (new_preview or preview)


def _vision_verify_first(session: Session, composite: dict,
                         preview: dict | None) -> dict:
    """Project-scope verify: critique ONLY the first clip's preview (cost cap)
    and attach any visible defects as composite['vision_problems'] — advisory,
    no batch re-plan. No-op on the no-vision path (flag off / no key / no frame).
    """
    from pipeline import config as _cfg
    if not _cfg.VISION_VERIFY or not preview:
        return composite
    plans = preview.get("plans") or []
    first = plans[0] if plans else None
    if not first or not first.get("file"):
        return composite
    from pipeline import perception
    try:
        frames = perception.extract_keyframes(first["file"])
        summary = ""
        for p in composite.get("plans") or []:
            if p.get("clip_id") == first.get("clip_id"):
                summary = p.get("summary", "")
                break
        critique = perception.critique_clip(frames, summary)
    except Exception:  # noqa: BLE001 — best-effort; never break the proposal
        return composite
    problems = critique.get("problems") or []
    if not critique.get("ok") and problems:
        composite["vision_problems"] = {"clip_id": first.get("clip_id"),
                                        "problems": problems}
    return composite


def propose_assets(session: Session, clip_id: int,
                   instruction: str = "") -> dict:
    """Propose placements of the USER'S OWN assets in a clip (no execution)."""
    from chat.planner import propose_assets as _propose
    try:
        plan = _propose(session, clip_id, instruction)
    except ValueError as e:
        return _err(str(e))
    preview = _render_plan_preview(session, plan)
    if preview:
        plan["preview"] = preview
    session.data["pending_plan"] = plan
    session.save()
    return _ok(plan=plan, gaps=plan.get("gaps") or None,
               msg="Plan ready and a PREVIEW render of the result is already "
                   "showing in the player (A=current, B=plan). Present the "
                   "numbered steps (with each 'why', and any 'gaps' as "
                   "missing-asset notes), tell the user to compare A/B, and "
                   "WAIT for approval. Only call apply_plan after they "
                   "confirm; approval is then instant (cached render).")


# Edits the user has flagged as safe to auto-apply under autonomy=auto_minor:
# audio polish only, no structural/timing change.
_MINOR_ACTIONS = {"remove_fillers", "set_loudness", "set_fade"}


def ask_user(session: Session, question: str, options=None) -> dict:
    """Ask the user a clarifying question with optional one-tap choices.

    The agent loop intercepts this call as TERMINAL: the question is shown and
    the turn ends, so the assistant never guesses on a materially-ambiguous
    request. The user's next message (a tapped option or free text) answers it.
    Stores the chips on the session for the chat payload; no state mutation."""
    q = (question or "").strip()
    if not q:
        return _err("ask_user needs a question.")
    opts = options if isinstance(options, list) else []
    session.last_clarify = {"question": q,
                            "options": [str(o) for o in opts][:5]}
    return _ok(asked=True, question=q, options=session.last_clarify["options"])


def propose_edit(session: Session, clip_id: int, instruction: str) -> dict:
    """Plan a multi-step 'vibe' edit WITHOUT executing it."""
    from chat.planner import propose
    try:
        plan = propose(session, clip_id, instruction)
    except ValueError as e:
        # e.g. every step needed an asset the user doesn't have — the message
        # carries the planner's note so the agent can explain what's missing.
        return _err(str(e))

    # Autonomy gate: if the user set auto_minor and EVERY step is a minor
    # polish action, skip approval and apply straight away.
    steps = plan.get("steps", [])
    if (session.data.get("autonomy") == "auto_minor" and steps
            and all(s.get("action") in _MINOR_ACTIONS for s in steps)):
        session.data["pending_plan"] = plan
        res = apply_plan(session)
        res["auto_applied"] = True
        res["plan"] = plan
        res["msg"] = ("Auto-applied (autonomy=auto_minor; all steps were minor "
                      "polish). Tell the user what changed; undo reverts it.")
        return res

    preview = _render_plan_preview(session, plan)
    # visual_perception: optional verify-and-refine BEFORE the A/B gate. No-op
    # (and no LLM/ffmpeg cost) unless VISION_VERIFY is set; bounded to one pass.
    plan, preview = _vision_refine(session, plan, preview)
    if preview:
        plan["preview"] = preview
    session.data["pending_plan"] = plan
    session.save()
    if preview:
        msg = ("Plan ready and a PREVIEW render of the result is already "
               "showing in the player (A=current, B=plan). Present the "
               "numbered steps (with each 'why'), tell the user to compare "
               "A/B, and WAIT for approval. Only call apply_plan after they "
               "confirm; approval is then instant (cached render).")
    else:
        msg = ("Plan ready, but the preview render could not be produced (a "
               "step may not apply to this clip — e.g. it is picture-locked). "
               "Present the numbered steps (with each 'why'), note there is no "
               "A/B preview yet, and WAIT for approval. On 'uygula' call "
               "apply_plan; if a step then fails, explain which and why.")
    return _ok(plan=plan, msg=msg)


def propose_project(session: Session, instruction: str,
                    clip_ids: list[int] | None = None) -> dict:
    """multiclip_plans — plan a project-scope 'vibe' edit that spans several
    clips ('tighten every clip', 'hepsine altyazı ekle') WITHOUT executing.
    Builds ONE composite pending_plan over the target clips (explicit clip_ids,
    else every non-skipped clip, capped) and renders a per-clip A/B carousel.
    Approval applies them all under a single undo."""
    from chat.planner import propose_project as _propose
    try:
        composite = _propose(session, instruction, clip_ids=clip_ids)
    except ValueError as e:
        return _err(str(e))
    preview = _render_composite_preview(session, composite)
    # visual_perception: at project scope a composite can multiply cost, so we
    # only verify the FIRST clip's preview (and only when VISION_VERIFY is on);
    # a found defect is surfaced as an advisory note, not a full re-plan of the
    # whole batch. No-op on the default no-vision path.
    composite = _vision_verify_first(session, composite, preview)
    if preview:
        composite["preview"] = preview
    session.data["pending_plan"] = composite
    session.save()
    n = len(composite.get("plans") or [])
    if preview:
        msg = (f"Project plan ready for {n} clip(s); a per-clip A/B preview "
               "carousel is showing (A=current, B=plan per clip). Present the "
               "per-clip steps, note any skipped clips, tell the user to "
               "compare A/B across clips, and WAIT for approval. Only call "
               "apply_plan after they confirm — it applies every clip's steps "
               "under ONE undo (cached render, instant).")
    else:
        msg = (f"Project plan ready for {n} clip(s), but no preview render "
               "could be produced. Present the per-clip steps, note there is "
               "no A/B preview yet, and WAIT for approval. On approval call "
               "apply_plan — one undo reverts the whole multi-clip op.")
    return _ok(plan=composite, scope="project",
               skipped=composite.get("skipped"), msg=msg)


def apply_plan(session: Session) -> dict:
    """Execute the pending plan(s) as ONE atomic, single-undo operation.

    multiclip_plans — pending_plan may be a single plan OR a project-scope
    composite spanning several clips (session.pending_plans() normalizes both
    to a list). Either way we take ONE tagged snapshot and suppress per-step
    snapshots across the WHOLE batch, so a single undo / one revert_plan
    checkpoint reverts every clip's steps together (atomic multi-clip undo).
    A failure mid-clip still leaves exactly ONE undo entry."""
    composite = session.data.get("pending_plan")
    plans = session.pending_plans()
    if not plans:
        return _err("No pending plan. Use propose_edit first.")
    is_project = session.pending_plan_is_project()
    instruction = (composite.get("instruction")
                   or plans[0].get("instruction", "")) if composite else ""
    import uuid
    cp = uuid.uuid4().hex[:8]
    # Tag the PRE-plan snapshot so revert_plan can pop to exactly this op's
    # checkpoint later, regardless of how much history accrues on top. ONE
    # snapshot covers every clip in the composite -> atomic multi-clip undo.
    session.snapshot(f"plan: {instruction[:48]}", tag=cp)
    session.suppress_snapshots = True
    results: list[dict] = []
    failed_at = None
    try:
        i = 0
        for plan in plans:
            pcid = plan.get("clip_id")
            for step in plan["steps"]:
                i += 1
                fn = REGISTRY.get(step["action"])
                if fn is None:
                    r = {"ok": False,
                         "error": f"unknown action {step['action']}"}
                else:
                    try:
                        r = fn(session, **step["args"])
                    except Exception as e:
                        r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                entry = {"step": i, "action": step["action"],
                         "ok": bool(r.get("ok"))}
                if is_project:
                    entry["clip_id"] = pcid
                if not r.get("ok"):
                    entry["error"] = r.get("error", "?")
                    failed_at = i
                results.append(entry)
                if failed_at:
                    break  # later steps may depend on this one
            if failed_at:
                break  # stop the whole batch on the first failure
    finally:
        session.suppress_snapshots = False
    session.data["pending_plan"] = None
    # Record this applied plan against its checkpoint so the chat thread can
    # offer a per-message Revert / Regenerate (named checkpoints, not LIFO).
    # ONE record covers all clips so revert/regenerate act on the whole op.
    record = {"checkpoint": cp, "clip_id": plans[0].get("clip_id"),
              "instruction": instruction,
              "summary": (composite.get("summary", "") if composite else ""),
              "steps": [s for p in plans for s in p["steps"]],
              "failed_at": failed_at}
    if is_project:
        record["scope"] = "project"
        record["clip_ids"] = [p.get("clip_id") for p in plans]
        record["plans"] = plans
    session.data.setdefault("applied_plans", []).append(record)
    session.data["applied_plans"] = session.data["applied_plans"][-20:]
    session.last_applied = record  # transient, surfaced in the chat payload
    session.save()
    msg = ("Completed steps are kept; one undo reverts the whole plan."
           if not is_project else
           f"Applied across {len(plans)} clip(s); one undo reverts the whole "
           "multi-clip operation.")
    return _ok(applied=results, failed_at=failed_at, checkpoint=cp, msg=msg)


def discard_plan(session: Session) -> dict:
    session.data["pending_plan"] = None
    session.save()
    return _ok(msg="Pending plan discarded.")


def _resolve_applied(session: Session, checkpoint: str = "") -> dict | None:
    """The applied-plan record for a checkpoint id, or the most recent one
    when checkpoint is empty. None if there are no applied plans (or no match)."""
    records = session.data.get("applied_plans") or []
    if not records:
        return None
    if not checkpoint:
        return records[-1]
    return next((r for r in records if r.get("checkpoint") == checkpoint), None)


def revert_plan(session: Session, checkpoint: str = "") -> dict:
    """Revert ONE previously applied plan by its checkpoint id."""
    record = _resolve_applied(session, checkpoint)
    cp = checkpoint or (record["checkpoint"] if record else "")
    if not cp:
        return _err("No applied plan to revert.")
    # A pending plan staged over a now-reverted state is incoherent — clear it.
    session.data["pending_plan"] = None
    msg = session.revert_to_tag(cp)
    if "not found" in msg:
        session.save()
        return _err(msg)
    return _ok(reverted=cp, msg=msg + " (this restores the state BEFORE that "
               "plan, including any later edits; the revert is itself undoable)")


def regenerate_plan(session: Session, checkpoint: str = "",
                    revert: bool = True) -> dict:
    """Redo a previously applied plan DIFFERENTLY: optionally revert to its
    checkpoint, then re-propose the same instruction with a 'take a different
    approach' nudge. Returns a new pending plan for A/B approval."""
    import json
    from chat.planner import propose
    record = _resolve_applied(session, checkpoint)
    if record is None:
        return _err("No applied plan found to regenerate.")
    note = ""
    if revert:
        # Revert FIRST so the preview backup below captures the reverted state.
        msg = session.revert_to_tag(record["checkpoint"])
        if "not found" in msg:
            note = ("Could not revert (the checkpoint aged out), so the new "
                    "plan stacks on the current state. ")
    extra_note = (
        "The user REJECTED a previous plan for this same instruction. Take a "
        "NOTICEABLY different approach (different actions or clearly different "
        "parameters). Previous steps: "
        + json.dumps([{s["action"]: s["args"]} for s in record["steps"]],
                     ensure_ascii=False))
    session.data["pending_plan"] = None
    # multiclip_plans: a project-scope record regenerates as a project plan over
    # the same clip set (re-proposes each clip with the different-approach nudge).
    if record.get("scope") == "project":
        from chat.planner import propose_project as _propose_project
        try:
            plan = _propose_project(session, record["instruction"],
                                    clip_ids=record.get("clip_ids"),
                                    extra_note=extra_note)
        except ValueError as e:
            return _err(str(e))
        preview = _render_composite_preview(session, plan)
        if preview:
            plan["preview"] = preview
        session.data["pending_plan"] = plan
        session.save()
        n = len(plan.get("plans") or [])
        return _ok(plan=plan, scope="project", msg=(
            note + f"New (different) project plan ready for {n} clip(s) with a "
            "per-clip A/B carousel. Present the per-clip steps and WAIT for "
            "approval; apply_plan commits them all under one undo."))
    try:
        plan = propose(session, record["clip_id"], record["instruction"],
                       extra_note=extra_note)
    except ValueError as e:
        return _err(str(e))
    preview = _render_plan_preview(session, plan)
    if preview:
        plan["preview"] = preview
    session.data["pending_plan"] = plan
    session.save()
    if preview:
        msg = (note + "New (different) plan ready with a PREVIEW render showing "
               "in the player (A=current, B=plan). Present the numbered steps "
               "(with each 'why'), tell the user to compare A/B, and WAIT for "
               "approval. Only call apply_plan after they confirm.")
    else:
        msg = (note + "New (different) plan ready, but the preview render could "
               "not be produced. Present the numbered steps (with each 'why'), "
               "note there is no A/B preview, and WAIT for approval.")
    return _ok(plan=plan, msg=msg)


def nudge_edit(session: Session, clip_id: int, edge: str = "start",
               frames: int = -1, target: str = "cut") -> dict:
    """Move a clip's cut boundary by N frames (negative = earlier). Frame-exact
    via the source's timebase. 'kesimi 4 frame geri al' = frames=-4."""
    from pipeline.timebase import Timebase
    if edge not in ("start", "end"):
        return _err("edge must be 'start' or 'end'.")
    clip = session.clip(clip_id)
    cut = next((st["params"] for st in clip["stages"]
                if st["name"] == "cut"), None)
    if cut is None:
        return _err("Clip has no cut to nudge.")
    tb = Timebase.from_rate(str(session.data["source"].get("fps", 30)))
    start, end = float(cut["start"]), float(cut["end"])
    if edge == "start":
        start = tb.nudge_s(start, int(frames))
    else:
        end = tb.nudge_s(end, int(frames))
    if end - start < 0.2:
        return _err("That would make the clip shorter than 0.2s.")
    session.snapshot("nudge_edit")
    out = session.set_stage(clip_id, "cut", {"start": start, "end": end})
    clip["start"], clip["end"] = start, end
    session.save()
    return _ok(file=out, edge=edge, frames=int(frames),
               range=f"{start:.3f}-{end:.3f}s", notes=session.last_notes)


def add_marker(session: Session, clip_id: int, t: float,
               label: str = "", color: str = "amber") -> dict:
    """Pin a marker at clip-local time t (a note/chapter point). No render."""
    import hashlib
    clip = session.clip(clip_id)
    markers = clip.setdefault("markers", [])
    mid = "m" + hashlib.sha1(
        f"{clip_id}:{t}:{label}:{len(markers)}".encode()).hexdigest()[:6]
    markers.append({"id": mid, "t": round(float(t), 3),
                    "label": label or f"{float(t):.1f}s",
                    "color": color, "origin": "user"})
    markers.sort(key=lambda m: m["t"])
    session.save()
    return _ok(marker_id=mid, markers=markers)


def remove_marker(session: Session, clip_id: int, marker_id: str) -> dict:
    """Remove a marker by id."""
    clip = session.clip(clip_id)
    before = clip.get("markers", [])
    clip["markers"] = [m for m in before if m["id"] != marker_id]
    session.save()
    return _ok(removed=len(before) - len(clip["markers"]),
               markers=clip["markers"])


# Stages whose events the timeline can drag/resize/delete directly.
# (list_key, is_point, value_key) — zoom items are [s,e,strength] lists; the
# rest are dicts. Index-addressed: under the session lock, timeline item order
# equals param-list order, so no per-event id is needed.
_EDITABLE_EVENTS = {
    "zoom":    ("windows", False, "strength"),
    "broll":   ("events", False, None),
    "overlay": ("events", False, "opacity"),
    "fx":      ("events", True, None),
    "sfx":     ("events", True, "volume"),
}


def _editable_stage(session: Session, clip_id: int, stage: str, index: int):
    if stage not in _EDITABLE_EVENTS:
        return None, _err(f"Stage '{stage}' is not directly editable.")
    clip = session.clip(clip_id)
    st = next((s for s in clip["stages"] if s["name"] == stage), None)
    if st is None:
        return None, _err(f"Clip has no {stage} stage.")
    key = _EDITABLE_EVENTS[stage][0]
    items = list(st["params"].get(key, []))
    if not 0 <= index < len(items):
        return None, _err(f"No {stage} event #{index} (have {len(items)}).")
    return (clip, st, items), None


def edit_event(session: Session, clip_id: int, stage: str, index: int,
               start: float | None = None, end: float | None = None,
               value: float | None = None, motion: str | None = None) -> dict:
    """Move/resize/retune one timeline event (frame-snapped). start/end are
    clip-local seconds; value is strength|volume|opacity per the stage; motion
    (zoom only) is the Ken-Burns drift direction."""
    from pipeline.timebase import Timebase
    ctx, err = _editable_stage(session, clip_id, stage, index)
    if err:
        return err
    _clip, st, items = ctx
    key, is_point, vkey = _EDITABLE_EVENTS[stage]
    tb = Timebase.from_rate(str(session.data["source"].get("fps", 30)))

    if stage == "zoom":
        ev = list(items[index])
        while len(ev) < 4:                       # backfill [s,e,strength,motion]
            ev.append(1.18 if len(ev) == 2 else "center")
        if start is not None:
            ev[0] = tb.snap_s(float(start))
        if end is not None:
            ev[1] = tb.snap_s(float(end))
        if value is not None:
            ev[2] = max(1.05, min(1.6, float(value)))
        if motion is not None:
            ev[3] = motion if motion in ZOOM_MOTIONS else "center"
        if ev[1] <= ev[0]:
            return _err("end must be after start.")
        items[index] = ev
    else:
        ev = dict(items[index])
        if is_point:
            if start is not None:
                ev["time"] = tb.snap_s(float(start))
        else:
            if start is not None:
                ev["start"] = tb.snap_s(float(start))
            if end is not None:
                ev["end"] = tb.snap_s(float(end))
            if ev.get("end", 0) <= ev.get("start", 0):
                return _err("end must be after start.")
        if value is not None and vkey:
            ev[vkey] = float(value)
        items[index] = ev

    session.snapshot(f"edit {stage}")
    out = session.set_stage(clip_id, stage, {**st["params"], key: items})
    return _ok(file=out, stage=stage, index=index, notes=session.last_notes)


def delete_event(session: Session, clip_id: int, stage: str,
                 index: int) -> dict:
    """Delete one timeline event from a stage."""
    ctx, err = _editable_stage(session, clip_id, stage, index)
    if err:
        return err
    _clip, st, items = ctx
    key = _EDITABLE_EVENTS[stage][0]
    removed = items.pop(index)
    session.snapshot(f"delete {stage}")
    out = session.set_stage(clip_id, stage, {**st["params"], key: items})
    return _ok(file=out, stage=stage, removed=removed,
               remaining=len(items), notes=session.last_notes)


def lock_clip(session: Session, clip_id: int) -> dict:
    """Picture-lock a clip: its timing (cut/jumpcut/trim) is frozen; only
    visual/audio polish stays editable. The vibe planner and timeline both
    refuse timing edits on a locked clip."""
    clip = session.clip(clip_id)
    clip["locked"] = True
    session.save()
    return _ok(locked=True, clip_id=clip_id)


def unlock_clip(session: Session, clip_id: int) -> dict:
    clip = session.clip(clip_id)
    clip["locked"] = False
    session.save()
    return _ok(locked=False, clip_id=clip_id)


def set_autonomy(session: Session, mode: str) -> dict:
    """How much the AI may do without asking. ask_all = every plan needs
    approval; auto_minor = minor-only plans (fillers/loudness/fade) auto-apply,
    structural edits still ask."""
    if mode not in ("ask_all", "auto_minor"):
        return _err("mode must be 'ask_all' or 'auto_minor'.")
    session.data["autonomy"] = mode
    session.save()
    return _ok(autonomy=mode)


def export_captions(session: Session, clip_id: int,
                    format: str = "srt", language: str = "") -> dict:
    """Export a clip's captions as a sidecar subtitle file (SRT or VTT).

    Uses the SAME segmenter as the burned-in captions, so the file matches what
    plays on screen. Works even if the clip has no burned subtitle stage — it
    reads the clip's current word timings directly.

    language: target language for a TRANSLATED sidecar ('Spanish', 'es', …).
    Pass 'original'/'off' to force the spoken language. When omitted, the export
    DEFAULTS to whatever language the clip's burned captions use (subtitles
    stage 'lang'), so the file keeps matching the screen. Translated files get a
    language suffix (clip01.es.srt) so they don't clobber the original.
    """
    from pipeline.captions import build_caption_segments, to_srt, to_vtt
    fmt = (format or "srt").lower().lstrip(".")
    if fmt not in ("srt", "vtt"):
        return _err("format must be 'srt' or 'vtt'.")
    try:
        clip = session.clip(clip_id)
        words = session.words_for(clip)
    except ValueError as e:
        return _err(str(e))
    # Resolve the target language: explicit param wins; 'original'/'off' forces
    # the spoken language; an empty param matches the burned captions.
    raw = (language or "").strip()
    if raw.lower() in ("original", "off", "source", "none"):
        lang = ""
    elif raw:
        lang = raw
    else:
        sub = next((st["params"] for st in clip["stages"]
                    if st["name"] == "subtitles"), {})
        lang = (sub.get("lang") or "").strip()
    if lang:
        from pipeline.translate import translate_captions
        words = translate_captions(words, lang)
    segments = build_caption_segments(words, clip_start=0.0)
    if not segments:
        return _err("No transcript words to export for this clip.")
    text = to_vtt(segments) if fmt == "vtt" else to_srt(segments)
    suffix = f".{_lang_slug(lang)}" if lang else ""
    out = session.workdir / f"clip{clip_id:02d}{suffix}.{fmt}"
    out.write_text(text, encoding="utf-8")
    return _ok(path=str(out), format=fmt, segments=len(segments),
               language=(lang or None),
               url=f"/api/captions/{clip_id}.{fmt}")


def set_denoise(session: Session, clip_id: int,
                strength: str = "medium") -> dict:
    """Clean steady background noise from a clip's speech (afftdn).
    strength: light | medium | strong | off."""
    from pipeline.denoise import STRENGTHS
    strength = (strength or "medium").strip().lower()
    if strength not in (*STRENGTHS, "off"):
        return _err(f"strength must be one of {[*STRENGTHS, 'off']}.")
    session.snapshot(f"denoise:{strength}")
    if strength == "off":
        out = session.set_stage(clip_id, "denoise", {"enabled": False})
        return _ok(file=out, denoise="off")
    out = session.set_stage(clip_id, "denoise",
                            {"enabled": True, "strength": strength})
    return _ok(file=out, denoise=strength, notes=session.last_notes)


def restore_section(session: Session, clip_id: int,
                    start: float, end: float) -> dict:
    """Bring back a previously removed span. start/end are SOURCE seconds
    (as listed by the transcript's cut spans). Silence/filler cuts get a
    protect range on the jumpcut stage; user trims get their range removed."""
    clip = session.clip(clip_id)
    if clip.get("locked"):
        return _err("Clip is picture-locked — unlock it first.")
    try:
        chain = session.timing_chain_for(clip)
    except ValueError as e:
        return _err(str(e))
    mid = (float(start) + float(end)) / 2
    for link in chain:
        if link["name"] == "cut":
            continue
        t_in = link["pre"].to_output(mid)
        if t_in is None:
            continue  # span was removed by an earlier stage
        for gs, ge in link["own"].removed_spans():
            if not (gs - 0.02 <= t_in <= ge + 0.02):
                continue
            st = link["stage"]
            if link["name"] == "jumpcut":
                protect = [list(r) for r in
                           st["params"].get("protect_ranges", [])]
                protect.append([round(gs, 3), round(ge, 3)])
                session.snapshot("restore (sessizlik)")
                out = session.set_stage(clip_id, "jumpcut",
                                        {**st["params"],
                                         "protect_ranges": protect})
                return _ok(file=out, restored=[gs, ge], stage="jumpcut",
                           notes=session.last_notes)
            # trim: find the stored range that resolves onto this gap
            from pipeline.transcribe import transcribe
            from chat.session import _resolve_trim_ranges
            ranges = list(st["params"].get("ranges", []))
            words = transcribe(link["input"])["words"]
            resolved = _resolve_trim_ranges(words, ranges)
            for i, (rs, re_) in enumerate(resolved):
                if rs <= (gs + ge) / 2 <= re_ or gs <= (rs + re_) / 2 <= ge:
                    removed = ranges.pop(i)
                    session.snapshot("restore (kesit)")
                    out = session.set_stage(clip_id, "trim",
                                            {**st["params"],
                                             "ranges": ranges})
                    return _ok(file=out, restored=removed, stage="trim",
                               notes=session.last_notes)
            return _err("Cut span found but no matching trim range.")
    return _err("That span is not inside any removed section.")


def set_caption_language(session: Session, clip_id: int,
                         language: str = "") -> dict:
    """Render a clip's captions in another language (translated, timing kept).

    language: a target language name or code ("Spanish", "es", "İspanyolca",
    "Arabic"…). Pass "" / "original" / "off" to clear translation and go back
    to the spoken language. Translation happens at render time through the
    model-agnostic LLM layer and is disk-cached; on any failure the captions
    fall back to the original language rather than breaking the render.
    """
    lang = (language or "").strip()
    clip = session.clip(clip_id)
    if clip.get("locked"):
        return _err("Clip is picture-locked — unlock it first.")
    off = lang.lower() in ("", "original", "off", "none", "source")
    params = dict(next((st["params"] for st in clip["stages"]
                        if st["name"] == "subtitles"), {}))
    if off:
        if not params.get("lang"):
            return _ok(clip_id=clip_id, language=None,
                       msg=f"Clip #{clip_id} captions are already in the "
                           "original language.")
        params.pop("lang", None)
    else:
        params["lang"] = lang
    params.setdefault("karaoke", True)
    params.setdefault("scale", 1.0)
    params.setdefault("y_ratio", 0.68)
    session.snapshot(f"caption_lang:{lang or 'original'}")
    out = session.set_stage(clip_id, "subtitles", params)
    return _ok(file=out, clip_id=clip_id, language=(None if off else lang),
               msg=(f"Captions back to original language."
                    if off else f"Captions now in {lang}."),
               notes=session.last_notes)


def set_dub(session: Session, clip_id: int, language: str = "",
            voice: str = "") -> dict:
    """Dub a clip — replace the SPOKEN AUDIO with a translated voice.

    language: target language name/code ("Spanish", "es", "Arabic"…). Pass
    "" / "original" / "off" to remove the dub and restore the original voice.
    voice: optional provider voice name/id (else the configured default).

    Unlike set_caption_language (which only translates on-screen text), this
    re-voices the clip: each spoken sentence is translated and synthesized, then
    time-fitted back onto its original moment. Music/SFX still layer on top.
    Needs a TTS provider configured (OpenAI key by default); on failure the clip
    keeps its original audio.
    """
    lang = (language or "").strip()
    clip = session.clip(clip_id)
    if clip.get("locked"):
        return _err("Clip is picture-locked — unlock it first.")
    off = lang.lower() in ("", "original", "off", "none", "source")
    params = dict(next((st["params"] for st in clip["stages"]
                        if st["name"] == "dub"), {}))
    if off:
        if not params.get("lang"):
            return _ok(clip_id=clip_id, language=None,
                       msg=f"Clip #{clip_id} already uses its original voice.")
        params.pop("lang", None)
    else:
        params["lang"] = lang
        if voice.strip():
            params["voice"] = voice.strip()
    session.snapshot(f"dub:{lang or 'original'}")
    out = session.set_stage(clip_id, "dub", params)
    return _ok(file=out, clip_id=clip_id, language=(None if off else lang),
               msg=(f"Restored clip #{clip_id}'s original voice."
                    if off else f"Dubbed clip #{clip_id} into {lang}."),
               notes=session.last_notes)


def set_aspect(session: Session, clip_id: int, aspect: str = "9:16") -> dict:
    """Reframe a clip to a different aspect ratio (9:16 / 1:1 / 16:9).

    Merges over the EXISTING reframe params so tracking stays on, then replays.
    A different aspect is a distinct param-keyed artifact, so the upstream
    cut/jumpcut cache is reused across ratios and downstream subtitles/overlays
    auto-re-render on the new canvas (burn_subtitles reads the canvas from the
    reframed input via ffprobe).
    """
    aspect = (aspect or "9:16").strip()
    if aspect not in ASPECT_CHOICES:
        return _err(f"aspect must be one of {list(ASPECT_CHOICES)}.")
    clip = session.clip(clip_id)
    if clip.get("locked"):
        return _err("Clip is picture-locked — unlock it first.")
    existing = dict(next((st["params"] for st in clip["stages"]
                          if st["name"] == "reframe"), {}))
    if existing.get("aspect", "9:16") == aspect and clip.get("current"):
        return _ok(file=clip["current"], aspect=aspect,
                   msg=f"Clip #{clip_id} is already {aspect}.")
    session.snapshot(f"aspect:{aspect}")
    existing["aspect"] = aspect
    out = session.set_stage(clip_id, "reframe", existing)
    return _ok(file=out, clip_id=clip_id, aspect=aspect,
               msg=f"Reframed clip #{clip_id} to {aspect}.",
               notes=session.last_notes)


def export_clip(session: Session, clip_id: int, aspect: str = "") -> dict:
    """Phase 5 — render the FINAL full-resolution video for a clip.

    Interactive editing/preview runs against the cheap 540p proxy; this replays
    the clip's APPROVED stage-param chain against the full-res source for the
    deliverable. It does not change any edit, does not go through the A/B gate,
    and marks the clip 'exported'. Re-exporting an unedited clip is a cache hit.

    aspect (optional): one of 9:16 / 1:1 / 16:9. When given, the reframe stage
    is replayed at that ratio for THIS export only (param-keyed, so the upstream
    cut/jumpcut cache is reused) WITHOUT changing the clip's stored recipe — the
    editable stack keeps its current aspect. Omit to use the stored aspect.
    """
    aspect = (aspect or "").strip()
    if aspect and aspect not in ASPECT_CHOICES:
        return _err(f"aspect must be one of {list(ASPECT_CHOICES)}.")
    clip = session.clip(clip_id)
    # Temporarily pin the reframe stage to the requested aspect for this export.
    saved = None
    if aspect:
        for st in clip.get("stages") or []:
            if st["name"] == "reframe":
                saved = (st, dict(st["params"]))
                st["params"] = {**st["params"], "aspect": aspect}
                break
    try:
        out = session.export_clip(clip_id)
    except ValueError as e:
        return _err(str(e))
    finally:
        if saved is not None:
            saved[0]["params"] = saved[1]
            session.save()
    clip = session.clip(clip_id)
    clip["status"] = "exported"
    session.save()
    from pipeline.media import ffprobe_info
    info = ffprobe_info(out)
    return _ok(file=out, clip_id=clip_id,
               aspect=aspect or "stored",
               resolution=f"{info.get('width')}x{info.get('height')}",
               metadata=clip.get("metadata"),
               msg=f"Exported clip #{clip_id} at full resolution.")


def export_timeline(session: Session, clip_id: int,
                    format: str = "xml") -> dict:
    """Export a clip's cuts + markers as an NLE timeline file referencing the
    ORIGINAL source video — open in DaVinci Resolve (or Premiere) to refine.
    format: xml (FCP7 xmeml, best compat) | edl (CMX3600)."""
    from chat.export_nle import export_timeline as _export
    try:
        out = _export(session, clip_id, format)
    except ValueError as e:
        return _err(str(e))
    fmt = out.suffix.lstrip(".")
    return _ok(path=str(out), format=fmt,
               url=f"/api/export/{clip_id}.{fmt}",
               msg="Timeline exported. It references the original source "
                   "file; import into Resolve via File → Import → Timeline.")


def undo(session: Session) -> dict:
    return _ok(msg=session.undo())


def redo(session: Session) -> dict:
    return _ok(msg=session.redo())


# ----------------------------------------------------------------- specs
def _spec(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props,
                       "required": required}}}


_NUM = {"type": "number"}
_INT = {"type": "integer"}
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_STAGE_ENUM = {"type": "string", "enum": sorted(_EDITABLE_EVENTS)}
_ASPECT_ENUM = {"type": "string", "enum": list(ASPECT_CHOICES)}

TOOL_SPECS = [
    _spec("ask_user", "Ask the user ONE short clarifying question when an edit "
          "request is genuinely ambiguous and the choice changes the result "
          "(e.g. which kind of transition, which music mood, which color). "
          "options = 2-4 concrete one-tap choices in the user's language. The "
          "turn ends after this; the user's reply answers it. Do NOT use it "
          "when the intent is already clear.",
          {"question": _STR,
           "options": {"type": "array", "items": {"type": "string"}}},
          ["question"]),
    _spec("generate_clips", "Analyze the source video and produce the best "
          "short clips (cut + silence-trim + vertical reframe + karaoke "
          "captions). Replaces any existing clips. Clip length is chosen by "
          "the AI from the content's structure — only pass max_duration when "
          "the user explicitly asks for a cap. model: optional whisper size "
          "('tiny'/'base'/'small') for the analysis transcription — smaller is "
          "faster for a quick first candidate list; default keeps the "
          "configured model.",
          {"count": _INT, "max_duration": _NUM, "model": _STR}, []),
    _spec("list_clips", "Show the current session state (all clips and their "
          "applied stages).", {}, []),
    _spec("set_clip_status", "Set a candidate clip's review-queue status: "
          "pending | approved | skipped | exported. 'skipped' prunes a "
          "candidate from the top of the queue (the user dismissed it) WITHOUT "
          "deleting its rendered file — restore it later by setting it back to "
          "'pending'. Bookkeeping only; never re-renders.",
          {"clip_id": _INT, "status": _STR}, ["clip_id", "status"]),
    _spec("preview_clip", "Open a clip in the video player (QuickTime).",
          {"clip_id": _INT}, ["clip_id"]),
    _spec("set_music", "Add or change a clip's background music (auto-ducked "
          "under speech). mood: calm|neutral|energetic or a free word; or pass "
          "an explicit audio file path.",
          {"clip_id": _INT, "mood": _STR, "file": _STR, "volume": _NUM},
          ["clip_id"]),
    _spec("set_subtitles", "Re-style a clip's captions. scale: 1.0=default "
          "size (1.3=bigger). y_ratio: vertical center 0..1 (smaller=higher, "
          "default 0.68). karaoke: highlight the spoken word. text_color / "
          "highlight_color: hex like '#ffffff' / '#ffd60a'.",
          {"clip_id": _INT, "karaoke": _BOOL, "scale": _NUM, "y_ratio": _NUM,
           "text_color": _STR, "highlight_color": _STR},
          ["clip_id"]),
    _spec("set_caption_language", "Translate a clip's burned-in captions into "
          "another language while keeping them synced to the speech. language: "
          "a target language name or code ('Spanish', 'es', 'Arabic', "
          "'İspanyolca'). Pass 'original' (or 'off') to revert to the spoken "
          "language. The audio is NOT changed — this only translates the "
          "on-screen captions.",
          {"clip_id": _INT, "language": _STR},
          ["clip_id", "language"]),
    _spec("set_dub", "DUB a clip: replace the spoken AUDIO with a translated "
          "voice-over (each sentence translated, synthesized, and time-fitted "
          "back onto its moment). language: target language name/code. Pass "
          "'original' (or 'off') to restore the original voice. Use this when "
          "the user wants the VOICE/audio in another language; use "
          "set_caption_language when they only want translated SUBTITLES.",
          {"clip_id": _INT, "language": _STR, "voice": _STR},
          ["clip_id", "language"]),
    _spec("list_music", "List the available music tracks (by mood bucket) and "
          "ambience files — use to SUGGEST music to the user.", {}, []),
    _spec("add_zoom", "Add an eased punch-in zoom at a moment (seconds, "
          "clip-local). strength is a zoom FACTOR > 1: 1.1=subtle, "
          "1.18=default, 1.3=strong. motion: center|left|right|up|down — "
          "center=static punch, the others add a slow Ken-Burns pan.",
          {"clip_id": _INT, "time": _NUM, "duration": _NUM, "strength": _NUM,
           "motion": _STR},
          ["clip_id", "time"]),
    _spec("cut_silences", "Tighten a clip by removing pauses longer than "
          "max_pause seconds.",
          {"clip_id": _INT, "max_pause": _NUM}, ["clip_id"]),
    _spec("set_fade", "Set fade-in/out duration (also loudness-normalizes).",
          {"clip_id": _INT, "fade": _NUM}, ["clip_id"]),
    _spec("add_sound_effect", "Add a timed sound effect. kind: ding|whoosh|"
          "riser|impact|pop|boom|glitch (any file in assets/sfx/), or pass "
          "file=<path> for a user-library sfx.",
          {"clip_id": _INT, "time": _NUM, "kind": _STR, "volume": _NUM,
           "file": _STR},
          ["clip_id", "time"]),
    _spec("get_transcript", "Get a clip's timestamped transcript (to find "
          "moments for zoom/sfx or answer content questions).",
          {"clip_id": _INT}, ["clip_id"]),
    _spec("find_moment", "Semantic in-clip moment lookup: describe WHAT is "
          "said/happens ('where she mentions pricing', 'sondaki tekrar') and "
          "get the best matching {start,end} time spans (clip-local player "
          "seconds) with the matched quote. Use BEFORE add_zoom/add_broll/"
          "remove_section/add_emphasis or inside a propose_edit instruction "
          "when the user references content instead of times. Read-only.",
          {"clip_id": _INT, "description": _STR, "limit": _INT},
          ["clip_id", "description"]),
    _spec("list_styles", "List the available named editing styles (presets "
          "bundling captions+pacing+music+sfx).", {}, []),
    _spec("apply_style", "Apply a named style preset to a clip in one pass "
          "('Hormozi tarzı yap'). Changes captions, pacing, zooms, music, "
          "sfx and fade together. Use list_styles for names.",
          {"clip_id": _INT, "style": _STR}, ["clip_id", "style"]),
    _spec("remove_fillers", "Cut hesitation sounds (um, uh, ee, ıı, hmm...). "
          "aggressive=true ALSO removes Turkish discourse fillers (yani, "
          "şey, hani) judged per-occurrence by an LLM.",
          {"clip_id": _INT, "aggressive": _BOOL}, ["clip_id"]),
    _spec("save_style", "Save a clip's current look (captions+pacing+audio) "
          "as a named style preset reusable via apply_style.",
          {"name": _STR, "from_clip": _INT}, ["name", "from_clip"]),
    _spec("learn_style_from_reels", "Learn the user's editing STYLE from their "
          "OWN Instagram Reels and save it as a reusable preset (then usable via "
          "apply_style or as the auto-edit style). Pass the Reel permalink URLs "
          "the user provides (individual instagram.com/reel/... links only — "
          "never a profile/bulk). Optional name; use_vision uses the BYOK vision "
          "model for font/color/emoji feel (degrades gracefully). Use for "
          "'reel'lerimden stilimi öğren', 'learn my style from my reels'.",
          {"urls": {"type": "array", "items": _STR}, "name": _STR,
           "use_vision": _BOOL}, ["urls"]),
    _spec("remember_preference", "Store a DURABLE editing taste the user "
          "expressed ('hep daha az zoom', 'always yellow highlights') — "
          "future edits and plans will respect it.",
          {"preference": _STR}, ["preference"]),
    _spec("forget_preferences", "Clear all stored editing preferences.",
          {}, []),
    _spec("remove_section", "Remove a section of a clip ('şu kısmı at'). "
          "start/end are CURRENT-timeline clip seconds (as the user sees in "
          "the player). Refuses to remove more than half the clip.",
          {"clip_id": _INT, "start": _NUM, "end": _NUM},
          ["clip_id", "start", "end"]),
    # transcript_timeline: delete by CONTENT ('X dediği cümleyi sil') — resolves
    # the span via the same lookup as find_moment, then trims it.
    _spec("remove_phrase", "Delete the sentence/part where the speaker SAYS "
          "something ('X dediği yeri/cümleyi sil'), described by CONTENT not "
          "times. Resolves the matching span semantically (like find_moment) "
          "then removes it through the trim pipeline. occurrence='all' deletes "
          "every matching occurrence in one atomic step. Refuses if the total "
          "removal exceeds half the clip.",
          {"clip_id": _INT, "description": _STR,
           "occurrence": {"type": "string", "enum": ["first", "all"]}},
          ["clip_id", "description"]),
    _spec("set_speed", "Change a clip's constant playback speed. factor: "
          "2.0 = 2× faster, 0.5 = slow-motion, 1.0 = normal (range 0.25-4). "
          "Captions are rescaled with the footage so they stay in sync.",
          {"clip_id": _INT, "factor": _NUM}, ["clip_id", "factor"]),
    _spec("set_cut", "Re-cut a clip's bounds from the SOURCE video (seconds "
          "in the original long video) — e.g. start earlier / extend the end. "
          "All downstream edits replay automatically.",
          {"clip_id": _INT, "start": _NUM, "end": _NUM},
          ["clip_id", "start", "end"]),
    _spec("auto_zoom", "Automatically place punch-in zooms on the clip's "
          "emphatic phrases. density: zooms per second (0.25 = one per ~4s). "
          "strength: zoom factor 1.1-1.3.",
          {"clip_id": _INT, "density": _NUM, "strength": _NUM}, ["clip_id"]),
    _spec("add_broll", "Overlay cover footage on parts of a clip. Three "
          "modes: auto=true (LLM picks moments + Pexels stock); query+"
          "start/end (manual stock search); file+start/end (a LOCAL/user "
          "asset video or image path). Never covers the first 3s.",
          {"clip_id": _INT, "auto": _BOOL, "query": _STR,
           "start": _NUM, "end": _NUM, "file": _STR}, ["clip_id"]),
    _spec("add_gameplay_background", "Split-screen 'brainrot'/doom-scroll "
          "format: put the clip in the TOP of the frame and a looping, muted "
          "gameplay/satisfying background in the BOTTOM — the secondary motion "
          "boosts retention. pack: minecraft (parkour) | satisfying (glitter/"
          "particles in water) | runner (fast forward FPV motion) | ramp "
          "(racing-car POV); or 'off' to remove. layout: top fraction 0.4-0.8 "
          "(default 0.6 = top 60%). where: 'full' (whole clip, default) or "
          "'auto' (gameplay shows ONLY during quiet/low-energy moments, full-"
          "frame when the speaker is talking). Use for 'alt tarafa oyun koy', "
          "'split screen', 'doom scroll'; where='auto' for 'sadece sessiz "
          "anlarda', 'boş anlarda oyun çıksın'.",
          {"clip_id": _INT, "pack": _STR, "layout": _NUM, "where": _STR},
          ["clip_id"]),
    _spec("fix_transcript", "INTENT FIX: re-read the clip's transcript and "
          "correct obvious speech-to-text errors — mis-heard English tech "
          "terms (backend->'bekend', AI->'EI', frontend->'fronted') and "
          "similar. Fixes the CAPTION TEXT only; timing is untouched. Pass "
          "the user's specific correction as hint when they name one ('EI "
          "should be AI'). Use for 'altyazıdaki/transkriptteki hataları "
          "düzelt', 'yanlış yazılmış kelimeler', 'intent fix'.",
          {"clip_id": _INT, "hint": _STR}, ["clip_id"]),
    _spec("list_assets", "Show the user's asset library (their uploaded "
          "logos, b-roll, music, SFX...) with AI descriptions and ids.",
          {}, []),
    _spec("ingest_assets", "Add user assets to the library from a file or "
          "folder path — auto-analyzes (vision tags, colors, loudness).",
          {"path": _STR}, ["path"]),
    _spec("propose_assets", "Propose where the USER'S OWN assets would "
          "improve a clip (logo watermark, their b-roll, their music/sfx). "
          "Returns a plan for approval — same flow as propose_edit. Pass "
          "the user's wish as instruction if they gave one.",
          {"clip_id": _INT, "instruction": _STR}, ["clip_id"]),
    _spec("set_watermark", "Add a corner logo watermark. corner: tl|tr|bl|br.",
          {"clip_id": _INT, "file": _STR, "corner": _STR, "opacity": _NUM},
          ["clip_id", "file"]),
    _spec("set_title_card", "Show a big title card over the first seconds "
          "of a clip.",
          {"clip_id": _INT, "text": _STR, "duration": _NUM},
          ["clip_id", "text"]),
    _spec("add_meme_text", "Add an Instagram-style meme HEADLINE to a clip. "
          "bar=true (default) = classic white bar with black text; bar=false = "
          "white Impact text with a heavy black outline over the video. "
          "position: top|bottom. Omit duration (or 0) to show it the whole "
          "clip; else it shows for duration seconds from start. This is the "
          "meme caption/'üst yazı' the user writes — NOT the spoken karaoke "
          "subtitles. font: impact|block|condensed (free bundled faces).",
          {"clip_id": _INT, "text": _STR, "position": _STR, "bar": _BOOL,
           "font": _STR, "start": _NUM, "duration": _NUM},
          ["clip_id", "text"]),
    _spec("auto_pace", "RETENTION PASS: guarantee a visual/audio change "
          "every few seconds. Finds static spans longer than max_static "
          "(default 5s) and fills them with jittered interrupts (zoom/"
          "whoosh/shake/ding). Use for 'daha akıcı yap', 'izleyiciyi tut'.",
          {"clip_id": _INT, "max_static": _NUM}, ["clip_id"]),
    _spec("set_loudness", "Master the clip's final loudness for a platform. "
          "platform: youtube_shorts (-14 LUFS) | tiktok | instagram_reels "
          "(-11 LUFS).",
          {"clip_id": _INT, "platform": _STR}, ["clip_id", "platform"]),
    _spec("set_look", "Color-grade a clip. look: warm|cold|bw|cinematic|"
          "vintage (built-in), or file=<path.cube> for a user LUT. "
          "strength 0.1-1.0 (default 0.5 — pros never use 1.0).",
          {"clip_id": _INT, "look": _STR, "file": _STR, "strength": _NUM},
          ["clip_id"]),
    _spec("add_overlay", "Blend a texture video loop (film grain, light "
          "leak, dust) over a clip. mode: screen|overlay|softlight. "
          "opacity 0.2-0.4 typical. Omit end to cover the whole clip.",
          {"clip_id": _INT, "file": _STR, "mode": _STR, "opacity": _NUM,
           "start": _NUM, "end": _NUM}, ["clip_id", "file"]),
    _spec("add_reaction", "Overlay a GREEN-SCREEN reaction/meme clip at a "
          "moment (keyed, bottom-center by default, 0.5-1.5s typical).",
          {"clip_id": _INT, "file": _STR, "start": _NUM, "duration": _NUM,
           "width_ratio": _NUM, "y_ratio": _NUM}, ["clip_id", "file", "start"]),
    _spec("add_sticker", "Overlay a PNG sticker/emoji/arrow/logo at a "
          "position. x_ratio/y_ratio = center (0-1), width_ratio = size.",
          {"clip_id": _INT, "file": _STR, "start": _NUM, "duration": _NUM,
           "x_ratio": _NUM, "y_ratio": _NUM, "width_ratio": _NUM},
          ["clip_id", "file", "start"]),
    _spec("add_emphasis", "Flash+shake accent on the strongest moment "
          "(kind: flash|shake|flashshake), optionally with an impact sfx.",
          {"clip_id": _INT, "time": _NUM, "kind": _STR, "with_sfx": _BOOL},
          ["clip_id", "time"]),
    _spec("duplicate_clip", "Create a VARIANT copy of a clip to try a "
          "different edit ('3 farklı hook dene' = duplicate twice, then edit "
          "each). Instant.",
          {"clip_id": _INT, "label": _STR}, ["clip_id"]),
    _spec("pick_variant", "Resolve an A/B test: keep this variant, archive "
          "its siblings.", {"clip_id": _INT}, ["clip_id"]),
    _spec("join_clips", "Join clips into ONE compilation video with "
          "transitions. transition: fade|slideleft|wipeleft|circleopen|"
          "dissolve.",
          {"clip_ids": {"type": "array", "items": {"type": "integer"}},
           "transition": _STR, "duration": _NUM}, ["clip_ids"]),
    _spec("propose_edit", "Plan a vague/multi-aspect 'vibe' edit ('daha "
          "punchy yap', 'X tarzı ama girişe dokunma') WITHOUT executing. "
          "Returns a numbered step plan to show the user for approval. "
          "Pass the user's instruction verbatim.",
          {"clip_id": _INT, "instruction": _STR},
          ["clip_id", "instruction"]),
    # multiclip_plans: project-scope 'tighten every clip' planning. Builds ONE
    # composite pending plan over several clips; apply_plan commits them all
    # under a single undo.
    _spec("propose_project", "Plan a project-scope 'vibe' edit spanning "
          "SEVERAL clips ('tighten every clip', 'hepsine altyazı ekle') "
          "WITHOUT executing. Omit clip_ids to target every non-skipped clip "
          "(capped); else pass the specific ids. Returns a per-clip A/B "
          "carousel for approval. Pass the user's instruction verbatim.",
          {"instruction": _STR,
           "clip_ids": {"type": "array", "items": {"type": "integer"}}},
          ["instruction"]),
    _spec("apply_plan", "Execute the pending plan from propose_edit/"
          "propose_project after the user approves. One undo reverts the "
          "whole plan (every clip of a project plan, atomically).", {}, []),
    _spec("discard_plan", "Discard the pending plan (user said no/vazgeç).",
          {}, []),
    _spec("revert_plan", "Revert ONE previously applied plan by its checkpoint "
          "id (from apply_plan's result / the chat message). Restores the "
          "pre-plan state — including any edits applied after it — without "
          "disturbing later history (the revert itself is undoable). Empty "
          "checkpoint = the most recent applied plan.",
          {"checkpoint": _STR}, []),
    _spec("regenerate_plan", "Redo a previously applied plan DIFFERENTLY: "
          "reverts to its checkpoint and re-proposes the SAME instruction with "
          "a 'take a different approach' nudge. Returns a new pending plan for "
          "A/B approval. Empty checkpoint = the most recent applied plan.",
          {"checkpoint": _STR, "revert": _BOOL}, []),
    _spec("export_captions", "Export a clip's captions as a downloadable "
          "subtitle sidecar file. format: srt (default) | vtt. Matches the "
          "on-screen captions exactly. language: optional target for a "
          "TRANSLATED sidecar ('Spanish','es'); omit to match the burned "
          "captions, or 'original' to force the spoken language.",
          {"clip_id": _INT, "format": _STR, "language": _STR}, ["clip_id"]),
    _spec("nudge_edit", "Move a clip's cut boundary by N frames (frame-exact). "
          "edge: start|end. frames negative = earlier ('kesimi 4 frame geri "
          "al' = frames -4).",
          {"clip_id": _INT, "edge": _STR, "frames": _INT}, ["clip_id"]),
    _spec("add_marker", "Pin a marker (note/chapter point) at clip-local time "
          "t seconds. No re-render.",
          {"clip_id": _INT, "t": _NUM, "label": _STR}, ["clip_id", "t"]),
    _spec("remove_marker", "Remove a marker by its id.",
          {"clip_id": _INT, "marker_id": _STR}, ["clip_id", "marker_id"]),
    _spec("edit_event", "Move/resize/retune ONE existing timeline event by its "
          "0-based index (indices are shown per stage in the session state, "
          "e.g. zoom[1] = the second zoom). start/end are clip-local "
          "PLAYER-timeline seconds (frame-snapped); value retunes the stage's "
          "knob (zoom strength 1.05-1.6 | sfx volume | overlay opacity); motion "
          "(zoom only): center|left|right|up|down. Include only the fields you "
          "change.",
          {"clip_id": _INT, "stage": _STAGE_ENUM, "index": _INT,
           "start": _NUM, "end": _NUM, "value": _NUM, "motion": _STR},
          ["clip_id", "stage", "index"]),
    _spec("delete_event", "Delete ONE timeline event (a specific zoom/sfx/"
          "b-roll/overlay/fx hit) by stage + 0-based index from the session "
          "state.",
          {"clip_id": _INT, "stage": _STAGE_ENUM, "index": _INT},
          ["clip_id", "stage", "index"]),
    _spec("lock_clip", "Picture-lock a clip — freeze its timing (cut/silence/"
          "trim); only visual & audio polish stays editable.",
          {"clip_id": _INT}, ["clip_id"]),
    _spec("unlock_clip", "Remove a clip's picture-lock.",
          {"clip_id": _INT}, ["clip_id"]),
    _spec("set_autonomy", "Set how much the AI does without asking. mode: "
          "ask_all (approve everything) | auto_minor (auto-apply minor-only "
          "plans: fillers/loudness/fade; structural edits still ask).",
          {"mode": _STR}, ["mode"]),
    # reframe_aspect_tool: change a clip's framing/aspect ratio (a render).
    _spec("set_aspect", "Reframe a clip to a different aspect ratio: 9:16 "
          "(dikey, default), 1:1 (kare) or 16:9 (yatay). Keeps the "
          "active-speaker tracking and re-renders downstream captions/overlays "
          "on the new canvas. Goes through the A/B approval gate.",
          {"clip_id": _INT, "aspect": _ASPECT_ENUM}, ["clip_id", "aspect"]),
    _spec("export_clip", "Render the FINAL full-resolution video for a clip. "
          "Editing/preview uses a fast 540p proxy; this replays the approved "
          "edits on the full-res source for the deliverable. Marks the clip "
          "'exported'. Not part of the approval gate — call it when the user is "
          "happy with the clip and wants the final file. Optional aspect "
          "(9:16/1:1/16:9) exports that ratio for this render only without "
          "changing the clip's stored framing.",
          {"clip_id": _INT, "aspect": _ASPECT_ENUM}, ["clip_id"]),
    _spec("generate_metadata", "Write platform-specific publish copy (title + "
          "description + hashtags) for a clip from its transcript. platforms "
          "subset of youtube_shorts|tiktok|instagram_reels (default: all). "
          "Read-only — no render, no approval gate. Offer it after export_clip.",
          {"clip_id": _INT,
           "platforms": {"type": "array", "items": {
               "type": "string",
               "enum": ["youtube_shorts", "tiktok", "instagram_reels"]}},
           "language": _STR}, ["clip_id"]),
    _spec("export_timeline", "Export a clip's cuts+markers as an NLE timeline "
          "for DaVinci Resolve / Premiere. format: xml (FCP7, default) | edl.",
          {"clip_id": _INT, "format": _STR}, ["clip_id"]),
    _spec("restore_section", "Bring back a removed span (undo one specific "
          "cut without touching anything else). start/end in SOURCE seconds — "
          "get them from the transcript's cut list.",
          {"clip_id": _INT, "start": _NUM, "end": _NUM},
          ["clip_id", "start", "end"]),
    _spec("set_denoise", "Clean background noise from the speech track. "
          "strength: light | medium | strong | off.",
          {"clip_id": _INT, "strength": _STR}, ["clip_id"]),
    _spec("undo", "Undo the last editing operation.", {}, []),
    _spec("redo", "Redo the last undone editing operation.", {}, []),
]

REGISTRY = {
    "ask_user": ask_user,
    "generate_clips": generate_clips,
    "render_clip": render_clip,
    "list_clips": list_clips,
    "set_clip_status": set_clip_status,
    "list_music": list_music,
    "preview_clip": preview_clip,
    "set_music": set_music,
    "set_subtitles": set_subtitles,
    "set_caption_language": set_caption_language,
    "set_dub": set_dub,
    "add_zoom": add_zoom,
    "cut_silences": cut_silences,
    "set_fade": set_fade,
    "add_sound_effect": add_sound_effect,
    "get_transcript": get_transcript,
    "find_moment": find_moment,
    "list_styles": list_styles,
    "apply_style": apply_style,
    "remove_fillers": remove_fillers,
    "save_style": save_style,
    "learn_style_from_reels": learn_style_from_reels,
    "remember_preference": remember_preference,
    "forget_preferences": forget_preferences,
    "remove_section": remove_section,
    "remove_phrase": remove_phrase,  # transcript_timeline
    "set_speed": set_speed,
    "set_cut": set_cut,
    "auto_zoom": auto_zoom,
    "add_broll": add_broll,
    "add_gameplay_background": add_gameplay_background,
    "fix_transcript": fix_transcript,
    "set_watermark": set_watermark,
    "set_title_card": set_title_card,
    "add_meme_text": add_meme_text,
    "duplicate_clip": duplicate_clip,
    "pick_variant": pick_variant,
    "join_clips": join_clips,
    "auto_pace": auto_pace,
    "set_loudness": set_loudness,
    "set_look": set_look,
    "add_overlay": add_overlay,
    "add_reaction": add_reaction,
    "add_sticker": add_sticker,
    "add_emphasis": add_emphasis,
    "list_assets": list_assets,
    "ingest_assets": ingest_assets,
    "propose_assets": propose_assets,
    "propose_edit": propose_edit,
    "propose_project": propose_project,  # multiclip_plans
    "apply_plan": apply_plan,
    "discard_plan": discard_plan,
    "revert_plan": revert_plan,
    "regenerate_plan": regenerate_plan,
    "export_captions": export_captions,
    "nudge_edit": nudge_edit,
    "add_marker": add_marker,
    "remove_marker": remove_marker,
    "edit_event": edit_event,
    "delete_event": delete_event,
    "lock_clip": lock_clip,
    "unlock_clip": unlock_clip,
    "set_autonomy": set_autonomy,
    "export_clip": export_clip,
    "generate_metadata": generate_metadata,
    "export_timeline": export_timeline,
    "restore_section": restore_section,
    "set_denoise": set_denoise,
    "set_aspect": set_aspect,  # reframe_aspect_tool
    "undo": undo,
    "redo": redo,
}
