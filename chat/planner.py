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

# propose() is a BOUNDED tool-using re-plan loop: at most this many LLM replies
# total (1 = today's one-shot; the rest are moment lookups + validator-feedback
# refinements). The last round is always a plain validate-and-take pass, so the
# loop terminates and never exceeds MAX_PLAN_ROUNDS calls.
MAX_PLAN_ROUNDS = 3

# Actions whose time args are NOT clip-local player seconds (set_cut is SOURCE
# seconds) — exempt from the dry-run time_out_of_range bound.
_TIME_EXEMPT_ACTIONS = ("set_cut",)
# Actions with a real start<end span we can sanity-check for an empty range.
_RANGE_ACTIONS = ("remove_section", "set_cut", "add_broll")
# Stage edited by an event-index action -> the clip["stages"] params key whose
# list length bounds the index (mirrors tools._EDITABLE_EVENTS).
_EVENT_STAGE_KEY = {"zoom": "windows", "broll": "events", "overlay": "events",
                    "fx": "events", "sfx": "events"}

# Actions the planner may emit — name -> (one-line contract shown to the LLM).
# These are exactly chat tool names; apply_plan dispatches through REGISTRY.
PLAN_ACTIONS: dict[str, str] = {
    "apply_style": '{"style": "hormozi|mrbeast|podcast_minimal|kinetic"} — '
                   'whole-look preset (captions+pacing+zooms+music+sfx)',
    "cut_silences": '{"max_pause": 0.3-0.7} — tighten/loosen pause removal',
    "remove_fillers": '{} — cut hesitation sounds (um/uh/ee)',
    "remove_section": '{"start": s, "end": s} — delete a span '
                      '(current-timeline seconds)',
    "remove_phrase": '{"description": "<what the speaker says>", '
                     '"occurrence": "first|all"} — delete the sentence/part '
                     'described by CONTENT (resolves the span semantically, '
                     'no times needed); use when the user names WHAT was said '
                     '("X dediği cümleyi sil") rather than a time range',
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
    "add_meme_text": '{"text": "<the meme headline>", "position": "top|bottom", '
                     '"bar": true|false, "font": "impact", "duration": 0} — an '
                     'Instagram-style meme HEADLINE the user writes (NOT the '
                     'spoken karaoke subtitles). bar=true = classic white bar '
                     'with black text; bar=false = white Impact text with a '
                     'thick black outline over the video. duration 0 = whole '
                     'clip. Use when the user gives literal top/bottom meme text '
                     '("üste şunu yaz", "meme yazısı: ...", "white bar caption")',
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
    "set_aspect": '{"aspect": "9:16|1:1|16:9"} — reframe the clip to a '
                  'different aspect ratio (9:16 dikey/default, 1:1 kare, 16:9 '
                  'yatay); keeps speaker tracking and re-renders captions on '
                  'the new canvas',
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
    "edit_event": '{"stage": "zoom|broll|overlay|fx|sfx", "index": <0-based '
                  'event number from CURRENT STATE>, "start": s, "end": s, '
                  '"value": <zoom strength 1.1-1.3 | sfx volume 0-1 | overlay '
                  'opacity>, "motion": "center|left|right|up|down"} — '
                  'move/resize/retune ONE EXISTING event; include only the '
                  'fields you change',
    "delete_event": '{"stage": "zoom|broll|overlay|fx|sfx", "index": <0-based>} '
                    '— remove one existing event ("ikinci zoomu sil"); in a '
                    'multi-step plan, deletes come LAST so earlier indices stay '
                    'valid',
}

