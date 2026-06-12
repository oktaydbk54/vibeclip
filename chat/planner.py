"""V2.2 — Vibe interpreter: a vague intent -> a reviewable edit plan.

"daha punchy yap" is not one tool call; it's taste applied across pacing,
zooms, captions, music and sfx. propose() asks the LLM for a SHORT numbered
plan of whitelisted tool calls (with a why per step), grounded in the clip's
current stage params and transcript. The plan is shown to the user; only on
approval does apply_plan execute it — atomically, as ONE undo entry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline import config
from chat.session import Session

# Actions the planner may emit — name -> (one-line contract shown to the LLM).
# These are exactly chat tool names; apply_plan dispatches through REGISTRY.
PLAN_ACTIONS: dict[str, str] = {
    "apply_style": '{"style": "hormozi|mrbeast|podcast_minimal|kinetic"} — '
                   'whole-look preset (captions+pacing+zooms+music+sfx)',
    "cut_silences": '{"max_pause": 0.3-0.7} — tighten/loosen pause removal',
    "remove_fillers": '{} — cut hesitation sounds (um/uh/ee)',
    "remove_section": '{"start": s, "end": s} — delete a span '
                      '(current-timeline seconds)',
    "set_cut": '{"start": s, "end": s} — re-cut clip bounds in SOURCE seconds',
    "auto_zoom": '{"density": 0.1-0.4, "strength": 1.1-1.3} — auto-place '
                 'punch-in zooms on emphatic phrases',
    "add_zoom": '{"time": s, "duration": s, "strength": 1.1-1.3, '
                '"motion": "center|left|right|up|down"} — one manual zoom at a '
                'moment; motion≠center = a slow Ken-Burns pan in that direction',
    "set_speed": '{"factor": 0.25-4.0} — ABSOLUTE constant playback speed '
                 '(2.0 = 2× faster, 0.5 = slow-mo / ağır çekim, 1.0 = normal). '
                 'Captions/zooms/sfx stay in sync automatically. For relative '
                 'asks ("biraz daha hızlandır") read the clip\'s current speed '
                 'from CURRENT STATE and emit the new ABSOLUTE factor',
    "add_sound_effect": '{"time": s, "kind": "ding|whoosh", "volume": 0-1} — '
                        'one timed sfx',
    "set_music": '{"mood": "calm|neutral|energetic", "volume": 0.10-0.25} — '
                 'change music; or {"file": "<track name>"} to keep/set a track',
    "set_subtitles": '{"karaoke": bool, "scale": 0.8-1.4, "y_ratio": 0.12-0.85, '
                     '"text_color": "#hex", "highlight_color": "#hex"} — '
                     'caption styling. Include ONLY the keys you are CHANGING; '
                     'omitted keys keep their current value, so NEVER restate '
                     'karaoke/colors on a position-only or size-only ask. '
                     'y_ratio = vertical CENTER: 0.15=top/"yukarıda", 0.5=screen '
                     'middle/"ortada", 0.68=lower-third (default), 0.8=bottom/'
                     '"altta". Captions are always horizontally centered',
    "set_title_card": '{"text": "<short title ≤5 words>", "duration": 1.5-3} — '
                      'a big title card over the first seconds of the clip',
    "set_fade": '{"fade": 0.15-0.6} — fade in/out length',
    "auto_pace": '{"max_static": 3-6} — retention pass: fill static spans '
                 'with jittered interrupts (zoom/sfx/shake)',
    "set_look": '{"look": "warm|cold|bw|cinematic|vintage", '
                '"strength": 0.3-0.7} — color grade',
    "add_emphasis": '{"time": s, "kind": "flashshake"} — flash+shake accent '
                    'on the strongest moment',
    "set_loudness": '{"platform": "youtube_shorts|tiktok|instagram_reels"} — '
                    'final loudness mastering',
    "set_denoise": '{"strength": "light|medium|strong|off"} — clean '
                   'background noise/hum from the voice track',
    "fix_transcript": '{"hint": "optional: a specific wrong->right the user '
                      'named"} — re-read the transcript and correct obvious '
                      'speech-to-text errors (mis-heard English tech terms: '
                      'bekend->backend, EI->AI); fixes caption TEXT only, '
                      'timing unchanged. Use only when the user reports a '
                      'transcription/caption spelling error',
    "add_gameplay_background": '{"pack": "minecraft|satisfying|runner|ramp", '
                               '"layout": 0.5-0.7, "where": "full|auto"} — '
                               'split-screen "brainrot" format: clip on top, '
                               'looping muted gameplay on the bottom. '
                               'where="auto" shows it ONLY during quiet/low-'
                               'energy moments (use for "sadece sessiz/boş "'
                               'anlarda"). Use only if the user asks for split-'
                               'screen / gameplay / doom-scroll',
    "add_broll": '{"auto": true} for LLM-picked stock cover moments; OR '
                 '{"query": "<english search>", "start": s, "end": s} for one '
                 'stock insert; OR {"file": "<exact asset path>", "start": s, '
                 '"end": s} for a user asset. Never covers the first 3s',
    "add_overlay": '{"file": "<exact asset path>", "mode": "screen", '
                   '"opacity": 0.2-0.4} — blend a film-grain / light-leak / '
                   'dust texture loop over the clip. The file must BE such a '
                   'texture per its catalog description — if the library has '
                   'no grain/leak/dust asset, OMIT this step',
    "add_reaction": '{"file": "<exact asset path>", "start": s, '
                    '"duration": 0.8-1.5} — green-screen reaction/meme overlay '
                    'at a moment. The file must BE a reaction/meme clip per '
                    'its catalog description — otherwise OMIT this step',
    "add_sticker": '{"file": "<exact asset path>", "start": s, "duration": 1-3, '
                   '"x_ratio": 0-1, "y_ratio": 0-1, "width_ratio": 0.15-0.35} — '
                   'a PNG sticker/emoji/arrow/logo. The file must DEPICT what '
                   'the user asked for per its catalog description (a logo is '
                   'not an arrow/emoji) — otherwise OMIT this step',
    "set_watermark": '{"file": "<exact asset path>", "corner": "tl|tr|bl|br", '
                     '"opacity": 0.6-0.9} — a corner logo watermark. Needs a '
                     'real asset path',
}

_SYSTEM = """You are a senior short-form video editor. The user gives a vague \
"vibe" instruction for ONE clip. Produce a SHORT plan of tool calls that \
realizes it. You see the clip's CURRENT edit state and transcript — edit \
RELATIVE to that state (e.g. raise the existing music volume, don't reset the \
track; keep what already matches the vibe).

