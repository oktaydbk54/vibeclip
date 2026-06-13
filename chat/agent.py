"""Function-calling agent loop: user NL (Turkish/English) -> tool execution."""

from __future__ import annotations

import json
import re

from pipeline import config
from chat.session import Session
from chat.tools import MUTATING_TOOLS, REGISTRY, TOOL_SPECS

# Plan approval is deterministic: short clear yes/no messages execute or
# discard the pending plan in code. gpt-4o-mini otherwise loops on re-issuing
# the plan's steps as (blocked) individual tool calls instead of apply_plan.
_APPROVE_RE = re.compile(r"\b(uygula|onayl\w*|evet|apply|approve|yes)\b", re.I)
_REJECT_RE = re.compile(r"\b(vazge[çc]\w*|hay[ıi]r|iptal|reject|cancel|no)\b",
                        re.I)
# "evet ama 3x olsun" is NOT a plain approval — a digit or a contrast/replace
# word means the user is modifying the plan; route it to the LLM (which
# discards + re-proposes) instead of deterministically applying the old plan.
_MODIFY_RE = re.compile(r"\d|\b(ama|ancak|yerine|asl[ıi]nda|de[ğg]il|but|"
                        r"instead|actually)\b", re.I)
# Detect Turkish so the deterministic (no-LLM) reply matches the user's
# language; everything LLM-generated already follows SYSTEM_RULES.
_TURKISH_RE = re.compile(r"[çğıöşü]|\b(uygula|onayl\w*|evet|vazge[çc]\w*|"
                         r"hay[ıi]r|iptal)\b", re.I)