_SYSTEM = """You are a senior short-form video editor. The user gives a vague \
"vibe" instruction for ONE clip. Produce a SHORT plan of tool calls that \
realizes it. You see the clip's CURRENT edit state and transcript — edit \
RELATIVE to that state (e.g. raise the existing music volume, don't reset the \
track; keep what already matches the vibe).

CAPABILITY MAP (high-level intent -> action; the detailed arg specs are the \
authoritative list below — use it for exact names/ranges):
- Trim/clean speech: cut_silences, remove_fillers, remove_section (by time), \
remove_phrase (by what was SAID), set_cut (re-cut bounds).
- Motion/emphasis: auto_zoom, add_zoom, add_emphasis, set_speed.
- Look/sound: set_look (color grade), set_music, add_sound_effect, set_fade, \
set_loudness, set_denoise.
- Captions/titles: set_subtitles (size/position/color/karaoke), \
set_title_card, add_meme_text (literal top/bottom meme headline the user \
writes — white bar or Impact outline), fix_transcript (correct ASR text, keep \
timing).
- Framing: set_aspect (9:16 / 1:1 / 16:9 reframe).
- Assets/FX: add_broll (stock/user cover footage), add_overlay, add_reaction, \
add_sticker, set_watermark, add_gameplay_background (split-screen gameplay).
- Edit existing events: edit_event / delete_event (use the 0-based indices \
from CURRENT STATE; deletes go last).
Canonical examples: "yarı hıza al" -> set_speed factor=0.5; "kare yap" -> \
set_aspect aspect=1:1; "X dediği cümleyi sil" -> remove_phrase; "altyazıyı \
büyüt ve yukarı al" -> set_subtitles {{scale, y_ratio}}; "sinematik görünüm" -> \
set_look look=cinematic. When unsure between two actions, prefer the one whose \
arg spec below best matches the user's words.

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

MOMENT LOOKUP: when a step needs a TIME you cannot read confidently from the \
transcript [Xs] markers (e.g. "the part where he mentions the price", "right \
after the greeting"), do NOT guess. Return JSON with a "lookup" array — \
{{"lookup": [{{"id": "m1", "description": "<what is said/happens there>"}}]}} \
— optionally alongside draft "steps". You will then receive LOOKUP RESULTS with \
exact clip-local {{start,end}} spans for each id; reply with the corrected FULL \
plan that uses those numbers.

VALIDATOR FEEDBACK: you may receive a list of PROBLEMS found in your steps \
(unknown action, missing file, a time outside the clip, an empty range, too \
many steps, a bad event index). When you do, return the corrected COMPLETE plan \
— ALL steps in the same JSON shape, not a diff. You have at most {rounds} \
replies total, so converge quickly.

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


def _transcript_block(words: list[dict]) -> str:
    """Render words as the planner-facing transcript with [Xs] gap markers."""
    text, t = [], None
    for w in words:
        if t is None or w["start"] - t > 2.0:
            text.append(f"\n[{w['start']:.1f}s]")
        text.append(w["word"])
        t = w["end"]
    return " ".join(text).strip()


def _validate_steps(clip_id: int, data: dict, duration: float,
                    clip: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Dry-run the LLM's steps against the whitelist + cheap bounds.

    Returns (valid_steps, issues). Valid steps get clip_id injected (the plan is
    scoped to ONE clip). issues is a list of STRUCTURED problems to feed back to
    the model so it can self-correct, instead of silently dropping the step. The
    same checks back the post-loop hard drop, so a step that survives here is
    safe for apply_plan.
    """
    steps: list[dict] = []
    issues: list[dict] = []
    raw = data.get("steps") or []
    for i, s in enumerate(raw):
        action = s.get("action")
        if action not in PLAN_ACTIONS:
            issues.append({"step": i, "action": action,
                           "problem": "unknown_action",
                           "hint": "choose only from the documented actions"})
            continue
        args = s.get("args")
        if not isinstance(args, dict):
            issues.append({"step": i, "action": action, "problem": "bad_args",
                           "hint": "args must be a JSON object"})
            continue
        # file= must be a real on-disk path (set_music excepted — it resolves
        # bare track names itself). A fabricated path would only surface as a
        # "not found" failure at apply time.
        f = args.get("file")
        if f and action != "set_music" and not Path(f).exists():
            issues.append({"step": i, "action": action,
                           "problem": "missing_file", "path": f,
                           "hint": "use an exact ASSET CATALOG path or omit "
                                   "the step"})
            continue
        # Cheap time bounds: clip-local player seconds must sit in [0, dur+slack]
        # (set_cut speaks SOURCE seconds — exempt). add_broll end may run a touch
        # past by design padding, so the +0.5 slack absorbs it.
        bad_time = None
        if action not in _TIME_EXEMPT_ACTIONS:
            for key in ("time", "start", "end"):
                v = args.get(key)
                if isinstance(v, (int, float)) and (
                        v < 0 or v > duration + 0.5):
                    bad_time = v
                    break
        if bad_time is not None:
            issues.append({"step": i, "action": action,
                           "problem": "time_out_of_range", "got": bad_time,
                           "clip_duration": round(duration, 1),
                           "hint": "times are CLIP-LOCAL player seconds; use "
                                   "lookup to resolve a moment"})
            continue
        # Empty range on a span action.
        if action in _RANGE_ACTIONS:
            st, en = args.get("start"), args.get("end")
            if isinstance(st, (int, float)) and isinstance(en, (int, float)) \
                    and en <= st:
                issues.append({"step": i, "action": action,
                               "problem": "empty_range", "start": st,
                               "end": en, "hint": "end must be after start"})
                continue
        # Event-index actions (edit_event/delete_event) reference an EXISTING
        # event — bound the index against the live stage list length.
        if action in ("edit_event", "delete_event") and clip is not None:
            stage = args.get("stage")
            idx = args.get("index")
            key = _EVENT_STAGE_KEY.get(stage)
            if key is not None and isinstance(idx, int):
                stg = next((x for x in clip.get("stages", [])
                            if x["name"] == stage), None)
                have = len(stg["params"].get(key, [])) if stg else 0
                if not 0 <= idx < have:
                    issues.append({"step": i, "action": action,
                                   "problem": "event_index_out_of_range",
                                   "stage": stage, "index": idx, "have": have,
                                   "hint": "use a 0-based index that exists in "
                                           "CURRENT STATE, or omit the step"})
                    continue
        args["clip_id"] = clip_id
        steps.append({"action": action, "args": args, "why": s.get("why", "")})
    if len(steps) > 7:
        issues.append({"problem": "too_many_steps", "count": len(steps),
                       "hint": "merge or drop to <=7 steps"})
    return steps, issues