House taste rules (hard):
- ALL time args (time/start/end) are CLIP-LOCAL seconds, from 0 to the clip's \
duration — NEVER the source-video timestamps shown in CURRENT STATE (those are \
where the clip was cut FROM the long video; ignore them for placement). Read \
clip-local moments from the TRANSCRIPT's [Xs] markers, and keep every time \
within the clip's length.
- Zooms: prefer ONE auto_zoom over many add_zoom; density <= 0.4.
- At most 3 sound effects in a clip.
- Music volume stays within 0.10-0.25.
- Never remove more than 40% of the clip's duration in total.
- The first 3 seconds are the hook: strengthen them, NEVER cut into them.
- A vague vibe ("daha punchy", "viral yap") -> 3-7 steps. But a SPECIFIC, \
single-effect request ("split screen yap", "altyazıyı büyüt", "sıcak görünüm", \
"müziği kıs", "2x hızlandır", "geçiş ekle", "başlık koy", "logoyu koy") -> a \
MINIMAL plan of just that effect (1-2 steps); do NOT pad it with unrequested \
music/watermark/sfx/zooms. Each step must visibly serve the instruction.
- MULTIPLE DISTINCT REQUESTS in one instruction (joined by "ve", "and", commas, \
"sonra", "bir de" — e.g. "2x hızlandır, altyazıyı yukarı al ve müzik ekle") -> \
emit ONE step for EACH request and NEVER drop one. The "minimal plan" rule \
above applies PER request, not to the whole message — so 3 requests = 3 steps, \
not a trimmed-down 1.
- SPEED: set_speed's factor is ABSOLUTE, never relative. "2x hızlandır" = 2.0, \
"yarı hız / ağır çekim / slow motion" = 0.5. When the user NAMES a factor \
emit that number clamped to the tool's real 0.25-4.0 range — "10x yap" -> 4.0 \
(max), "0.1x" -> 0.25 (min) — and say so in the summary; NEVER silently \
substitute a different factor or pad the plan with unrequested steps. For relative asks \
read the clip's current factor from CURRENT STATE and move FROM it: "biraz \
daha hızlandır" at 2.0× -> ~2.5; "biraz yavaşlat" at 2.0× -> ~1.5 (toward \
1.0 — NOT slow-mo 0.5; 0.5 is only for an explicit slow-motion/yarı-hız ask). \
Emit at most ONE set_speed per plan and never pair it with an unrequested \
cut_silences/auto_pace. Stay within 0.5-2.0 for vague asks.
- CAPTION POSITION: "(altyazıları/captionları) yukarı al / üstte / yukarıda \
ortala" -> set_subtitles y_ratio≈0.15; "ortala / ekranın ortasına" -> 0.5; \
"aşağı / altta" -> 0.8. "ortala" alone (no vertical word) means the screen \
MIDDLE (0.5) — captions are already horizontally centered.
- WITHIN-CLIP TRANSITIONS: "araya geçiş ekle / geçiş efekti" on ONE continuous \
clip = a flash-cut: add_emphasis(kind="flash") at a sentence/topic boundary \
visible in the transcript — pick a pause AFTER the first 3 seconds (a [Xs] gap \
marker is a good spot); NEVER time=0 or inside the 3s hook. Optionally add \
add_sound_effect(kind="whoosh") at the SAME time. Max 3 transitions. (True \
crossfades exist only when JOINING separate clips — not a plan action here.)
- FILE-BASED FX (add_overlay/add_reaction/add_sticker/set_watermark, and \
add_broll with file=): NEVER invent a path. Use ONLY exact `path` values from \
the ASSET CATALOG block below (absolute paths) or a path given in the \
instruction itself, and ONLY when the asset genuinely MATCHES the request — a \
logo is NOT an arrow sticker, an abstract clip is NOT film grain. If no \
matching asset exists, OMIT the step and name what's missing in the summary; \
never fabricate a file or substitute an unrelated asset. A persistent corner \
logo = set_watermark, not add_sticker.
- Split-screen / "brainrot" / doom-scroll ("ekranı böl", "alt tarafa oyun/\
parkour/Minecraft/satisfying koy", "alt yarıya gameplay") -> \
add_gameplay_background with the pack the user names (minecraft|satisfying|\
runner|ramp; default minecraft). This is the ONLY correct action for it — never \
use add_broll/b-roll to fake a split-screen.