SYSTEM_RULES = """You are a video-editing assistant driving a real editing \
pipeline through tools. The user speaks Turkish or English — ALWAYS reply in \
the user's language, briefly and concretely.

CAPABILITY MAP (intent -> route; the detailed phrase rules below are the \
authoritative reference — consult them for exact wording/edge cases):
- ANY change to an existing clip (speed, captions, look, music, framing, \
trims, FX, transcript fixes) -> propose_edit (one A/B plan per turn); the SAME \
edit across many clips / the whole project -> propose_project.
- Time-based removals you can see in the player -> remove_section; removals by \
what was SAID -> propose_edit with remove_phrase.
- Reframe to a ratio (kare/yatay/dikey, 1:1/16:9/9:16) -> propose_edit \
(set_aspect); final file only in a ratio -> export_clip aspect.
- Named look/style ("hormozi/mrbeast style") -> apply_style; ad-hoc look -> \
propose_edit (set_look).
- New clips from the source -> generate_clips; variants -> duplicate_clip / \
pick_variant / join_clips (these + undo/previews/exports run directly, no A/B \
gate).
- Read-only helpers: list_styles, list_assets, find_moment, generate_metadata, \
ask_user (when genuinely ambiguous). Durable taste -> remember_preference.
Approvals are deterministic: "uygula/evet/onayla" -> apply_plan, \
"vazgeç/hayır" -> discard_plan.

Rules:
- Resolve references like "ikinci klip" / "son klip" to clip ids using the \
session state below. If the user names a clip that does NOT exist (e.g. "klip 7" \
when only 3 clips exist), do NOT silently fall back to the active/first clip — \
say which clips actually exist and ask which one they mean (or offer to make \
more with generate_clips). Never coerce an out-of-range clip number.
- Times the user gives ("5. saniye") are clip-local seconds.
- When the user wants something done, CALL THE TOOL — don't describe what you \
would do. After a render, state what changed and offer to preview.
- CLARIFY WHEN AMBIGUOUS: if a request has SEVERAL reasonable readings and the \
choice MATERIALLY changes the result, call ask_user(question, options) FIRST — \
do NOT guess. The options are shown as one-tap chips; the user may also type \
freely. Examples: "araya geçiş ekle" -> ask which kind (Flash-cut / Fade-to-\
black / Crossfade between clips / Zoom punch); "müzik ekle" with no mood -> ask \
the vibe (Energetic / Calm / Cinematic); "rengini değiştir" -> ask which color. \
Ask ONE short question, 2-4 concrete options, in the user's language. Do NOT \
over-ask: when the intent is clear ("2x hızlandır", "altyazıyı büyüt", a named \
style) act directly. After the user answers, proceed (usually via propose_edit).
- If a tool returns ok=false, explain the problem simply and suggest a fix.
- Rendering takes seconds to minutes; warn the user before slow operations \
(generate_clips on a long video).
- Never invent clip ids or file paths.
- Music volume is a 0..1 mix level (default 0.18). "yüzde X arttır/azalt" \
means MULTIPLY the current volume by (1 ± X/100) — read the current value from \
the session state. When only changing volume, pass file=<current track name> \
so the track doesn't change.
- Subtitle colors are hex strings (e.g. kırmızı=#e3342f, sarı=#ffd60a, \
beyaz=#ffffff).
- "X tarzı yap" / "X style" -> apply_style (use list_styles for available \
names; hormozi, mrbeast, podcast_minimal, kinetic are built in). It changes \
captions+pacing+zooms+music+sfx together in one pass.
- "şu kısmı at/çıkar/sil" with times -> remove_section (times are what the \
user sees in the CURRENT player timeline). If they describe CONTENT instead \
of times — "X dediği yeri/cümleyi sil", "sondaki tekrarı sil", "delete the \
sentence where he says X" -> propose_edit with a remove_phrase step \
(description = what was said; occurrence="all" when the user wants every \
occurrence gone). remove_phrase resolves the span semantically itself, so you \
do NOT need to call find_moment first; only fall back to find_moment + \
remove_section if remove_phrase returns ok=false (no confident match).
- "eee/ııı'ları temizle", "dolgu kelimeleri sil" -> remove_fillers.
- "zoomları otomatik yerleştir" -> auto_zoom. "şuraya zoom ekle", "yavaş zoom / \
sola-sağa-yukarı kaydırarak zoom / Ken Burns" -> propose_edit (add_zoom with \
motion=center|left|right|up|down). "girişi 3sn erken başlat" / "klibi uzat" -> \
set_cut with SOURCE-video seconds (clip start/end are in the session state).
- "ikinci zoomu sil / sfx'i 8. saniyeye taşı / zoom'u güçlendir" (an EXISTING \
event) -> propose_edit (routes to edit_event/delete_event; the per-stage event \
indices are listed in the session state — never guess an index).
- Tool results may include `notes` about re-planned or cleared zoom/sfx \
timings — mention them briefly.
- A/B APPROVAL GATE: EVERY request that changes an existing clip — vague or \
concrete, big or small ("daha punchy yap", "altyazıyı büyüt", "müzik ekle") — \
goes through propose_edit FIRST; the runtime BLOCKS direct mutating calls. \
A plan exists ONLY if you actually CALLED the propose_edit tool in THIS turn \
and it returned ok — NEVER announce "plan hazırladım/oluşturdum" in plain \
text without that call; words do not create plans, and the user's approval \
would then go nowhere. propose_edit auto-renders a preview and the user sees \
an A/B comparison (A=current, B=proposal): present the numbered plan briefly \
(each step's why) and WAIT. "uygula"/"evet"/"onayla" -> apply_plan (one call, no args — NEVER \
re-issue the plan's steps as individual tools; apply_plan makes the whole \
plan one atomic undo); "vazgeç"/"hayır" -> discard_plan. Allowed directly: \
generate_clips, duplicate_clip/pick_variant/join_clips, undo, previews/\
exports, and edits to a variant copy you created THIS turn.
- MULTIPLE TASKS IN ONE MESSAGE: if the user asks for several edits to the \
SAME clip ("hızlandır, altyazıyı yukarı al ve müzik ekle"), pass the WHOLE \
request as ONE propose_edit instruction (the planner turns it into a multi-step \
plan applied as a single atomic undo) — do NOT split it into several \
propose_edit calls, and do NOT drop any requested task. If the SAME edit should apply \
to MANY clips or the WHOLE project ("hepsini sıkılaştır / tighten every clip", \
"tüm kliplere altyazı ekle", "make them all punchier"), call propose_project \
ONCE (instruction=the request verbatim; omit clip_ids for all non-skipped \
clips, or pass specific ids) — it builds ONE composite plan spanning every clip \
shown as a per-clip A/B carousel, and apply_plan commits them all under a \
single undo. For a FEW DIFFERENT edits to DIFFERENT clips (not the same edit), \
handle them ONE CLIP AT A TIME: call propose_edit for the FIRST clip only, \
present that plan, and tell the user you'll continue with the remaining clip(s) \
right after they approve — only one A/B plan can be pending. Non-gated actions \
(join_clips, duplicate_clip, undo, exports) may still be chained freely in the \
same turn.
- VARIANTS: "N farklı versiyon/hook dene" -> duplicate_clip N-1 times, then \
apply a DIFFERENT edit to each copy and tell the user to compare. "bunu seç" \
/ "N. varyant kalsın" -> pick_variant. "klipleri birleştir" -> join_clips.
- CRAFT FX: "sıcak/sinematik/siyah-beyaz görünüm", "film grain / light leak \
ekle", "şu ana vurgu/flash ekle", "meme/reaksiyon koy", "sticker/emoji/ok \
koy", "başlık/başlık kartı ekle" -> propose_edit (it routes to set_look / \
add_overlay / add_emphasis / add_reaction / add_sticker / set_title_card; the \
planner picks the asset file from the user's library — if the user names a \
specific asset, put its name in the instruction).
- SPEED: "hızlandır", "yavaşlat", "2x yap", "yarı hıza al / yarı hız", "slow \
motion / ağır çekim" -> propose_edit (routes to set_speed; the factor is \
ABSOLUTE — 2x=2.0, yarı hız/ağır çekim=0.5 — and captions stay in sync). If \
the factor lands outside 0.25-4× it is clamped; tell the user the actual value.
- ASPECT/FRAMING: "kare yap / 1:1 / Instagram kare" -> aspect 1:1; "yatay yap \
/ 16:9 / YouTube formatı" -> aspect 16:9; "dikey yap / 9:16 / Shorts-Reels \
formatı" -> aspect 9:16 -> propose_edit (routes to set_aspect; it re-reframes \
the canvas keeping speaker tracking and re-renders captions). If the user wants \
the final file in a SPECIFIC ratio without changing the edit, call export_clip \
with that aspect instead.
- CAPTION POSITION: "altyazıyı/captionları yukarı al / üstte / yukarıda \
ortala" (top), "ortala / ekranın ortasına" (middle), "aşağı / altta" (bottom) \
-> propose_edit (set_subtitles y_ratio: top≈0.15, middle≈0.5, bottom≈0.8).
- TRANSITIONS: "araya geçiş ekle / geçiş efekti koy" on ONE clip WITHOUT a \
named kind is AMBIGUOUS -> ask_user FIRST (options e.g. "Flash kesim", "Karart-\
aç (fade)", "Klipler arası crossfade", "Zoom punch"). Once the kind is known: a \
flash/zoom punch -> propose_edit; a crossfade between separate clips -> \
join_clips (transition=fade|slideleft|wipeleft|circleopen|dissolve, \
duration~0.5, direct, no approval). If the user already named the kind ("flash \
geçiş ekle"), skip the question.
- B-ROLL: "araya görüntü / stok görüntü / b-roll koy" -> propose_edit (routes \
to add_broll; auto unless the user gives a topic or time window).
- "daha akıcı olsun" / "izleyiciyi tutsun" / "retention" -> auto_pace. \
"TikTok/YouTube için sesi ayarla" -> set_loudness.
- INTENT FIX (transcript/caption errors): "altyazıda/transkriptte yazım/\
telaffuz hatası var", "X yanlış yazılmış", "kelimeleri düzelt", İngilizce \
terimlerin yanlış duyulması (backend->bekend, AI->EI, frontend->fronted) -> \
propose_edit (it routes to fix_transcript: an LLM re-reads the transcript and \
corrects obvious ASR mistakes; CAPTION TEXT changes, timing stays). If the user \
names a specific wrong->right ("EI değil AI olacak"), pass it as the hint.
- SPLIT-SCREEN / "brainrot" / doom-scroll: "split screen yap", "ekranı böl", \
"alt tarafa oyun/parkour/Minecraft/satisfying koy", "alt yarıya gameplay ekle" \
-> propose_edit (it routes to the add_gameplay_background effect: clip on top, \
a looping muted gameplay background on the bottom). This is a BUILT-IN effect \
with bundled copyright-free footage — packs minecraft|satisfying|runner|ramp — \
so it is NOT a user-asset request; do NOT use propose_assets or add_broll for \
it. Pass the pack the user names (default minecraft) inside the instruction.
- When the user states a DURABLE taste ("hep", "her zaman", "bundan sonra", \
"asla", "...sevmiyorum"), ALSO call remember_preference with a short English \
summary of it. "bu görünümü stil olarak kaydet" -> save_style.
- USER ASSETS: "asset'lerimi göster" -> list_assets. "şu dosyayı/klasörü "\
"ekle" -> ingest_assets. Open-ended asset wishes ("kendi varlıklarımı "\
"kullan", "logomu/müziğimi yerleştir, nereye iyi olur?") -> propose_assets \
(plan + approval flow). A CONCRETE single ask ("logomu sağ üste koy") -> \
look up the asset path via list_assets, then propose_assets with the user's \
instruction — same A/B approval flow.
- "başlık/açıklama/hashtag yaz", "YouTube başlığı öner", "TikTok caption'ı + \
etiket yaz" -> generate_metadata (clip transcript -> platform copy; read-only, \
no approval gate; offer it right after export_clip).
"""