def propose(session: Session, clip_id: int, instruction: str,
            extra_note: str = "") -> dict:
    """Bounded tool-using re-plan loop -> validated plan dict (not executed).

    Emits a plan, dry-runs it through _validate_steps, and feeds any structured
    issues (and any requested moment lookups) back to the model to refine —
    capped at MAX_PLAN_ROUNDS LLM replies. The happy path (clean first plan, no
    lookups) is EXACTLY one call, identical in cost to the old one-shot. The
    pending_plan shape ({clip_id, instruction, summary, steps}) is unchanged;
    additive keys (refinements/resolved_moments) are ignored downstream.

    extra_note (additive): an extra steering line appended to the user message
    — used by regenerate_plan to ask for a NOTICEABLY different approach. The
    returned plan keeps the CLEAN instruction so downstream records stay
    canonical."""
    clip = session.clip(clip_id)
    words = session.words_for(clip)
    transcript = _transcript_block(words)
    # Plan times are CLIP-LOCAL PLAYER seconds: prefer the rendered duration,
    # else the pre-speed transcript end mapped into the sped timeline.
    from pipeline.media import ffprobe_info
    if clip.get("current"):
        duration = ffprobe_info(clip["current"])["duration"]
    else:
        duration = (words[-1]["end"] / session.speed_factor(clip)) \
            if words else 0.0

    actions = "\n".join(f"- {k}: {v}" for k, v in PLAN_ACTIONS.items())
    # PLANNER_TIER (env opt-in) lets the proposer run on the stronger model for
    # sharper intent routing; unset = inherit the chat turn's tier (today's
    # behavior). llm_settings falls back pro->fast when no pro model exists.
    tier = config.PLANNER_TIER or getattr(session, "_tier", "fast")
    api_key, base_url, model = config.llm_settings(tier)
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

    messages: list[dict] = [
        {"role": "system",
         "content": _SYSTEM.format(actions=actions, rounds=MAX_PLAN_ROUNDS)
         + pref_block},
        {"role": "user", "content":
            f"CURRENT STATE:\n{session.summary()}\n\n"
            f"CLIP #{clip_id} TRANSCRIPT (current timeline):\n{transcript}"
            f"{catalog_block}\n\n"
            f"INSTRUCTION: {instruction}"
            + (f"\n\nNOTE: {extra_note}" if extra_note else "")},
    ]

    # Bounded re-plan loop: emit -> dry-run validate / resolve lookups -> refine.
    data: dict = {}
    steps: list[dict] = []
    issues: list[dict] = []
    rounds_used = 0
    resolved_moments = 0
    for round_i in range(MAX_PLAN_ROUNDS):
        rounds_used += 1
        last = round_i == MAX_PLAN_ROUNDS - 1
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.4,
            **config.json_response_format(base_url))
        content = resp.choices[0].message.content
        try:
            data = config.extract_json(content)
        except ValueError:
            if last:
                break
            messages.append({"role": "assistant", "content": content or ""})
            messages.append({"role": "user", "content":
                             "Your reply was not valid JSON. Return ONLY the "
                             "JSON object described above, nothing else."})
            continue

        # Moment lookup: resolve descriptions to exact clip-local spans, then
        # let the model emit the corrected plan with real numbers.
        lookups = data.get("lookup") or []
        if lookups and not last:
            from chat.tools import _find_moment_core  # runtime: avoid cycle
            resolved: dict[str, list[dict]] = {}
            for lk in lookups:
                lid = str(lk.get("id") or f"m{len(resolved) + 1}")
                desc = str(lk.get("description") or "")
                try:
                    spans = _find_moment_core(session, clip, desc, limit=2)
                except Exception:
                    spans = []
                resolved[lid] = spans
                resolved_moments += 1
            messages.append({"role": "assistant", "content": content or ""})
            messages.append({"role": "user", "content":
                             "LOOKUP RESULTS (clip-local player seconds): "
                             + json.dumps(resolved, ensure_ascii=False)
                             + "\nNow return the corrected FULL plan."})
            continue

        steps, issues = _validate_steps(clip_id, data, duration, clip)
        if issues and not last:
            messages.append({"role": "assistant", "content": content or ""})
            messages.append({"role": "user", "content":
                             "VALIDATOR FEEDBACK — fix these and return the "
                             "FULL corrected plan as the same JSON shape: "
                             + json.dumps(issues, ensure_ascii=False)})
            continue
        break

    # Deterministic post-loop, exactly once. A factor the user NAMED ("3x",
    # "0.5x", "10 kat") is unambiguous — enforce it (clamped to the tool's
    # range); the LLM sometimes substitutes a "safer" value, changing the ask.
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:x\b|kat\b)", instruction, re.I)
    if m:
        named = max(0.25, min(4.0, float(m.group(1).replace(",", "."))))
        for st in steps:
            if st["action"] == "set_speed":
                st["args"]["factor"] = named
    steps = steps[:7]
    if not steps:
        msg = "Planner produced no valid steps."
        if issues:
            msg += (" Unresolved issues after refinement: "
                    + json.dumps(issues, ensure_ascii=False)
                    + ". A referenced asset may be missing from the user's "
                      "library (list_assets / ingest_assets).")
        if data.get("summary"):
            msg += f" Planner note: {data['summary']}"
        raise ValueError(msg)
    return {"clip_id": clip_id, "instruction": instruction,
            "summary": data.get("summary", ""), "steps": steps,
            "refinements": rounds_used - 1,
            "resolved_moments": resolved_moments}