Available actions and their args:
{actions}

Return ONLY JSON:
{{
  "summary": "<one sentence, in the user's language, of what the edit will feel like>",
  "steps": [{{"action": "<name>", "args": {{...}}, "why": "<short reason, user's language>"}}]
}}
"""


# Placement types the asset-proposer may emit -> (tool action, asset kinds).
ASSET_PLACEMENTS = {
    "broll_overlay": ("add_broll", ("video", "image")),
    "corner_logo": ("set_watermark", ("image",)),
    "audio_bed": ("set_music", ("audio",)),
    "sfx_hit": ("add_sound_effect", ("audio",)),
}

_ASSET_SYSTEM = """You are a senior short-form editor. Given the user's OWN \
asset library (catalog below) and ONE clip's transcript, propose where their \
assets would improve the edit.

HARD RULES:
- Reference assets ONLY by the exact `id` values in the catalog. NEVER invent \
ids or files. If nothing in the library fits a good slot, add a proposal with \
"asset_id": null and a "gap" describing what's missing.
- Timestamps are clip-local seconds within 0-{duration}s.
- Placements: broll_overlay (video/image full-frame cover, 2-5s, NEVER in the \
first 3s — the hook must show the speaker, max 30% of the clip covered), \
corner_logo (image with transparency as watermark), audio_bed (music under \
the voice), sfx_hit (short sound effect at an exact moment).
- Match on the MEANING of what is said at that moment, not isolated keywords.
- 1-6 proposals, each visibly motivated by the transcript or branding.
- If a USER INSTRUCTION is given, propose ONLY placements that fulfill it \
(usually a single one) — do NOT pad with other "improvements" the user did \
not ask for. Open-ended instructions ("kendi varlıklarımı kullan") may get \
the full 1-6.