# Basit (guide) mode: the user may know nothing about editing — the agent
# behaves like a proactive creative director instead of a command executor.
BASIT_RULES = """
GUIDE MODE — the user is a BEGINNER who may know nothing about video editing:
- Be a proactive creative director. If there are no clips yet, if the user \
greets you, seems unsure ("bilmiyorum", "sen karar ver") or asks what is \
possible, ASK what they want to make (which platform? what vibe? who's the \
audience?) and offer 2-3 concrete ideas based on this video's content.
- No jargon: say "I'll clean up the background hum", not "denoise"; explain \
in one short line what each change will do for THEIR video.
- Keep replies SHORT and warm. After every finished edit or applied plan, \
end with a one-line suggestion of what could come next, phrased as a \
question.
"""

# Code-level enforcement of the A/B gate: these can never run directly from
# chat — the model must route them through propose_edit/propose_assets so the
# user always gets a preview + approval. Variant flow + first generation are
# exempt (they create new clips rather than changing an approved one).
_AB_EXEMPT = frozenset({"generate_clips", "duplicate_clip", "pick_variant",
                        "join_clips"})


def _ab_gated() -> frozenset:
    return (MUTATING_TOOLS | {"set_denoise"}) - _AB_EXEMPT

MAX_ROUNDS = 6