# multiclip_plans — how many clips one project-scope proposal may span. Each
# clip costs at least one planner LLM call (propose loops per clip), so the set
# is bounded; the agent should warn on long sources before going wider.
MAX_PROJECT_CLIPS = 8


def _project_clip_ids(session: Session,
                      clip_ids: list[int] | None = None) -> list[int]:
    """Resolve the target clip set for a project-scope plan. Explicit ids are
    honored (validated, de-duped, order-preserved); otherwise the candidates
    are every NON-skipped clip in ranked order, capped at MAX_PROJECT_CLIPS."""
    clips = session.data.get("clips") or []
    if clip_ids:
        valid = {c["id"] for c in clips}
        seen: set[int] = set()
        out: list[int] = []
        for cid in clip_ids:
            if cid in valid and cid not in seen:
                seen.add(cid)
                out.append(cid)
        return out[:MAX_PROJECT_CLIPS]
    out = [c["id"] for c in clips
           if Session.clip_status(c) != "skipped"]
    return out[:MAX_PROJECT_CLIPS]


def propose_project(session: Session, instruction: str,
                    clip_ids: list[int] | None = None,
                    extra_note: str = "") -> dict:
    """Project-scope plan: run the SAME single-clip propose() over the target
    clips ("tighten every clip") and assemble a composite pending_plan shape
    ({'scope':'project', 'instruction', 'plans':[<plan dict>, ...]}).

    Looping the existing propose() reuses ALL of its validation, factor
    enforcement and bounded re-plan loop per clip. Clips that yield no valid
    plan (e.g. a step needed an asset they lack, or the clip is picture-locked)
    are skipped with a note rather than failing the whole batch; raises only
    when NO clip produced any step."""
    targets = _project_clip_ids(session, clip_ids)
    if not targets:
        raise ValueError("No eligible clips for a project-scope plan.")
    plans: list[dict] = []
    skipped: list[str] = []
    for cid in targets:
        try:
            plans.append(propose(session, cid, instruction,
                                  extra_note=extra_note))
        except ValueError as e:
            skipped.append(f"#{cid}: {e}")
    if not plans:
        raise ValueError(
            "No clip produced a valid plan. " + " | ".join(skipped))
    summary = (f"Applies '{instruction}' across {len(plans)} clip(s)"
               + (f"; skipped {len(skipped)}" if skipped else "") + ".")
    return {"scope": "project", "instruction": instruction,
            "summary": summary, "plans": plans,
            "skipped": skipped or None}