Return ONLY JSON:
{{
  "summary": "<one sentence in the user's language>",
  "proposals": [{{"asset_id": "ast_xx"|null, "placement": "<type>",
    "start": <s>, "end": <s>, "why": "<short, user's language>",
    "gap": "<only when asset_id is null>"}}]
}}
"""


def propose_assets(session: Session, clip_id: int,
                   instruction: str = "") -> dict:
    """Catalog + transcript -> validated placement plan (pending_plan shape)."""
    from pipeline import assets as alib
    from pipeline.media import ffprobe_info

    catalog = alib.catalog_for_llm()
    if not catalog:
        raise ValueError("Asset library is empty — upload files first "
                         "(UI upload or ingest_assets).")
    clip = session.clip(clip_id)
    words = session.words_for(clip)
    duration = ffprobe_info(clip["current"])["duration"] \
        if clip.get("current") else (words[-1]["end"] if words else 0)

    text, t = [], None
    for w in words:
        if t is None or w["start"] - t > 2.0:
            text.append(f"\n[{w['start']:.1f}s]")
        text.append(w["word"])
        t = w["end"]

    api_key, base_url, model = config.llm_settings(
        getattr(session, "_tier", "fast"))
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system",
             "content": _ASSET_SYSTEM.format(duration=f"{duration:.0f}")},
            {"role": "user", "content":
                "ASSET CATALOG:\n" + json.dumps(catalog, ensure_ascii=False)
                + f"\n\nCLIP #{clip_id} TRANSCRIPT:\n" + " ".join(text).strip()
                + (f"\n\nUSER INSTRUCTION: {instruction}" if instruction
                   else "")},
        ],
        temperature=0.4, **config.json_response_format(base_url))
    data = config.extract_json(resp.choices[0].message.content)

    ids = {a["id"]: a for a in catalog}
    steps, gaps = [], []
    for pr in data.get("proposals", []):
        placement = pr.get("placement")
        if placement not in ASSET_PLACEMENTS:
            continue
        aid = pr.get("asset_id")
        if not aid:
            if pr.get("gap"):
                gaps.append(pr["gap"])
            continue
        if aid not in ids:  # hallucinated id -> drop, never guess
            continue
        action, kinds = ASSET_PLACEMENTS[placement]
        asset = alib.get_asset(aid)
        if asset["kind"] not in kinds:
            continue
        s = max(0.0, min(float(pr.get("start", 0)), duration - 0.5))
        e = max(s + 0.5, min(float(pr.get("end", s + 3)), duration))
        args: dict = {"clip_id": clip_id}
        if action == "add_broll":
            if s < 3.0:
                s, e = 3.0, max(e, 5.0)
            args.update({"auto": False, "file": asset["path"],
                         "start": round(s, 1), "end": round(e, 1)})
        elif action == "set_watermark":
            args.update({"file": asset["path"], "corner": "tr"})
        elif action == "set_music":
            args.update({"file": asset["path"], "volume": 0.15})
        elif action == "add_sound_effect":
            args.update({"file": asset["path"], "time": round(s, 1),
                         "volume": 0.55})
        steps.append({"action": action, "args": args,
                      "why": f"[{asset['id']}] {pr.get('why', '')}"})
    if not steps and not gaps:
        raise ValueError("No valid asset placements were proposed.")
    return {"clip_id": clip_id,
            "instruction": instruction or "asset önerisi",
            "summary": data.get("summary", ""), "steps": steps[:6],
            "gaps": gaps}


def propose(session: Session, clip_id: int, instruction: str) -> dict:
    """One JSON-mode LLM call -> validated plan dict (not yet executed)."""
    clip = session.clip(clip_id)
    words = session.words_for(clip)
    text, t = [], None
    for w in words:
        if t is None or w["start"] - t > 2.0:
            text.append(f"\n[{w['start']:.1f}s]")
        text.append(w["word"])
        t = w["end"]
    transcript = " ".join(text).strip()

    actions = "\n".join(f"- {k}: {v}" for k, v in PLAN_ACTIONS.items())
    api_key, base_url, model = config.llm_settings(
        getattr(session, "_tier", "fast"))
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)

    prefs = session.data.get("preferences", [])
    pref_block = ("\nUSER PREFERENCES (every plan must respect these): "
                  + "; ".join(prefs)) if prefs else ""

    # File-based FX (overlay/reaction/sticker/watermark, broll file=) need real
    # paths — surface the user's own asset library so the planner can fill them
    # in directly. An EMPTY catalog must still be stated explicitly: if the
    # block is simply absent the model fabricates plausible-looking paths.
    catalog_block = ("\n\nASSET CATALOG: (empty — the user has uploaded no "
                     "assets. There is NO file to use: OMIT every step that "
                     "needs a file path and name what's missing in the "
                     "summary.)")
    try:
        from pipeline import assets as alib
        catalog = alib.catalog_for_llm()
        if catalog:
            # catalog_for_llm strips `path` (id-only contract for proposals);
            # the planner fills file= args directly, so re-attach real paths.
            paths = {r["id"]: r.get("path", "") for r in alib.load_catalog()}
            for row in catalog:
                if paths.get(row["id"]):
                    row["path"] = paths[row["id"]]
            catalog_block = ("\n\nASSET CATALOG (use these exact `path` values "
                             "for any file= arg; never invent paths. Pick an "
                             "asset ONLY if its description/tags actually "
                             "match what the user asked for — if none matches, "
                             "OMIT that step and say what's missing in the "
                             "summary):\n"
                             + json.dumps(catalog, ensure_ascii=False))
    except Exception:
        pass

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system",
             "content": _SYSTEM.format(actions=actions) + pref_block},
            {"role": "user", "content":
                f"CURRENT STATE:\n{session.summary()}\n\n"
                f"CLIP #{clip_id} TRANSCRIPT (current timeline):\n{transcript}"
                f"{catalog_block}\n\n"
                f"INSTRUCTION: {instruction}"},
        ],
        temperature=0.4,
        **config.json_response_format(base_url),
    )
    data = config.extract_json(resp.choices[0].message.content)

    steps, dropped = [], []
    for s in data.get("steps", []):
        action = s.get("action")
        if action not in PLAN_ACTIONS:
            continue
        args = s.get("args") or {}
        if not isinstance(args, dict):
            continue
        # file= must be a real on-disk path (set_music excepted — it resolves
        # bare track names itself). A fabricated path would only surface as a
        # "not found" failure at apply time; drop it here instead.
        f = args.get("file")
        if f and action != "set_music" and not Path(f).exists():
            dropped.append(f"{action} (no such file: {f})")
            continue
        args["clip_id"] = clip_id  # the plan is scoped to ONE clip
        steps.append({"action": action, "args": args,
                      "why": s.get("why", "")})
    # A factor the user NAMED ("3x", "0.5x", "10 kat") is unambiguous —
    # enforce it deterministically (clamped to the tool's range); the LLM
    # sometimes substitutes a "safer" value, silently changing the ask.
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:x\b|kat\b)", instruction, re.I)
    if m:
        named = max(0.25, min(4.0, float(m.group(1).replace(",", "."))))
        for st in steps:
            if st["action"] == "set_speed":
                st["args"]["factor"] = named
    if not steps:
        msg = "Planner produced no valid steps."
        if dropped:
            msg += (" Dropped steps referencing files that don't exist: "
                    + "; ".join(dropped)
                    + ". The needed asset is probably missing from the user's "
                      "library (list_assets / ingest_assets).")
        if data.get("summary"):
            msg += f" Planner note: {data['summary']}"
        raise ValueError(msg)
    return {"clip_id": clip_id, "instruction": instruction,
            "summary": data.get("summary", ""), "steps": steps[:7]}