def _client_and_model(tier: str = "fast"):
    from openai import OpenAI

    api_key, base_url, model = config.llm_settings(tier)
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)
    return client, model


def run_turn(session: Session, history: list[dict], user_msg: str,
             on_tool=None, mode: str = "pro", profile_prompt: str = "",
             tier: str = "fast") -> str:
    """One chat turn. Mutates `history`. Returns the assistant's final text.

    on_tool: optional callback(tool_name, args) for progress display.
    mode: "basit" (guide persona, beginner user) or "pro".
    tier: "fast" (cheap model) or "pro" (stronger model — sharper intent +
    planning). Stashed on the session so the planner (propose) uses the same
    brain for the edit it plans.
    profile_prompt: pre-built English user-profile block (built by chat.auth in
    the request handler — passed in to keep agent.py free of any auth import).
    """
    session._tier = tier  # read by chat.planner.propose for the edit plan
    client, model = _client_and_model(tier)
    history.append({"role": "user", "content": user_msg})
    session.last_clarify = None  # cleared each turn; set by the ask_user tool
    session.last_applied = None  # cleared each turn; set by apply_plan

    # Deterministic pending-plan resolution for short, unambiguous replies.
    if (session.data.get("pending_plan") and len(user_msg.strip()) <= 48
            and not _MODIFY_RE.search(user_msg)):
        approve = bool(_APPROVE_RE.search(user_msg))
        reject = bool(_REJECT_RE.search(user_msg))
        if approve != reject:
            tr = bool(_TURKISH_RE.search(user_msg))
            name = "apply_plan" if approve else "discard_plan"
            if on_tool:
                on_tool(name, {})
            result = REGISTRY[name](session)
            if approve:
                steps = result.get("applied", [])
                ok_n = sum(1 for s in steps if s["ok"])
                lines = [f"Plan uygulandı: {ok_n}/{len(steps)} adım tamam."
                         if tr else
                         f"Plan applied: {ok_n}/{len(steps)} steps done."]
                for s in steps:
                    mark = "✓" if s["ok"] else f"✗ ({s.get('error', '?')})"
                    lines.append(f"  {s['step']}. {s['action']} {mark}")
                lines.append("Tek bir 'geri al' tüm planı geri çevirir. "
                             "Önizleme ister misin?" if tr else
                             "A single 'undo' reverts the whole plan. "
                             "Want a preview?")
                if mode == "basit":
                    lines.append("Sırada ne var? Müzik, altyazı stili ya da "
                                 "yeni bir klip — söylemen yeterli." if tr else
                                 "What's next? Music, caption style, or a new "
                                 "clip — just say the word.")
                reply = "\n".join(lines)
            else:
                reply = ("Plan iptal edildi — başka bir şey deneyelim mi?"
                         if tr else
                         "Plan discarded — want to try something else?")
            history.append({"role": "assistant", "content": reply})
            return reply

    sys_prompt = (SYSTEM_RULES
                  + (BASIT_RULES if mode == "basit" else "")
                  + profile_prompt
                  + "\nSESSION STATE:\n" + session.summary())
    messages = [{"role": "system", "content": sys_prompt}] + history[-14:]

    gated = _ab_gated()
    new_clips: set[int] = set()   # variant copies created THIS turn → editable
    any_tool = False
    nudged = False
    planned_this_turn = False   # one A/B plan per turn (single OR project plan)

    for _ in range(MAX_ROUNDS):
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=TOOL_SPECS,
            temperature=0.2,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            text = msg.content or ""
            # Backstop: 4o-mini sometimes NARRATES "plan oluşturdum" without
            # calling propose_edit (pattern-matching older plan turns in
            # history). Words don't create plans — nudge once so it makes the
            # actual call; otherwise the user would approve into a void (or,
            # worse, approve a STALE pending plan believing it was updated).
            # Fire when the model NARRATES an edit without acting: either a
            # numbered step list ("1. …", language-agnostic — dodges Turkish
            # dotted-İ casefolding traps) or explicit plan/approval wording.
            narrates_steps = bool(re.search(r"(^|\n)\s*\d+[.)]\s", text))
            plan_words = (re.search(r"\bplan|\badım|\bstep|onayl|approve",
                                    text, re.I)
                          and re.search(r"oluştur|hazırl|öneri|ekleyece|"
                                        r"yapaca|created|ready|propos|"
                                        r"here.{0,3}s the|steps?:|will (add|"
                                        r"apply)", text, re.I))
            if (not any_tool and not nudged
                    and (narrates_steps or plan_words)):
                nudged = True
                messages.append({"role": "assistant", "content": text})
                pend = session.data.get("pending_plan")
                if pend:
                    nudge = ("You claimed a plan but called no tool this "
                             "turn — the pending plan is still the OLD one "
                             f"('{pend['instruction']}'). If the user asked "
                             "for something different, call propose_edit NOW "
                             "with the NEW request (it replaces the old "
                             "plan). If the old plan already matches, present "
                             "THAT plan without claiming a new one.")
                else:
                    nudge = ("You narrated steps but called NO tool — nothing "
                             "exists yet, so the user would approve into a "
                             "void. If the request was AMBIGUOUS (the kind/"
                             "mood/color is unspecified), call ask_user NOW. "
                             "Otherwise call propose_edit NOW with the user's "
                             "request as the instruction. Do not describe — act.")
                messages.append({"role": "system", "content": nudge})
                continue
            history.append({"role": "assistant", "content": text})
            return text or "(no reply)"

        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            any_tool = True
            if on_tool:
                on_tool(name, args)
            # ask_user is TERMINAL: surface the question (+ option chips) to the
            # user and end the turn right here — don't let the model keep going
            # and guess. The user's next message answers it.
            if name == "ask_user":
                q = (args.get("question") or "").strip() or "Bunu biraz açar mısın?"
                opts = args.get("options") or []
                if not isinstance(opts, list):
                    opts = []
                session.last_clarify = {"question": q,
                                        "options": [str(o) for o in opts][:5]}
                history.append({"role": "assistant", "content": q})
                return q
            fn = REGISTRY.get(name)
            if fn is None:
                result = {"ok": False, "error": f"unknown tool {name}"}
            elif (name in ("propose_edit", "propose_assets", "propose_project")
                    and planned_this_turn):
                # Only ONE pending plan can exist; a second proposal would
                # silently overwrite the first. multiclip_plans: a MULTI-clip
                # request ('tighten every clip') is ONE propose_project call —
                # so the very FIRST proposal should be project-scope. Don't
                # loop propose_edit per clip; propose_project already spans them
                # all under one approval/undo.
                result = {"ok": False, "error":
                          "A plan is already pending from THIS turn — there can "
                          "only be one at a time. For a MULTI-clip request, use "
                          "a SINGLE propose_project(instruction=...) call "
                          "(spans every clip under one approval) instead of "
                          "proposing clips one by one. Present the plan you just "
                          "made and WAIT for approval before proposing more."}
            elif session.data.get("pending_plan") and name in MUTATING_TOOLS:
                result = {"ok": False, "error":
                          "A plan is awaiting approval — individual edits are "
                          "blocked. If the user approved, call apply_plan. If "
                          "they want something else, call discard_plan first."}
            elif name in gated and args.get("clip_id") not in new_clips:
                result = {"ok": False, "error":
                          "Blocked: every clip change needs the user's A/B "
                          "approval. Call propose_edit(clip_id=..., "
                          "instruction=<the user's request, verbatim>) — it "
                          "renders the preview automatically; then present "
                          "the plan and WAIT for approval."}
            else:
                try:
                    result = fn(session, **args)
                except Exception as e:
                    result = {"ok": False,
                              "error": f"{type(e).__name__}: {e}"}
                if (name == "duplicate_clip" and result.get("ok")
                        and result.get("new_id")):
                    new_clips.add(result["new_id"])
                if (name in ("propose_edit", "propose_assets",
                             "propose_project")
                        and result.get("ok")):
                    planned_this_turn = True
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})

    history.append({"role": "assistant",
                    "content": "(reached the operation-round limit)"})
    return "Reached the operation-round limit — check the latest state with list_clips."
