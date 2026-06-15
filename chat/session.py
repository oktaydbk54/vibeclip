"""Chat-editor session state + declarative stage replay.

A session is a JSON project file holding the source video, its clips, and each
clip's ordered edit stack. Every stage records its params and the artifact it
produced, so changing ONE stage replays only that stage and the ones after it
(from the cached upstream artifact) instead of re-rendering everything.

Timing rule: clip timing changes only at TIMING_STAGES ('cut', 'jumpcut',
'trim'). Stages that need word timings (subtitles, zoom planning) transcribe
the LAST timing-changing artifact — transcribe() is content-cached, so this is
a cache hit after the first call. When a timing stage changes, downstream
time-anchored params (zoom windows, sfx events) are re-planned if they were
auto-generated, or cleared (with a note) if hand-placed — their old times
would land on the wrong content.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info

# Canonical stage order — mirrors orchestrate._auto_edit_clip. broll sits on
# the 9:16 frame under captions; lut grades footage BEFORE captions so text
# stays clean; splitscreen squishes the graded content into the top region
# (after zoom punches in, before captions) so the gameplay background sits
# UNDER the captions and the captions land on the final composited frame;
# overlay (textures/memes/stickers) and brand go over captions; fx
# (flash/shake) hits last so it reads on everything.
# "speed" sits AFTER reframe/denoise (which carry no time-anchored events and
# commute with a uniform retime) so a speed change never re-runs the expensive
# tracked reframe — and tracking runs on normal-speed footage for cleaner
# motion. Every stage from broll onward DOES carry time events, so they run in
# the already-sped timeline (only caption word-timing, derived from pre-speed
# words, is divided by the factor — see Session.speed_factor / SPED_EVENT_STAGES).
CANONICAL = ["cut", "jumpcut", "trim", "reframe", "denoise", "speed", "broll",
             "lut", "zoom", "splitscreen", "subtitles", "overlay", "brand",
             "fx", "dub", "music", "ambience", "sfx", "fade"]

# Stages that change the clip's timeline by REMOVING content (drive the
# kept/removed TimeMap). "speed" is deliberately NOT here: it rescales time
# uniformly rather than cutting spans, so it stays outside the TimeMap and the
# word-timing reconstruction (words_for transcribes the last TIMING_STAGE
# output, i.e. PRE-speed; the speed factor is applied on top where needed).
TIMING_STAGES = ("cut", "jumpcut", "trim")

# Post-speed stages whose event times live in the SPED (player) timeline.
SPED_EVENT_STAGES = ("zoom", "broll", "overlay", "fx", "sfx")

SESSIONS_DIR = config.OUTPUTS_DIR / "sessions"


def _token_parts(tok: str) -> tuple[str, str, str]:
    """Split a transcript token into (leading punct, alnum core, trailing)."""
    i, j = 0, len(tok)
    while i < j and not tok[i].isalnum():
        i += 1
    while j > i and not tok[j - 1].isalnum():
        j -= 1
    return tok[:i], tok[i:j], tok[j:]


def _apply_word_fixes(words: list[dict], fixes) -> list[dict]:
    """Overlay Intent-Fix corrections onto a word list (text only, timing kept).

    `fixes` is [{"from","to"}]; every token whose alnum core case-insensitively
    equals a `from` is rewritten to `to`, preserving surrounding punctuation.
    Returns the same list object when there is nothing to change (cheap path).
    """
    if not fixes:
        return words
    fix_map = {}
    for f in fixes:
        frm = (f.get("from") or "").strip()
        to = (f.get("to") or "").strip()
        if frm and to:
            fix_map[frm.casefold()] = to
    if not fix_map:
        return words
    out, changed = [], False
    for w in words:
        pre, core, post = _token_parts(w.get("word", ""))
        repl = fix_map.get(core.casefold()) if core else None
        if repl is not None:
            nw = dict(w)
            nw["word"] = pre + repl + post
            out.append(nw)
            changed = True
        else:
            out.append(w)
    return out if changed else words


class Session:
    def __init__(self, data: dict, path: Path):
        self.data = data
        self.path = path
        self.last_notes: list[str] = []  # transient replay notes, not persisted
        # Phase 5 — proxy-edit / full-res export split. The cut stage reads its
        # footage from here. None => INTERACTIVE mode: cut from proxy_or_source()
        # (the cheap 540p proxy when built, else the full source — legacy
        # behavior). export_clip() temporarily pins this to the full-res
        # source['path'] so the SAME stage params replay against full footage.
        # Never persisted; it's a per-render switch, not session state.
        self._render_source: str | None = None

    # ------------------------------------------------------------- lifecycle
    @classmethod
    def load_or_create(cls, video_path: str,
                       build_proxy: bool = True) -> "Session":
        src = Path(video_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Video not found: {src}")
        name = src.stem
        sdir = SESSIONS_DIR / name
        sdir.mkdir(parents=True, exist_ok=True)
        pfile = sdir / "project.json"
        if pfile.exists():
            return cls(json.loads(pfile.read_text()), pfile)

        info = ffprobe_info(str(src))
        data = {
            "version": 1,
            "name": name,
            "source": {"path": str(src), **info},
            "platform": "youtube_shorts",
            "clips": [],
            "history": [],
        }
        sess = cls(data, pfile)
        sess.save()
        # Phase 0: on FIRST create, build the 540p proxy + keyframe index as a
        # background job and record them onto source.* (additive). Older
        # sessions without these fields keep working via proxy_or_source().
        if build_proxy:
            sess._submit_proxy_job()
        return sess

    @classmethod
    def open_existing(cls, name: str) -> "Session":
        """Open an existing project by its session-dir name WITHOUT going
        through load_or_create. load_or_create keys on the video stem and would
        re-create the session (with a fresh proxy job) if the source file has
        since moved — we just want to adopt the saved project.json verbatim.
        Never renames the dir; artifact paths inside are absolute."""
        pfile = SESSIONS_DIR / name / "project.json"
        if not pfile.exists():
            raise FileNotFoundError(f"No project '{name}'.")
        return cls(json.loads(pfile.read_text()), pfile)

    @classmethod
    def create_from_clips(cls, name: str, clip_paths: list[str]) -> "Session":
        """Own-clips project: the user uploads already-finished clips and skips
        auto-clipping. The first clip doubles as the nominal data['source'] so
        every data['source'] consumer (summary, export, qc) stays valid; there
        is NO proxy job (these are short finished clips, not a long source).
        Each clip carries an additive 'source_path' (its own footage) — the
        engine's cut/_out/_cut_spans/export branches read that when present.
        Constructed and saved directly (bypasses load_or_create)."""
        if not clip_paths:
            raise ValueError("No clip files given.")
        sdir = SESSIONS_DIR / name
        sdir.mkdir(parents=True, exist_ok=True)
        pfile = sdir / "project.json"
        info0 = ffprobe_info(clip_paths[0])
        data = {
            "version": 1,
            "name": name,
            "display_name": name,
            "source": {"path": str(clip_paths[0]), **info0},
            "platform": "youtube_shorts",
            "clips": [],
            "history": [],
            "intake": {"mode": "own_clips", "processing_job": None,
                       "error": None, "processed_at": None},
        }
        clips = []
        for i, src in enumerate(clip_paths, start=1):
            sp = Path(src)
            dur = float(ffprobe_info(str(sp)).get("duration", 0.0))
            clips.append({
                "id": i, "title": sp.stem, "start": 0.0, "end": dur,
                "score": 0, "status": "pending", "stages": [], "current": None,
                "source_path": str(sp.resolve()),
                "hook": "", "reason": "",
            })
        data["clips"] = clips
        sess = cls(data, pfile)
        sess.save()
        return sess

    # ------------------------------------------------------------- status
    @staticmethod
    def derive_status(data: dict, live_job: dict | None) -> str:
        """Pipeline status DERIVED from clips + the optional live job. Sessions
        with no 'intake' read as a long_video project (old sessions untouched).

        live_job (when not None) is the ACTIVE project's current/queued job as a
        public() dict {id,status,...}; only the active project can be 'processing'.
        """
        clips = data.get("clips") or []
        intake = data.get("intake") or {}
        proc_job = intake.get("processing_job")
        if not clips:
            if (live_job is not None
                    and live_job.get("status") in ("queued", "running")
                    and proc_job is not None
                    and live_job.get("id") == proc_job):
                return "processing"
            if intake.get("error"):
                return "error"
            # No live job (covers a server restart mid-processing: job ids don't
            # survive a restart) and no recorded error -> ready to (re)process.
            return "needs_processing"
        statuses = [Session.clip_status(c) for c in clips]
        non_skipped = [s for s in statuses if s != "skipped"]
        if non_skipped and all(s == "exported" for s in non_skipped):
            return "done"
        if all(s == "pending" for s in statuses):
            return "clips_ready"
        return "editing"

    def _submit_proxy_job(self) -> None:
        """Submit the background proxy + keyframe-index build (best-effort).

        Kept import-light and failure-tolerant: a missing job manager or a proxy
        build error must never block session creation — analysis simply falls
        back to the full-res source until/unless the proxy lands."""
        sdir = self.path.parent
        src_path = self.data["source"]["path"]

        def _build(job=None) -> dict:
            from pipeline.proxy import build_proxy, keyframe_index
            proxy = build_proxy(src_path, sdir)
            keys = keyframe_index(src_path)
            # Re-read from disk before mutating: the job runs on the worker
            # thread and the in-memory copy here may be a different instance.
            data = json.loads(self.path.read_text())
            data.setdefault("source", {})["proxy_path"] = proxy
            data["source"]["keyframes"] = keys
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=1))
            self.data = data
            return {"proxy_path": proxy, "keyframes": len(keys)}

        try:
            from chat.jobs import MANAGER
            MANAGER.submit("tool", "build proxy", _build)
        except Exception:  # noqa: BLE001 — proxy is an optimization, not a gate
            pass

    def proxy_or_source(self) -> str:
        """Path to use for ANALYSIS/PREVIEW: the proxy if it's been built and
        still exists on disk, else the original full-res source. Final EXPORT
        must keep using source['path'] directly, not this.

        Phase 0 gap fix (option b, lowest-risk): the background proxy job writes
        source.proxy_path to project.json on its OWN worker thread, so the live
        global SESSION instance in app.py may still have no proxy_path in memory
        even after the proxy finished building. So if it's not in memory, we
        re-read project.json from disk once and adopt proxy_path/keyframes
        (additive — no other fields touched). If the proxy still isn't ready
        (job not finished yet), we gracefully return the source and never block;
        the next analysis call will pick it up once it lands."""
        proxy = self.data.get("source", {}).get("proxy_path")
        if not proxy and self.path.exists():
            try:
                disk = json.loads(self.path.read_text())
                dsrc = disk.get("source", {})
                if dsrc.get("proxy_path"):
                    self.data.setdefault("source", {})["proxy_path"] = \
                        dsrc["proxy_path"]
                    if "keyframes" in dsrc:
                        self.data["source"]["keyframes"] = dsrc["keyframes"]
                    proxy = dsrc["proxy_path"]
            except Exception:  # noqa: BLE001 — disk read is best-effort
                pass
        if proxy and Path(proxy).exists():
            return proxy
        return self.data["source"]["path"]

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))

    def snapshot(self, label: str = "", source: str = "chat",
                 tag: str | None = None) -> None:
        """Push an undo point (deep copy of clips). Artifacts stay on disk.

        source tags who triggered the edit ('chat' | 'ui' | 'plan') — the
        edit-log surfaces this later; older entries simply read as 'chat'.
        tag is an optional named-checkpoint id (set by apply_plan) so a single
        applied plan can be reverted later regardless of LIFO position; older
        entries simply lack the key.
        """
        if getattr(self, "suppress_snapshots", False):
            return  # apply_plan takes ONE snapshot for the whole plan
        entry = {"label": label, "source": source,
                 "clips": copy.deepcopy(self.data["clips"])}
        if tag:
            entry["tag"] = tag
        self.data["history"].append(entry)
        self.data["history"] = self.data["history"][-20:]
        # A fresh edit forks history — the redo branch is no longer reachable.
        self.data.pop("redo", None)

    def undo(self) -> str:
        if not self.data["history"]:
            return "Nothing to undo."
        entry = self.data["history"].pop()
        label = entry.get("label", "") if isinstance(entry, dict) else ""
        # Stash the CURRENT state on the redo stack before reverting, so the
        # undo can be re-applied. (Bare-list legacy entries have no label.)
        redo = self.data.setdefault("redo", [])
        redo.append({"label": label,
                     "clips": copy.deepcopy(self.data["clips"])})
        self.data["redo"] = redo[-20:]
        # Older project files stored a bare clips list.
        self.data["clips"] = entry["clips"] if isinstance(entry, dict) else entry
        self.save()
        return "Reverted to the previous state."

    def redo(self) -> str:
        """Re-apply the most recently undone edit (cleared by any new edit)."""
        redo = self.data.get("redo") or []
        if not redo:
            return "Nothing to redo."
        entry = redo.pop()
        # Record the pre-redo state on history so it stays undoable. Use the
        # history list directly (not snapshot(), which would clear redo).
        self.data["history"].append(
            {"label": entry.get("label") or "redo", "source": "ui",
             "clips": copy.deepcopy(self.data["clips"])})
        self.data["history"] = self.data["history"][-20:]
        self.data["clips"] = entry["clips"]
        self.save()
        return "Re-applied the change."

    def revert_to_tag(self, tag: str) -> str:
        """Pop to a named checkpoint (e.g. an applied plan), restoring the
        PRE-checkpoint clip state. Non-destructive: the revert itself is taken
        as a fresh snapshot first, so it stays undoable and later history is
        not rewritten. Returns a 'not found' message if the checkpoint has
        aged out of the 20-step history (or never existed)."""
        entry = next(
            (e for e in self.data.get("history", [])
             if isinstance(e, dict) and e.get("tag") == tag), None)
        if entry is None:
            return ("Checkpoint not found — it may have aged out of the "
                    "20-step history.")
        # Mirror /api/restore semantics: snapshot the current state, then swap
        # in the checkpoint's clips. This restores the state BEFORE that plan,
        # including any edits applied after it.
        self.snapshot("before revert", source="ui")
        self.data["clips"] = copy.deepcopy(entry["clips"])
        self.save()
        return "Reverted to the state before that edit."

    # ------------------------------------------------------------- accessors
    @property
    def workdir(self) -> Path:
        return self.path.parent

    def clip(self, clip_id: int) -> dict:
        for c in self.data["clips"]:
            if c["id"] == clip_id:
                return c
        raise ValueError(
            f"No clip #{clip_id}. Existing: {[c['id'] for c in self.data['clips']]}"
        )

    # Phase 3 — per-clip review status (additive). Old sessions whose clips
    # predate this field read as "pending", so the queue/UI keep working.
    CLIP_STATUSES = ("pending", "approved", "skipped", "exported")

    @staticmethod
    def clip_status(clip: dict) -> str:
        """Status of a clip, defaulting missing/unknown values to 'pending'."""
        s = clip.get("status")
        return s if s in Session.CLIP_STATUSES else "pending"

    # Phase 4 — sequential editing queue cursor (additive). active_clip_id is
    # the clip currently IN FOCUS in the center editor; the user walks the
    # ranked candidates one at a time. It's stored in session.data and persisted
    # via the normal save(); old project files with no cursor default to the
    # top-ranked pending clip (else the first clip). This is purely a "which
    # clip is in focus" pointer — it does NOT change timing/edits and never goes
    # through the A/B gate. The agent already resolves clip references from
    # summary() ("ikinci klip"/"this clip"); we surface the focused clip there
    # so "this clip" / "bu klip" resolves to the cursor rather than a competing
    # concept.

    def active_clip_id(self) -> int | None:
        """Id of the focused clip. Defaults (when unset/stale) to the top-ranked
        PENDING clip, else the first clip; None only when there are no clips."""
        clips = self.data.get("clips") or []
        if not clips:
            return None
        cur = self.data.get("active_clip_id")
        if cur is not None and any(c["id"] == cur for c in clips):
            return cur
        for c in clips:                       # ranked order = id asc = score desc
            if self.clip_status(c) == "pending":
                return c["id"]
        return clips[0]["id"]

    def active_clip(self) -> dict | None:
        cid = self.active_clip_id()
        return None if cid is None else self.clip(cid)

    def set_active_clip(self, clip_id: int) -> int:
        """Move the focus cursor. Validates the id exists. Returns it."""
        self.clip(clip_id)                    # raises ValueError on unknown id
        self.data["active_clip_id"] = clip_id
        self.save()
        return clip_id

    def advance_clip(self, direction: int = 1,
                     pending_only: bool = False) -> int | None:
        """Move the cursor to the next/prev NON-skipped clip in ranked order and
        return its id (None if there's no eligible target). direction: +1 next,
        -1 prev. pending_only=True (used by 'approve & next') lands on the next
        clip whose status is still 'pending', skipping approved/exported too.
        Tolerant of an empty clip list and of the cursor sitting on a now-skipped
        clip. Does NOT wrap around — at an edge it stays put."""
        clips = self.data.get("clips") or []
        if not clips:
            return None
        ids = [c["id"] for c in clips]
        cur = self.active_clip_id()
        try:
            idx = ids.index(cur)
        except ValueError:
            idx = 0
        step = 1 if direction >= 0 else -1
        i = idx + step
        while 0 <= i < len(clips):
            st = self.clip_status(clips[i])
            if st != "skipped" and (not pending_only or st == "pending"):
                self.data["active_clip_id"] = clips[i]["id"]
                self.save()
                return clips[i]["id"]
            i += step
        return None  # no eligible clip in that direction — cursor unchanged

    def queue_summary(self) -> dict:
        """Batch progress for the appbar: counts per status + cursor position.
        position = 1-based rank of the focused clip among ALL clips (ranked
        order), so the UI can show 'clip N / M'. All fields degrade gracefully
        on an empty list."""
        clips = self.data.get("clips") or []
        counts = {"approved": 0, "skipped": 0, "pending": 0, "exported": 0}
        for c in clips:
            counts[self.clip_status(c)] = counts.get(self.clip_status(c), 0) + 1
        cid = self.active_clip_id()
        position = 0
        if cid is not None:
            for i, c in enumerate(clips, start=1):
                if c["id"] == cid:
                    position = i
                    break
        return {"total": len(clips), "active_clip_id": cid,
                "position": position, **counts}

    # ----------------------------------------------------- pending plan(s)
    # multiclip_plans — pending_plan stores EITHER today's single plan dict
    # ({clip_id, instruction, steps, ...}) OR a project-scope composite
    # ({'scope':'project', 'instruction', 'plans':[<plan dict>, ...]}) so one
    # approval can span N clips under a SINGLE undo. Every reader (summary,
    # apply_plan, the preview, the agent guard) goes through pending_plans()
    # so single-clip flows stay byte-identical (a 1-element list).
    def pending_plans(self) -> list[dict]:
        """Normalize the pending plan to a flat list of per-clip plan dicts.
        Empty when nothing is pending; a single-clip plan yields [plan]."""
        pp = self.data.get("pending_plan")
        if not pp:
            return []
        if pp.get("scope") == "project":
            return [p for p in (pp.get("plans") or []) if p]
        return [pp]

    def pending_plan_is_project(self) -> bool:
        """True when the pending plan is a multi-clip composite."""
        pp = self.data.get("pending_plan")
        return bool(pp and pp.get("scope") == "project")

    def summary(self) -> str:
        """Compact state block for the agent's system prompt."""
        s = self.data["source"]
        active = self.active_clip_id()
        lines = [
            f"SOURCE: {s['path']} ({s['duration']:.0f}s, {s['width']}x{s['height']})",
            f"PLATFORM: {self.data['platform']}",
            f"CLIPS ({len(self.data['clips'])}):",
        ]
        for c in self.data["clips"]:
            parts = []
            for st in c["stages"]:
                p = {k: v for k, v in st["params"].items()
                     if k not in ("events", "windows", "path", "ranges")}
                if st["name"] == "music" and st["params"].get("path"):
                    p["track"] = Path(st["params"]["path"]).name
                # Indexed event listings so the model can address "the second
                # zoom" by its 0-based index (cf. edit_event/delete_event).
                if st["name"] == "zoom":
                    p["windows"] = [
                        f"[{i}] {w[0]:.1f}-{w[1]:.1f}s x{w[2]:g}"
                        + (f" {w[3]}" if len(w) > 3 else "")
                        for i, w in enumerate(
                            st["params"].get("windows", []))][:8]
                if st["name"] in ("sfx", "fx"):
                    p["events"] = [
                        f"[{i}] {e['time']:.1f}s"
                        + (f" {e.get('kind', '')}" if e.get("kind") else "")
                        for i, e in enumerate(
                            st["params"].get("events", []))][:8]
                if st["name"] in ("broll", "overlay"):
                    p["events"] = [
                        f"[{i}] {e.get('start', 0):.1f}-{e.get('end', 0):.1f}s"
                        for i, e in enumerate(
                            st["params"].get("events", []))][:8]
                if st["name"] == "trim":
                    p["removed"] = [
                        f"{r['start']:.1f}-{r['end']:.1f}s"
                        for r in st["params"].get("ranges", [])]
                parts.append(f"{st['name']}{p if p else ''}")
            style = f" style={c['style']}" if c.get("style") else ""
            variant = f" (variant of #{c['variant_of']})" \
                if c.get("variant_of") else ""
            # Phase 4 — mark the focused clip so the agent resolves bare
            # references ("this clip"/"bu klip", an edit with no clip named) to
            # the queue cursor rather than guessing.
            focus = " <-- ACTIVE (in focus now)" if c["id"] == active else ""
            lines.append(
                f"  #{c['id']} '{c['title']}' [{c['start']:.1f}-{c['end']:.1f}s]"
                f"{style}{variant} stages: {' → '.join(parts) or '(none)'}{focus}"
            )
        if active is not None:
            lines.append(
                f"ACTIVE CLIP: #{active} is in focus in the editor. When the "
                "user edits without naming a clip ('make this punchier', "
                "'altyazıyı büyüt'), they mean clip "
                f"#{active}. The A/B approval gate still applies as always.")
        if not self.data["clips"]:
            lines.append("  (none yet — use generate_clips)")
        comps = self.data.get("compilations", [])
        if comps:
            lines.append(f"COMPILATIONS ({len(comps)}):")
            for cp in comps:
                lines.append(f"  C{cp['id']} '{cp['title']}' "
                             f"({cp['duration']}s, clips {cp['clips']})")
        if self.data.get("preferences"):
            lines.append("USER PREFERENCES (always respect): "
                         + "; ".join(self.data["preferences"]))
        if self.data.get("pending_plan"):
            pp = self.data["pending_plan"]
            if self.pending_plan_is_project():
                ids = [str(p.get("clip_id")) for p in self.pending_plans()]
                lines.append(
                    f"PENDING PROJECT PLAN on clips #{', #'.join(ids)}:"
                    f" '{pp.get('instruction', '')}' — one composite awaiting"
                    " user approval. On approval call apply_plan (it applies"
                    " EVERY clip's steps under a SINGLE undo); on rejection"
                    " call discard_plan. NEVER run its steps yourself. The"
                    " preview is a per-clip A/B carousel. If the user MODIFIES"
                    " the request, call propose_edit (project scope) again —"
                    " it replaces this plan.")
            else:
                lines.append(
                    f"PENDING PLAN on clip #{pp['clip_id']}: '{pp['instruction']}'"
                    " — awaiting user approval. On approval call apply_plan; on"
                    " rejection call discard_plan. NEVER run its steps yourself."
                    " If the user MODIFIES the request instead ('aslında 3x"
                    f" olsun'), call propose_edit on clip #{pp['clip_id']} with"
                    " the new instruction — it replaces this plan.")
        return "\n".join(lines)

    # ------------------------------------------------------------- words
    # Phase 5 — clip-local transcript cache. words_for is called repeatedly per
    # replay (subtitles, zoom retiming) and across edits; transcribe() is
    # content-cached on disk, but each call still re-reads+parses that JSON. The
    # clip's words only change when its TIMING artifact changes, and that
    # artifact's filename embeds the timing params' hash (via _out). So we memo
    # words by that artifact path: a param tweak that doesn't change the clip's
    # timeline reuses the in-memory words and Whisper never re-runs. Keyed by the
    # ref path, so a genuine timing change (new artifact name) misses and
    # re-derives. Word timing stays exact — same source, same key, same words.
    _WORDS_CACHE: dict[str, list[dict]] = {}

    def words_for(self, clip: dict) -> list[dict]:
        """Clip-local word timings valid for the clip's current timing."""
        from pipeline.transcribe import transcribe

        ref = None
        for st in clip["stages"]:
            if st["name"] in TIMING_STAGES:
                ref = st["output"]
        if not ref:
            raise ValueError("Clip has no cut artifact yet.")
        cached = Session._WORDS_CACHE.get(ref)
        if cached is None:
            cached = transcribe(ref)["words"]
            Session._WORDS_CACHE[ref] = cached
        # Intent Fix: apply the clip's stored ASR corrections on top of the raw
        # (cached) transcription — text only, timings untouched — so captions and
        # everything else read the corrected words. Kept out of the cache so
        # changing the fixes takes effect immediately without re-transcribing.
        return _apply_word_fixes(cached, clip.get("word_fixes"))

    def segments_for(self, clip: dict) -> list[dict]:
        """Clip-local transcript SEGMENTS (sentence-level) for the clip's current
        timing — the dub stage's natural unit. Reads the same TIMING-stage
        artifact words_for uses; transcribe() is disk-cached so this is cheap."""
        from pipeline.transcribe import transcribe
        ref = None
        for st in clip["stages"]:
            if st["name"] in TIMING_STAGES:
                ref = st["output"]
        if not ref:
            raise ValueError("Clip has no cut artifact yet.")
        return transcribe(ref)["segments"]

    def speed_factor(self, clip: dict) -> float:
        """The clip's constant-speed factor (1.0 = untouched). Clamped 0.25–4×.

        Captions/auto-planned events derive their timing from the PRE-speed
        words (words_for); dividing those times by this factor maps them into
        the sped timeline every later stage runs in. See [[pipeline.speed]].
        """
        st = next((s for s in clip.get("stages", [])
                   if s["name"] == "speed"), None)
        if not st:
            return 1.0
        try:
            return max(0.25, min(4.0, float(st["params"].get("factor", 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def timing_chain_for(self, clip: dict) -> list[dict]:
        """Per-timing-stage maps. Each entry:
        {name, stage, pre (TimeMap source→stage-input), own (input→output)}.

        Sidecars (`.map.json`, written at render time) hold each stage's
        resolved keep-intervals. Legacy artifacts get a deterministic
        recompute — minus LLM aggressive-filler drops, which can't be
        re-derived (those rare spans then show as kept).
        """
        from pipeline.timemap import TimeMap, read_sidecar, write_sidecar
        chain: list[dict] = []
        m: TimeMap | None = None
        prev_out: str | None = None
        for st in clip["stages"]:
            if st["name"] not in TIMING_STAGES:
                continue
            out = st.get("output")
            if st["name"] == "cut":
                p = st["params"]
                own = TimeMap.from_kept([(float(p["start"]),
                                          float(p["end"]))])
                chain.append({"name": "cut", "stage": st, "pre": None,
                              "own": own})
                m, prev_out = own, out
                continue
            if m is None:
                raise ValueError("Clip has no cut stage.")
            if not out or out == prev_out:
                continue  # stage no-op'd: nothing was trimmed
            kept = read_sidecar(out)
            if kept is None:
                kept = self._recompute_kept(st, prev_out)
                write_sidecar(out, kept)
            own = TimeMap.from_kept(kept)
            chain.append({"name": st["name"], "stage": st, "pre": m,
                          "own": own, "input": prev_out})
            m = m.compose(own)
            prev_out = out
        if m is None:
            raise ValueError("Clip has no cut artifact yet.")
        return chain

    def timemap_for(self, clip: dict):
        """Composed SOURCE→current-output TimeMap from the timing stages."""
        chain = self.timing_chain_for(clip)
        m = chain[0]["own"]
        for link in chain[1:]:
            m = m.compose(link["own"])
        return m

    def _recompute_kept(self, st: dict, inp: str) -> list[tuple[float, float]]:
        """Re-derive a legacy timing stage's keep-intervals from its input."""
        from pipeline.jumpcut import (FILLER_WORDS, _norm_word,
                                      _subtract_ranges,
                                      keep_intervals_from_words)
        from pipeline.media import ffprobe_info
        from pipeline.transcribe import transcribe
        words = transcribe(inp)["words"]
        dur = ffprobe_info(inp)["duration"]
        p = st["params"]
        if st["name"] == "jumpcut":
            drop_words = frozenset()
            if p.get("remove_fillers"):
                drop_words = (frozenset(_norm_word(w)
                                        for w in p["filler_words"])
                              if p.get("filler_words") else FILLER_WORDS)
            return keep_intervals_from_words(
                words, 0.0, dur, p.get("max_pause", 0.5),
                drop_words=drop_words,
                protect_ranges=[tuple(r) for r in
                                p.get("protect_ranges", [])])
        resolved = _resolve_trim_ranges(words, p.get("ranges", []))
        return _subtract_ranges([(0.0, dur)], resolved)

    # ------------------------------------------------------------- replay
    def set_stage(self, clip_id: int, name: str, params: dict) -> str:
        """Set/replace one stage and replay everything after it. Returns path."""
        return self.set_stages(clip_id, [(name, params)])

    def set_stages(self, clip_id: int, updates: list[tuple[str, dict]]) -> str:
        """Set/replace several stages, replaying ONCE from the earliest change.

        This is what apply_style uses: 6 stage updates = one replay pass, not
        six cascading tail re-encodes. Notes about invalidated time-anchored
        params land in self.last_notes (transient, for the calling tool).
        """
        order = {n: i for i, n in enumerate(CANONICAL)}
        for name, _ in updates:
            if name not in order:
                raise ValueError(f"Unknown stage '{name}'. Stages: {CANONICAL}")
        upd = {name: params for name, params in updates}
        earliest = min(order[n] for n in upd)
        timing_changed = any(n in TIMING_STAGES for n in upd)
        self.last_notes = []

        clip = self.clip(clip_id)
        if timing_changed and clip.get("locked"):
            raise ValueError(
                f"Clip {clip_id} is picture-locked — its timing "
                f"({', '.join(TIMING_STAGES)}) is frozen. Unlock it first.")
        stack = clip["stages"]
        # Lazy clips (generate_clips records the stage recipe but renders
        # nothing) carry params with no 'output'. An edit to any later stage
        # would make 'keep' reference an unrendered stage's missing output, so
        # force a full replay from cut to materialize the whole recipe.
        if any("output" not in st for st in stack):
            earliest = 0
        keep = [st for st in stack if order[st["name"]] < earliest]
        merged = sorted(
            [{"name": n, "params": p} for n, p in upd.items()]
            + [{"name": st["name"], "params": st["params"]} for st in stack
               if order[st["name"]] >= earliest and st["name"] not in upd],
            key=lambda st: order[st["name"]],
        )

        out = keep[-1]["output"] if keep else None
        new_stack = list(keep)
        clip["stages"] = new_stack  # words_for must see the in-progress stack

        from pipeline import progress as _pg
        nominal_dur = max(0.0, clip.get("end", 0.0) - clip.get("start", 0.0))
        _pg.begin_stages(len(merged))
        for i, st in enumerate(merged):
            _pg.report_stage(i, st["name"], nominal_dur)
            params = st["params"]
            if (timing_changed
                    and st["name"] in ("zoom", "sfx", "broll", "overlay", "fx")
                    and (st["name"] not in upd or params.get("auto"))):
                params = self._retime_params(clip, st["name"], params)
            out = self._run_stage(clip, st["name"], params, out)
            new_stack.append({"name": st["name"], "params": params,
                              "output": out})

        clip["stages"] = new_stack
        clip["current"] = out
        self.save()
        return out

    def render_clip(self, clip_id: int, upto: str | None = None) -> str | None:
        """Materialize a lazily-created clip's recorded stage recipe.

        generate_clips records each clip's stage PARAMS (cut + default stages)
        without rendering, so a 20-min source yields its candidate list in
        seconds instead of eagerly encoding every clip. This replays that recipe
        once — against the proxy (interactive footage) — to produce clip
        ['current']. Idempotent: a clip whose stages all already have an 'output'
        is returned as-is (pure cache, no re-encode).

        `upto` enables a PROGRESSIVE render: pass a stage name (e.g. "reframe")
        to render only up to AND INCLUDING that stage, deferring the tail. This
        gives a fast captionless 9:16 preview; a follow-up render_clip(upto=None)
        then only re-encodes the deferred tail (the head stages are on-disk cache
        hits via _out), so no work is wasted."""
        clip = self.clip(clip_id)
        stages = clip.get("stages") or []
        if not stages:
            return clip.get("current")
        if upto is None and clip.get("current") \
                and all("output" in st for st in stages):
            return clip["current"]
        if upto is not None:
            return self._render_upto(clip, stages, upto)
        # Replay the whole recorded recipe. set_stages' lazy guard sees the
        # output-less stack and forces earliest=0, so this renders cut→…→current.
        return self.set_stages(
            clip_id, [(st["name"], st["params"]) for st in stages])

    def _render_upto(self, clip: dict, stages: list, upto: str) -> str | None:
        """Cache-aware PARTIAL render: replay the recipe up to & including
        `upto`, keeping the remaining stages' params (no 'output') so a later
        full render() only re-encodes the deferred tail — the head stages hash
        to existing on-disk artifacts (_out cache hits). Mirrors set_stages'
        in-progress clip['stages'] growth so words_for sees the rendered cut."""
        order = {n: i for i, n in enumerate(CANONICAL)}
        if upto not in order:
            raise ValueError(f"Unknown stage '{upto}'. Stages: {CANONICAL}")
        cutoff = order[upto]
        head = [st for st in stages if order[st["name"]] <= cutoff]
        tail = [st for st in stages if order[st["name"]] > cutoff]
        from pipeline import progress as _pg
        nominal = max(0.0, clip.get("end", 0.0) - clip.get("start", 0.0))
        _pg.begin_stages(len(head))
        out: str | None = None
        new_head: list[dict] = []
        clip["stages"] = new_head  # words_for must see the in-progress stack
        for i, st in enumerate(head):
            _pg.report_stage(i, st["name"], nominal)
            out = self._run_stage(clip, st["name"], st["params"], out)
            new_head.append({"name": st["name"], "params": st["params"],
                             "output": out})
        clip["stages"] = new_head + [{"name": st["name"], "params": st["params"]}
                                     for st in tail]
        clip["current"] = out
        self.save()
        return out

    def export_clip(self, clip_id: int) -> str:
        """Phase 5 — render the FINAL full-resolution output for a clip.

        Replays the clip's EXISTING stage-param chain (the exact stages the user
        approved during interactive editing) against the full-res source['path']
        instead of the proxy. Because every stage is param-keyed, this is a clean
        re-derivation: identical params on full-res footage produce the same
        edit at native resolution. The 1:1 proxy/source timing from Phase 0 makes
        every timestamp (cuts, zoom windows, caption word times) land on the same
        content on full-res as it did on the proxy.

        Returns the full-res output path and records it on clip['export'] (a new
        additive key) without disturbing clip['current'] (the proxy preview the
        editor keeps showing) or any stage's recorded 'output'. Does NOT take a
        snapshot, does NOT go through the A/B gate, and does NOT mutate the
        editable stage stack — export is a read-only re-render of the approved
        recipe. Cache-keyed by the same params, so a second export with no edits
        in between is a pure cache hit.
        """
        clip = self.clip(clip_id)
        stages = clip.get("stages") or []
        if not stages or stages[0]["name"] != "cut":
            raise ValueError(f"Clip #{clip_id} has no cut stage to export.")

        from pipeline import progress as _pg
        prev_render_source = self._render_source
        prev_notes = self.last_notes
        self._render_source = self.data["source"]["path"]
        outputs: list[str] = []
        try:
            out: str | None = None
            _pg.begin_stages(len(stages))
            nominal = max(0.0, clip.get("end", 0.0) - clip.get("start", 0.0))
            for i, st in enumerate(stages):
                _pg.report_stage(i, st["name"], nominal)
                # Replay with the stored params verbatim — no retiming, the
                # approved recipe is reproduced as-is at full resolution.
                out = self._run_stage(clip, st["name"], st["params"], out)
                outputs.append(out)
        finally:
            self._render_source = prev_render_source
            self.last_notes = prev_notes

        clip["export"] = out
        self.save()
        return out

    def _retime_params(self, clip: dict, name: str, params: dict) -> dict:
        """The clip's timeline shifted under this stage's time-anchored params.

        Auto-planned params are re-planned against the fresh transcript;
        hand-placed ones are cleared (their times now point at wrong content).
        """
        p = dict(params)
        if name in ("broll", "overlay", "fx") or not p.get("auto"):
            # re-planning these would re-hit the LLM/downloads; clear instead.
            key = "windows" if name == "zoom" else "events"
            if p.get(key):
                p[key] = []
                self.last_notes.append(
                    f"{name}: timeline changed, cleared {key} "
                    "(re-add or use auto mode)")
            return p

        from pipeline.editplan import plan_clip_edits
        words = self.words_for(clip)
        end = (words[-1]["end"] + 1.0) if words else 0.0
        # plan_clip_edits reads PRE-speed words; the event stages run on the sped
        # video, so map planned times into the sped timeline (÷ factor).
        f = self.speed_factor(clip)
        if name == "zoom":
            plan = plan_clip_edits(words, 0.0, end,
                                   density=p.get("density", 0.25), sfx_cap=0)
            p["windows"] = [[e["start"] / f, e["end"] / f,
                             p.get("strength", 1.18)]
                            for e in plan["emphasis"]]
        else:
            from pipeline.orchestrate import SFX_LIBRARY
            plan = plan_clip_edits(words, 0.0, end, density=0.0,
                                   sfx_cap=p.get("cap", 3))
            p["events"] = [{"time": s["time"] / f, "path": SFX_LIBRARY[s["kind"]],
                            "volume": p.get("volume", 0.6)}
                           for s in plan["sfx"] if s["kind"] in SFX_LIBRARY]
        self.last_notes.append(f"{name}: re-planned for the new timeline")
        return p

    def cut_source(self) -> str:
        """Footage the cut stage reads from. INTERACTIVE (default) cuts from the
        proxy when one exists (proxy_or_source falls back to full-res for legacy
        sessions); export_clip pins _render_source to the full-res source."""
        return self._render_source or self.proxy_or_source()

    def _keyframes_for(self, footage: str) -> list[float] | None:
        """Phase-0 I-frame PTS index for `footage`, or None when we don't have
        one. We only carry the index for the full-res SOURCE (Phase 0 builds it
        on source['path']). The proxy is re-encoded with a dense, regular GOP
        (-g 30), so fast_cut's plain stream-copy already snaps to a nearby proxy
        keyframe — no index needed there."""
        if footage == self.data["source"]["path"]:
            kf = self.data.get("source", {}).get("keyframes")
            if isinstance(kf, list) and kf:
                return kf
        return None

    def _out(self, clip: dict, stage: str, params: dict,
             inp: str | None) -> str:
        """Artifact path keyed by (params, input) so older renders survive —
        undo can point back at a real file, and replaying an unchanged stage
        is a free cache hit (the input's name embeds ITS chain's hashes, so
        upstream changes propagate).

        Phase 5 — proxy vs full-res must NOT collide. The cut stage has inp=None,
        so a proxy-cut and a full-res-cut of the same [start,end] would otherwise
        hash to the same name. We mix the cut footage's identity into the key
        ONLY for the cut stage, and ONLY when it differs from the canonical
        full-res source['path']. So the full-res/export and all legacy renders
        keep byte-identical hashes (zero cache churn), while proxy cuts land in a
        distinct artifact. Downstream stages need no change: their inp is the cut
        artifact's name, which already embeds this distinction."""
        import hashlib
        extra = None
        if stage == "cut":
            # Own-clips: each clip cuts from its OWN footage, so its identity must
            # mix into the artifact key — otherwise two clips with the same
            # [start,end] would collide. Legacy/long-video clips keep using
            # cut_source(); only mix when it differs from the canonical full-res
            # source['path'], so legacy hashes stay byte-identical.
            cs = clip.get("source_path") or self.cut_source()
            if cs != self.data["source"]["path"]:
                extra = cs
        payload = [params, inp] if extra is None else [params, inp, extra]
        key = hashlib.sha1(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:8]
        return str(self.workdir / f"clip{clip['id']:02d}_{stage}_{key}.mp4")

    def _run_stage(self, clip: dict, name: str, params: dict,
                   inp: str | None) -> str:
        """Execute one stage. The ONLY place that imports pipeline functions."""
        p = dict(params)
        out_path = self._out(clip, name, p, inp)
        if Path(out_path).exists():
            return out_path

        if name == "cut":
            from pipeline.cut import cut_clip
            # Phase 5: read footage from cut_source() — the proxy in interactive
            # mode, the full-res source under export_clip. Timestamps are 1:1
            # between proxy and source (Phase 0 preserves fps/timebase), so the
            # same [start,end] maps onto either footage identically.
            #
            # The interactive cut now reads the 540p PROXY (cut_source()), so the
            # whole edit cascade re-encodes 540p footage instead of full-res — a
            # big speedup on long sources — while EXPORT (cut_source()==full-res)
            # produces the final at native resolution.
            #
            # The cut stays PRECISE (frame-exact re-encode) by default: it is the
            # foundation of word-timing reconstruction (words_for/timing_chain
            # transcribe THIS artifact and assume t=0 == clip.start), so a
            # keyframe-snapped fast cut here would shift that origin. fast_cut is
            # available as an explicit opt-in (params {"fast": true}) for the
            # candidate-preview / re-encode-cascade case where the few-frames
            # snap is acceptable; it is NOT used for the editable default stack
            # to keep timing exact. See the Phase-5 notes / deferred items.
            # Own-clips: cut from the clip's own uploaded footage, not the
            # nominal shared source. _keyframes_for(footage) returns None for
            # these (the index only exists for source['path']) — fine, the
            # precise re-encode path doesn't need it.
            footage = clip.get("source_path") or self.cut_source()
            if p.get("fast") and not clip.get("locked") \
                    and self._render_source is None:
                from pipeline.cut import fast_cut_snapped
                return fast_cut_snapped(
                    footage, p["start"], p["end"], out_path,
                    keyframes=self._keyframes_for(footage))
            return cut_clip(
                footage, p["start"], p["end"],
                title=clip.get("title", f"clip{clip['id']}"),
                precise=True, index=clip["id"] - 1, out_path=out_path,
            )

        if inp is None:
            raise ValueError(f"Stage '{name}' needs a prior cut.")

        if name == "jumpcut":
            from pipeline.jumpcut import remove_silences
            from pipeline.transcribe import transcribe
            words = transcribe(inp)["words"]
            drop_ranges: list[tuple[float, float]] = []
            if p.get("aggressive_fillers"):
                from pipeline.fillers import classify_filler_ranges
                drop_ranges = classify_filler_ranges(words)
            out = remove_silences(inp, words, clip_start=0.0,
                                  max_pause=p.get("max_pause", 0.5),
                                  remove_fillers=p.get("remove_fillers", False),
                                  filler_words=p.get("filler_words"),
                                  drop_ranges=drop_ranges,
                                  protect_ranges=[tuple(r) for r in
                                                  p.get("protect_ranges", [])],
                                  out_path=out_path)
            return out if out != inp else inp

        if name == "trim":
            from pipeline.jumpcut import remove_ranges
            from pipeline.transcribe import transcribe
            ranges = p.get("ranges", [])
            if not ranges:
                return inp
            # Re-anchor each range by its text against THIS input's transcript
            # so trims survive upstream timing changes (e.g. a new max_pause).
            words = transcribe(inp)["words"]
            resolved = _resolve_trim_ranges(words, ranges)
            return remove_ranges(inp, resolved, out_path=out_path)

        if name == "speed":
            from pipeline.speed import retime
            f = float(p.get("factor", 1.0))
            if abs(f - 1.0) < 1e-3:
                return inp
            return retime(inp, f, out_path=out_path)

        if name == "reframe":
            aspect = p.get("aspect", "9:16")
            if p.get("tracked", True):
                from pipeline.tracking import reframe_vertical_tracked
                return reframe_vertical_tracked(inp, out_path=out_path,
                                                aspect=aspect)
            from pipeline.reframe import reframe_vertical
            return reframe_vertical(inp, out_path=out_path, aspect=aspect)

        if name == "denoise":
            from pipeline.denoise import denoise_audio
            if not p.get("enabled", True):
                return inp
            return denoise_audio(inp, strength=p.get("strength", "medium"),
                                 out_path=out_path)

        if name == "broll":
            from pipeline.broll import overlay_media
            events = p.get("events", [])
            if not events:
                return inp
            return overlay_media(inp, events, out_path=out_path)

        if name == "lut":
            from pipeline.colorfx import apply_look
            if not p.get("look") and not p.get("cube"):
                return inp
            return apply_look(inp, look=p.get("look", ""),
                              cube=p.get("cube", ""),
                              strength=p.get("strength", 0.5),
                              out_path=out_path)

        if name == "overlay":
            from pipeline.overlayfx import apply_overlays
            events = p.get("events", [])
            if not events:
                return inp
            return apply_overlays(inp, events, out_path=out_path)

        if name == "fx":
            from pipeline.overlayfx import emphasis_fx
            events = p.get("events", [])
            if not events:
                return inp
            return emphasis_fx(inp, events, out_path=out_path)

        if name == "brand":
            from pipeline.brand import apply_brand
            if (not p.get("watermark") and not p.get("title")
                    and not p.get("meme_texts")):
                return inp
            return apply_brand(inp, watermark=p.get("watermark"),
                               title=p.get("title"),
                               meme_texts=p.get("meme_texts"),
                               out_path=out_path)

        if name == "zoom":
            from pipeline.effects import punch_zoom
            windows = [tuple(w) for w in p.get("windows", [])]
            return punch_zoom(inp, windows, out_path=out_path)

        if name == "splitscreen":
            from pipeline.splitscreen import apply_splitscreen
            if not p.get("path"):
                return inp
            return apply_splitscreen(inp, p, out_path=out_path)

        if name == "subtitles":
            import pipeline.subtitle as sub
            words = self.words_for(clip)
            # Optional caption translation: render the captions in another
            # language while keeping their timing pinned to the original speech.
            # Done BEFORE the speed remap + emphasis pass so both run on the
            # translated words. Degrades to source-language captions on failure.
            if p.get("lang"):
                from pipeline.translate import translate_captions
                words = translate_captions(words, p["lang"])
            # words_for is PRE-speed; this stage runs on the sped video, so map
            # each word's timing into the sped timeline (p = u / factor).
            f = self.speed_factor(clip)
            if abs(f - 1.0) > 1e-3:
                words = [{**w, "start": w["start"] / f, "end": w["end"] / f}
                         for w in words]
            style = sub.SubStyle(font_size=int(84 * p.get("scale", 1.0)),
                                 caption_y_ratio=p.get("y_ratio", 0.68))
            if p.get("text_color"):
                style.color_base = _hex_rgba(p["text_color"])
            if p.get("highlight_color"):
                style.color_hilite = _hex_rgba(p["highlight_color"])
            if p.get("font") and Path(p["font"]).exists():
                style.font_path = p["font"]
            if p.get("stroke"):
                style.stroke_width = int(p["stroke"])
            if p.get("hilite_pop"):
                style.hilite_scale = float(p["hilite_pop"])
            if "uppercase" in p:
                style.uppercase = bool(p["uppercase"])
            # Caption-engine knobs: per-word entrance animation, a rounded pill
            # behind the active word, an LLM keyword-emphasis pass and auto-emoji.
            if "animation" in p:
                style.animation = str(p["animation"])
            if "pill" in p:
                style.pill = p["pill"]
            if "auto_emoji" in p:
                style.auto_emoji = bool(p["auto_emoji"])
            if p.get("highlight_color"):
                style.color_emphasis = _hex_rgba(p["highlight_color"])
            want_emphasis = str(p.get("emphasis", "none")) == "llm"
            want_emoji = bool(p.get("auto_emoji"))
            if (want_emphasis or want_emoji) and words:
                cache = clip.setdefault("_caption_emphasis", {})
                key = f"{want_emphasis}:{want_emoji}"
                if key not in cache:
                    cache[key] = list(sub._plan_emphasis(
                        words, want_emphasis=want_emphasis,
                        want_emoji=want_emoji))
                emph, emoji_map = cache[key]
                if want_emphasis and emph:
                    style.emphasis_keywords = list(emph)
                if want_emoji and emoji_map:
                    style.emoji_map = dict(emoji_map)
            return sub.burn_subtitles(
                inp, words, clip_start=0.0,
                karaoke=p.get("karaoke", True),
                out_path=out_path, style=style)

        if name == "dub":
            lang = p.get("lang")
            if not lang:
                return inp
            from pipeline.dub import apply_dub
            try:
                segments = self.segments_for(clip)
            except ValueError:
                return inp
            # segments_for is PRE-speed; pass the factor so utterances land in
            # this clip's current (possibly sped) timeline.
            return apply_dub(inp, segments, target_lang=lang,
                             speed=self.speed_factor(clip),
                             voice=p.get("voice") or None, out_path=out_path)

        if name == "music":
            from pipeline.audio import add_background_music
            return add_background_music(
                inp, p["path"], music_volume=p.get("volume", 0.18),
                duck=True, out_path=out_path)

        if name == "ambience":
            from pipeline.soundbed import add_ambience
            return add_ambience(inp, p["path"], volume=p.get("volume", 0.06),
                                out_path=out_path)

        if name == "sfx":
            from pipeline.sfx import add_sfx
            return add_sfx(inp, p.get("events", []),
                           out_path=out_path)

        if name == "fade":
            from pipeline.effects import PLATFORM_LOUDNESS, fade_in_out
            lufs, tp = PLATFORM_LOUDNESS.get(
                p.get("platform") or self.data.get("platform", ""),
                (-14.0, -1.5))
            return fade_in_out(inp, fade=p.get("fade", 0.3), normalize=True,
                               lufs=p.get("lufs", lufs), tp=p.get("tp", tp),
                               out_path=out_path)

        raise ValueError(f"Unhandled stage: {name}")


def _resolve_trim_ranges(words: list[dict],
                         ranges: list[dict]) -> list[tuple[float, float]]:
    """Anchor-relocate trim ranges against a transcript (render + timemap)."""
    resolved = []
    for r in ranges:
        s, e = float(r["start"]), float(r["end"])
        if r.get("anchor_text"):
            loc = _locate_anchor(words, r["anchor_text"])
            if loc:
                s, e = loc
        resolved.append((max(0.0, s - 0.02), e + 0.02))
    return resolved


def _locate_anchor(words: list[dict], text: str) -> tuple[float, float] | None:
    """Find a word sequence in a transcript; return its (start, end) span."""
    from pipeline.jumpcut import _norm_word

    target = [_norm_word(t) for t in text.split() if _norm_word(t)]
    if not target:
        return None
    toks = [_norm_word(w["word"]) for w in words]
    n = len(target)
    for i in range(len(toks) - n + 1):
        if toks[i:i + n] == target:
            return words[i]["start"], words[i + n - 1]["end"]
    return None


def _hex_rgba(hex_color: str) -> tuple[int, int, int, int]:
    """'#ff3322' / 'ff3322' -> (r, g, b, 255)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


def open_in_player(path: str) -> None:
    subprocess.run(["open", path], check=False)
