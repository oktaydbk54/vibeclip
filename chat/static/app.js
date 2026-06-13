/* VibeClip Studio — client logic. Talks to the FastAPI chat backend. */

const $ = (id) => document.getElementById(id);
const chat = $("chat"), input = $("input"), send = $("send");
const player = $("player"), playerB = $("playerB"), empty = $("empty");
const stageEl = $("stagewrap");

let activeClip = null;
let CLIPS = [], COMPS = [];
let GENERATING = false;   // a generate/preprocess job is running with no clips yet
                          // → the library shows shimmer skeleton cards (not bare text)
// Phase 4 — sequential editing queue: the server-side focus cursor + batch
// counts. QUEUE mirrors session.queue_summary(); activeClip stays the single
// source of truth for "which clip is loaded" (we keep it aligned with the
// cursor). Defaults are harmless until the first state load.
let QUEUE = { total: 0, position: 0, approved: 0, skipped: 0, pending: 0,
              exported: 0, active_clip_id: null };
let MODE = localStorage.getItem("kesim_mode") || "basit";
let TIER = localStorage.getItem("kesim_tier") || "fast";  // fast|pro AI brain

const KIND_ICON = { video: "🎞", image: "🖼", audio: "🎵", font: "🔤", lut: "🎨" };

/* ---------------------------------------------------------------- timecode */
let TC_FPS = 30;
function fmtTC(seconds, fps) {
  fps = fps || TC_FPS;
  const nominal = Math.max(1, Math.round(fps));
  const tf = Math.round(Math.max(0, seconds) * fps);
  const ff = tf % nominal, rest = Math.floor(tf / nominal);
  const p2 = n => String(n).padStart(2, "0");
  return `${p2(Math.floor(rest/3600))}:${p2(Math.floor(rest/60)%60)}:` +
         `${p2(rest%60)}:${p2(ff)}`;
}
window.fmtTC = fmtTC;
function parseTC(tc, fps) {
  fps = fps || TC_FPS;
  const nominal = Math.max(1, Math.round(fps));
  const p = String(tc).trim().split(":").map(Number);
  while (p.length < 4) p.unshift(0);
  const [hh, mm, ss, ff] = p.slice(-4);
  return (((hh*60+mm)*60+ss)*nominal + ff) / fps;
}
function paintTC() { $("tc").textContent = fmtTC(player.currentTime || 0); }
function tcLoop() {
  paintTC();
  if ("requestVideoFrameCallback" in player)
    player.requestVideoFrameCallback((now, meta) => {
      $("tc").textContent = fmtTC(meta ? meta.mediaTime : player.currentTime);
      tcLoop();
    });
}
player.addEventListener("seeked", paintTC);
player.addEventListener("timeupdate", paintTC);
player.addEventListener("loadedmetadata", () => { tcLoop(); });
tcLoop();
// click the timecode to jump (G also does this)
$("tc").style.cursor = "pointer";
$("tc").title = "click → jump to timecode";
$("tc").onclick = gotoTimecode;
function gotoTimecode() {
  const v = prompt("Jump to timecode (HH:MM:SS:FF):", fmtTC(player.currentTime));
  if (v) { player.currentTime = parseTC(v); player.pause(); }
}

function setBusy(b, label) {
  $("status").classList.toggle("busy", b);
  $("statusTxt").textContent = label || (b ? "RENDER..." : "READY");
}

/* ----------------------------------------------------- jobs (SSE + async) */
const JOB_WAITERS = {};   // job_id -> resolve(job)
const JOB_DONE = {};      // job_id -> terminal job (covers fast-finish race)
let curJobId = null;
// The chat turn currently awaiting completion (A1 live narration). Events
// arrive for ANY job; we only narrate into the thinking bubble when the event
// job id matches this one.
let chatJobId = null;
let chatThinkingEl = null;

/* Live-update the chat .thinking bubble from a job_progress event: friendly
   stage/tool line + a thin progress bar driven by the job's pct. */
function narrateThinking(job) {
  if (!chatThinkingEl) return;
  const { key } = parseJobMsg(job.message);
  const label = STAGE_TR[key] || key || job.label || "working";
  const friendly = STAGE_FRIENDLY[key] || "working on it";
  const pct = Math.round((job.progress || 0) * 100);
  chatThinkingEl.innerHTML =
    `<div class="th-row"><span class="bar"></span>` +
    `<span class="th-stage mono">${label}</span>` +
    `<span class="th-friendly">${friendly}</span></div>` +
    `<div class="th-prog"><i style="width:${pct}%"></i></div>`;
  chat.scrollTop = chat.scrollHeight;
}

const STAGE_TR = {
  cut: "cut", jumpcut: "silence", trim: "trim", reframe: "reframe",
  broll: "b-roll", lut: "color", zoom: "zoom", subtitles: "captions",
  overlay: "overlay", brand: "brand", fx: "fx", music: "music",
  ambience: "ambience", sfx: "sfx", fade: "master",
};

/* Friendly one-liners for the live chat narration (A1). Keyed by stage name
   (from the render pipeline) and by tool name (from on_tool). Falls back to a
   generic line so unknown stages/tools still read nicely. */
const STAGE_FRIENDLY = {
  cut: "trimming to the moment",
  jumpcut: "removing dead air",
  trim: "trimming the edges",
  reframe: "reframing to vertical",
  broll: "weaving in b-roll",
  lut: "grading the color",
  zoom: "punching in for emphasis",
  subtitles: "rendering word-synced captions",
  overlay: "placing overlays",
  brand: "stamping the brand",
  fx: "adding effects",
  music: "scoring the music bed",
  ambience: "laying ambience",
  sfx: "dropping sound effects",
  fade: "polishing loudness",
  // tool-level lines (chat agent dispatch)
  propose_edit: "drafting an edit & rendering a preview",
  generate_clips: "scanning for the best moments",
  set_style: "applying the style",
  remove_fillers: "cleaning up the umms",
  set_music: "scoring the music bed",
  add_sound_effect: "dropping a sound effect",
  set_subtitles: "styling the captions",
  set_denoise: "cleaning background noise",
  add_zoom: "punching in for emphasis",
};

/* Split a job.message that may be "tool|args" (A1 tool narration) or a bare
   stage name. Returns {key, args} where key is the lookup token. */
function parseJobMsg(msg) {
  if (!msg) return { key: "", args: "" };
  const i = msg.indexOf("|");
  return i < 0 ? { key: msg, args: "" }
               : { key: msg.slice(0, i), args: msg.slice(i + 1) };
}

/* Toggle the "generating clips" skeleton state, re-rendering the library only
   when the flag actually flips (job_progress fires many times a second). */
function setGenerating(on) {
  on = !!on;
  if (on === GENERATING) return;
  GENERATING = on;
  renderLibrary();
}

function showJobChip(job) {
  curJobId = job.id;
  // With no clips yet, any running job is generation/preprocessing (per-clip
  // renders only happen after clips exist) → show skeletons in the library.
  setGenerating(!CLIPS.length);
  const chip = $("jobchip");
  chip.style.display = "flex";
  const { key } = parseJobMsg(job.message);
  $("jcLabel").textContent = STAGE_TR[key] || key || job.label || "render";
  const pct = Math.round((job.progress || 0) * 100);
  $("jcFill").style.width = pct + "%";
  $("jcPct").textContent = pct + "%";
  // Phase 4 — mirror the same progress inside the phone's rendering state.
  const ej = $("ejFill"); if (ej) ej.style.width = pct + "%";
  const l = $("ejLbl");
  if (l) l.textContent = (STAGE_TR[key] || key || "render") + " · " + pct + "%";
  setBusy(true, "RENDER...");
}
function hideJobChip() {
  curJobId = null;
  $("jobchip").style.display = "none";
  setBusy(false);
  setGenerating(false);
}

function initEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (e) => {
    let evt; try { evt = JSON.parse(e.data); } catch (x) { return; }
    const job = evt.job; if (!job) return;
    if (evt.type === "job_done") {
      JOB_DONE[job.id] = job;
      hideJobChip();
      const w = JOB_WAITERS[job.id];
      if (w) { delete JOB_WAITERS[job.id]; w(job); }
    } else {
      showJobChip(job);
      // Live narration: only for the chat turn we're awaiting (A1).
      if (job.id === chatJobId) narrateThinking(job);
    }
  };
  es.onerror = () => {};   // EventSource auto-reconnects
}

/* POST a job-returning endpoint and resolve when its job finishes (via SSE).
   Falls back to a plain sync response if the endpoint didn't return a job_id. */
function runJob(url, body, onStart) {
  return new Promise(async (resolve, reject) => {
    let r;
    try {
      const resp = await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      // Mutating endpoints now require auth (A9). A 401 means the session
      // expired/was never set — bounce to login like boot() does for /api/me.
      if (resp.status === 401) { location.href = "/login?next=/studio"; return; }
      r = await resp.json();
    } catch (e) { reject(e); return; }
    if (r.error) { reject(new Error(r.error)); return; }
    if (!r.job_id) { resolve({ status: "done", result: r }); return; }
    if (onStart) onStart(r.job_id);
    const settle = (job) => {
      if (job.status === "error") reject(new Error(job.error || "render error"));
      else resolve(job);
    };
    if (JOB_DONE[r.job_id]) { settle(JOB_DONE[r.job_id]); return; }
    JOB_WAITERS[r.job_id] = settle;
  });
}
window.runJob = runJob;

document.addEventListener("DOMContentLoaded", () => {
  $("jcCancel").onclick = () => {
    if (curJobId) fetch(`/api/jobs/${curJobId}/cancel`, { method: "POST" });
  };
});

/* ----------------------------------------------------------------- mode */
/* BASİT: chat + video, every edit through A/B approval, proactive guide.
   PRO:   full NLE chrome (library, timeline dock, transcript, drag&drop). */
function applyMode(m, save) {
  MODE = m;
  if (save) localStorage.setItem("kesim_mode", m);
  document.body.classList.toggle("mode-basit", m === "basit");
  $("modeBasit").classList.toggle("on", m === "basit");
  $("modePro").classList.toggle("on", m === "pro");
  renderChips();
  renderClipStrip();
  if (m === "basit") starterCard();
  window.dispatchEvent(new CustomEvent("kesim:mode", { detail: m }));
}
document.addEventListener("DOMContentLoaded", () => {
  $("modeBasit").onclick = () => applyMode("basit", true);
  $("modePro").onclick = () => applyMode("pro", true);
  applyTier(TIER, false);
  $("tierFast").onclick = () => applyTier("fast", true);
  $("tierPro").onclick = () => applyTier("pro", true);
});

/* AI brain tier — Fast (gpt-4o-mini) vs Pro (stronger model) for editing. */
function applyTier(t, save) {
  TIER = t;
  if (save) localStorage.setItem("kesim_tier", t);
  const f = $("tierFast"), p = $("tierPro");
  if (f) f.classList.toggle("on", t === "fast");
  if (p) p.classList.toggle("on", t === "pro");
}

/* basit-mode onboarding: ask what the user wants to make + one-tap starts */
function starterCard() {
  if ($("starterCard")) return;
  const card = document.createElement("div");
  card.className = "starter-card"; card.id = "starterCard";
  card.innerHTML = `
    <div class="pt">LET'S START</div>
    <div class="ps">You don't need editing skills — tell me what you want to
make and I'll handle the rest. Before any change, I'll show you an
<b>A/B comparison</b> and wait for your approval.</div>
    <div class="opts"></div>`;
  const opts = [
    ["🎬 Clip the best moments",
     "extract the best moments from this video as short clips"],
    ["🚀 Viral TikTok clip",
     "make a viral-ready tiktok clip from this video, with captions and music"],
    ["🎓 Educational cut",
     "create an educational highlight from this video, clean and clear"],
    ["🤔 Suggest ideas — what can I make?",
     "analyze the video and suggest what content I could create"],
  ];
  const box = card.querySelector(".opts");
  opts.forEach(([label, msg]) => {
    const b = document.createElement("button");
    b.className = "opt"; b.type = "button"; b.textContent = label;
    b.onclick = () => sendMessage(msg);
    box.appendChild(b);
  });
  chat.appendChild(card); chat.scrollTop = chat.scrollHeight;
}

/* ----------------------------------------------------------------- tabs */
document.querySelectorAll(".tab").forEach(t => {
  t.onclick = () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("on"));
    document.querySelectorAll(".tabpage").forEach(x => x.classList.remove("on"));
    t.classList.add("on");
    $("page-" + t.dataset.page).classList.add("on");
  };
});

/* ---------------------------------------------------------------- library */
/* A3 — letter grade for a 0..100 sub-score. */
function scoreGrade(n) {
  return n >= 85 ? "A" : n >= 70 ? "B" : n >= 55 ? "C" : "D";
}
/* Three mini HOOK/FLOW/VALUE bars; empty string when no scores (old sessions). */
function scoreBars(scores) {
  if (!scores) return "";
  const rows = [["HOOK", scores.hook], ["FLOW", scores.flow],
                ["VALUE", scores.value]]
    .map(([lbl, v]) => {
      const n = Math.max(0, Math.min(100, Number(v) || 0));
      return `<div class="vrow"><span class="vlbl mono">${lbl}</span>` +
        `<span class="vbar"><i style="width:${n}%"></i></span>` +
        `<span class="vgrade mono">${scoreGrade(n)}</span></div>`;
    }).join("");
  return `<div class="viral">${rows}</div>`;
}

/* Phase 3 — review-queue status. Old clips without a status read as pending. */
const STATUS_LABEL = { pending: "PENDING", approved: "APPROVED",
                       skipped: "SKIPPED", exported: "EXPORTED" };
function clipStatus(c) {
  const s = c && c.status;
  return STATUS_LABEL[s] ? s : "pending";
}

/* Push a clip's status to the server (no LLM, no re-render, keeps artifacts).
   Optimistically updates local state then reconciles on the job result. */
function setClipStatus(c, status) {
  c.status = status;            // optimistic — re-render immediately
  renderLibrary();
  runJob("/api/tool", { name: "set_clip_status",
                        args: { clip_id: c.id, status } })
    .then(reloadState).catch(() => reloadState());
}

function clipCard(c, i) {
  const card = document.createElement("div");
  const status = clipStatus(c);
  // Phase 4 — the focused (queue-cursor) clip gets a distinct focus ring on top
  // of the existing .active selection styling.
  const isFocus = c.id === activeClip;
  card.className = "clipcard st-" + status +
    (isFocus ? " active focus" : "") +
    (RENDERING[c.id] ? " rendering" : "");
  card.style.animationDelay = (i * 50) + "ms";
  if (c.reason) card.title = c.reason;   // A3 — reason as hover tooltip
  const range = `${c.start.toFixed(1)}–${c.end.toFixed(1)}s`;
  // Score-tier the badge (green/amber/dim) and crown the top-ranked candidate —
  // the opus/reap "best clip first" triage cue, on top of the existing sort.
  const sc = Number(c.score) || 0;
  const tier = sc >= 80 ? "hi" : sc >= 55 ? "mid" : "lo";
  const isBest = i === 0 && status !== "skipped" && CLIPS.length > 1;
  const hookLine = c.hook
    ? `<div class="hookq">“${c.hook}”</div>` : "";
  // Phase 3 — skip prunes from the top of the queue without deleting files;
  // restore sets it back to pending. The pip shows current review status.
  const skipped = status === "skipped";
  const queueBtn = `<button class="qbtn mono" data-act="${skipped ? "restore" : "skip"}"
      title="${skipped ? "restore to the queue" : "skip — hide without deleting"}">${
      skipped ? "↺ restore" : "✕ skip"}</button>`;
  card.innerHTML = `
    <div class="thumb" ${c.url ? `style="background-image:url(/thumb/${c.id}?t=${Date.now()})"` : ""}>${c.url ? "" : "◻"}</div>
    <div class="meta">
      <div class="row"><span class="num mono">#${String(c.id).padStart(2,"0")}</span>
        ${isBest ? `<span class="bestbadge mono" title="Top-ranked clip">★ BEST</span>` : ""}
        <span class="ttl">${c.title}</span>
        <span class="vscore vscore-${tier} mono" title="Clip score ${c.score}/99">${c.score}</span></div>
      <div class="row qrow">
        <span class="pip pip-${status}" title="${STATUS_LABEL[status]}"></span>
        <span class="sub mono">${range}</span>
        ${queueBtn}
      </div>
      ${hookLine}
      ${scoreBars(c.scores)}
      <div class="badges">
        ${c.style ? `<span class="badge style">${c.style}</span>` : ""}
        ${c.variant_of ? `<span class="badge variant">var #${c.variant_of}</span>` : ""}
      </div>
    </div>`;
  // Selection reuses the existing playClip path; the skip/restore button is a
  // separate control that must not also trigger selection.
  card.onclick = () => playClip(c);
  const qb = card.querySelector(".qbtn");
  qb.onclick = (e) => {
    e.stopPropagation();
    setClipStatus(c, qb.dataset.act === "restore" ? "pending" : "skipped");
  };
  return card;
}

/* Shimmer ghost cards shown while clips are being generated — turns the dead
   ~90s wait into something that looks alive (cards resolve as candidates land). */
function skeletonCards(n) {
  let h = `<div class="sk-head mono">Scanning your video for the best moments…</div>`;
  for (let i = 0; i < n; i++) {
    h += `<div class="clipcard skeleton" style="animation-delay:${i * 70}ms">
      <div class="thumb sk"></div>
      <div class="meta">
        <div class="sk-line sk" style="width:62%"></div>
        <div class="sk-line sk" style="width:40%"></div>
        <div class="sk-line sk" style="width:88%"></div>
        <div class="sk-line sk" style="width:74%"></div>
      </div></div>`;
  }
  return h;
}

function renderLibrary() {
  const page = $("page-clips");
  page.innerHTML = "";
  if (!CLIPS.length) {
    if (GENERATING) { page.innerHTML = skeletonCards(6); return; }
    page.innerHTML = `<div class="sub mono" style="padding:8px;color:var(--bone-faint)">
      no clips yet — ask the AI on the right: "extract 3 clips from this video"</div>`;
  }
  // Keep the ranked order (id asc = score desc) but recede skipped candidates
  // to the bottom of the queue. Stable: same-bucket order is preserved.
  const ranked = CLIPS
    .map((c, i) => [c, i])
    .sort((a, b) => {
      const sa = clipStatus(a[0]) === "skipped" ? 1 : 0;
      const sb = clipStatus(b[0]) === "skipped" ? 1 : 0;
      return sa - sb || a[1] - b[1];
    });
  ranked.forEach(([c], i) => page.appendChild(clipCard(c, i)));
  if (COMPS.length) {
    const h = document.createElement("div");
    h.className = "sect"; h.textContent = "Compilations";
    page.appendChild(h);
    COMPS.forEach(cp => {
      const card = document.createElement("div");
      card.className = "clipcard";
      card.innerHTML = `
        <div class="thumb">▣</div>
        <div class="meta">
          <div class="row"><span class="num mono">C${cp.id}</span>
            <span class="ttl">${cp.title}</span></div>
          <div class="sub mono">${cp.duration}s · clips ${cp.clips.join(", ")}</div>
        </div>`;
      card.onclick = () => playClip({ id: cp.id, title: cp.title, url: cp.url, comp: true });
      page.appendChild(card);
    });
  }
  renderClipStrip();
}

/* basit-mode clip switcher: compact chips under the player */
function renderClipStrip() {
  const strip = $("clipstrip");
  if (!strip) return;
  strip.innerHTML = "";
  CLIPS.forEach(c => {
    const b = document.createElement("button");
    b.className = "clipchip" + (c.id === activeClip ? " on" : "");
    b.type = "button";
    b.textContent = `#${c.id} ${c.title}`;
    b.title = c.title;
    b.onclick = () => playClip(c);
    strip.appendChild(b);
  });
}

/* ---------------------------------------------- Phase 4: editing queue nav */
/* The clip in focus (activeClip) is the queue cursor. These controls only move
   WHICH clip is loaded — they NEVER apply edits. Per-clip editing still flows
   through the chat A/B approval gate (propose_edit -> preview -> APPLY/DISCARD),
   unchanged. "Approve & next" marks the focused clip approved via the existing
   set_clip_status tool (not a render, not gated) and advances. */

/* "clip N / M · X approved" in the appbar; hidden when there are no clips. */
function renderQueueChip() {
  const chip = $("queuechip");
  if (!chip) return;
  if (!CLIPS.length || !QUEUE.total) { chip.style.display = "none"; return; }
  const pos = QUEUE.position || 0;
  chip.style.display = "";
  chip.innerHTML =
    `clip <b>${pos || "—"}</b> / ${QUEUE.total} · ` +
    `<b>${QUEUE.approved || 0}</b> approved` +
    (QUEUE.skipped ? ` · ${QUEUE.skipped} skipped` : "");
  // Disable nav at the queue edges / when there's nothing to move to.
  const prevable = nextNonSkipped(-1) != null;
  const nextable = nextNonSkipped(1) != null;
  const pending = nextPending() != null;
  const setBtn = (id, on) => { const b = $(id); if (b) b.disabled = !on; };
  setBtn("qnPrev", prevable);
  setBtn("qnNext", nextable);
  // approve&next is allowed as long as there's a focused clip; if no next
  // pending clip exists it just approves the last one and stays put.
  setBtn("qnApprove", activeClip != null);
}

/* Ranked order the cards use: skipped clips recede to the bottom. We navigate
   over the SAME visible order so Prev/Next match what the user sees. */
function rankedClips() {
  return CLIPS
    .map((c, i) => [c, i])
    .sort((a, b) => {
      const sa = clipStatus(a[0]) === "skipped" ? 1 : 0;
      const sb = clipStatus(b[0]) === "skipped" ? 1 : 0;
      return sa - sb || a[1] - b[1];
    })
    .map(([c]) => c);
}

/* Next/prev NON-skipped clip relative to the focused one, in ranked order.
   Returns the clip object or null at the edge. */
function nextNonSkipped(dir) {
  const order = rankedClips();
  let idx = order.findIndex(c => c.id === activeClip);
  if (idx < 0) idx = 0;
  for (let i = idx + (dir >= 0 ? 1 : -1); i >= 0 && i < order.length; i += (dir >= 0 ? 1 : -1)) {
    if (clipStatus(order[i]) !== "skipped") return order[i];
  }
  return null;
}

/* Next still-PENDING clip after the focused one (for approve & next). */
function nextPending() {
  const order = rankedClips();
  let idx = order.findIndex(c => c.id === activeClip);
  if (idx < 0) idx = 0;
  for (let i = idx + 1; i < order.length; i++) {
    if (clipStatus(order[i]) === "pending") return order[i];
  }
  return null;
}

/* Move the cursor to a clip: persist via /api/active-clip, then LOAD it through
   the existing playClip() path so transcript/timeline/preview all update (the
   kesim:clip event playClip fires already wires those panels). */
function goToClip(c) {
  if (!c) return;
  playClip(c);                         // reuse the one selection path
  fetch("/api/active-clip", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clip_id: c.id }),
  }).then(r => {
    if (r.status === 401) { location.href = "/login?next=/studio"; return null; }
    return r.json();
  }).then(d => {
    if (d && d.queue) { QUEUE = d.queue; renderQueueChip(); }
  }).catch(() => {});
}

function queueNext() { const c = nextNonSkipped(1); if (c) goToClip(c); }
function queuePrev() { const c = nextNonSkipped(-1); if (c) goToClip(c); }

/* Approve & next: mark the FOCUSED clip approved (bookkeeping only — this does
   NOT apply any edit; edits already went through the A/B gate), then advance to
   the next pending clip. Reuses set_clip_status (not gated, not a render). */
function approveAndNext() {
  if (activeClip == null) return;
  const cur = CLIPS.find(c => c.id === activeClip);
  if (!cur) return;
  const nxt = nextPending();           // capture target before status flips
  celebrateApprove();                  // quick green ring-burst over the stage
  setClipStatus(cur, "approved");      // existing helper: optimistic + /api/tool
  if (nxt) goToClip(nxt);
  else { renderQueueChip(); }          // last clip — approved, nothing to advance to
}

/* A short delight beat when a clip is approved — a green ring + check pulse over
   the viewer stage. Pure CSS animation, self-removing. */
function celebrateApprove() {
  if (!stageEl) return;
  const b = document.createElement("div");
  b.className = "approve-burst";
  b.innerHTML = '<span class="ab-check">✓</span>';
  stageEl.appendChild(b);
  setTimeout(() => b.remove(), 760);
}

document.addEventListener("DOMContentLoaded", () => {
  const wire = (id, fn) => { const b = $(id); if (b) b.onclick = fn; };
  wire("qnPrev", queuePrev);
  wire("qnNext", queueNext);
  wire("qnApprove", approveAndNext);
  // Phase 2 — deliver popover: ▾ folds the pro caption/NLE/QC tools open/closed.
  const dm = $("deliverMore");
  if (dm) dm.onclick = (e) => {
    e.stopPropagation();
    $("ccExport").classList.toggle("open");
  };
  document.addEventListener("click", (e) => {
    const cc = $("ccExport");
    if (cc && cc.classList.contains("open") && !cc.contains(e.target))
      cc.classList.remove("open");
  });
});

/* ---------------------------------------------------------------- assets */
async function loadAssets() {
  try {
    const d = await (await fetch("/api/assets")).json();
    const grid = $("assetgrid");
    grid.innerHTML = "";
    (d.assets || []).forEach((a, i) => {
      const cell = document.createElement("div");
      cell.className = "assetcell";
      cell.style.animationDelay = (i * 40) + "ms";
      cell.title = a.description;
      cell.innerHTML = `
        <div class="pic" ${a.thumb ? `style="background-image:url(${a.thumb})"` : ""}>
          ${a.thumb ? "" : (KIND_ICON[a.kind] || "📦")}</div>
        <div class="nm">${a.name || a.id}</div>`;
      cell.onclick = () => {
        input.value = `suggest assets for clip ${activeClip || 1}`;
        input.focus();
      };
      grid.appendChild(cell);
    });
    $("assetEmpty").style.display = (d.assets || []).length ? "none" : "";
  } catch (e) {}
}

/* --------------------------------------- source video upload (Phase 0) */
/* The project-name chip in the appbar opens a hidden file input; uploading a
   video creates/swaps the active session and kicks off the 540p proxy build
   (surfaced via the normal job chip + SSE). Lightweight by design — the full
   project switcher is a later phase. */
/* Appbar project chip shows the filename only; the full duration · WxH · fps
   lives in the hover title to keep the spine quiet. */
function setProjName(src) {
  $("projname").innerHTML = `<b>${src.path.split("/").pop()}</b>`;
  $("projname").title =
    `${Math.round(src.duration)}s · ${src.width}×${src.height} · ` +
    `${(src.fps || 30).toFixed(2)}fps`;
}

$("replaceVideo").onclick = () => $("upvideo").click();
$("upvideo").addEventListener("change", (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  addMsg("tool", `uploading video: ${f.name}…`);
  setBusy(true, "UPLOAD 0%");
  const fd = new FormData(); fd.append("file", f);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload-video");
  xhr.upload.onprogress = (ev) => {
    if (ev.lengthComputable) {
      setBusy(true, "UPLOAD " + Math.round((ev.loaded / ev.total) * 100) + "%");
    }
  };
  xhr.onload = () => {
    setBusy(false);
    if (xhr.status === 401) { location.href = "/login?next=/studio"; return; }
    let d = {}; try { d = JSON.parse(xhr.responseText); } catch (x) {}
    if (xhr.status === 409) {
      addMsg("bot", `✗ ${d.error || "A job is running — try again shortly."}`);
      return;
    }
    if (!d.ok) {
      addMsg("bot", `✗ ${f.name}: ${d.error || "upload failed"}`);
      return;
    }
    addMsg("bot",
      `✓ ${f.name} loaded — ${Math.round(d.duration)}s. Building 540p proxy in the background; analysis will use it when ready.`);
    // The proxy job will surface on the job chip via SSE; pull fresh state now
    // so the appbar reflects the new source immediately.
    reloadState();
  };
  xhr.onerror = () => { setBusy(false); addMsg("bot", `✗ ${f.name}: network error`); };
  xhr.send(fd);
});

$("upbtn").onclick = () => $("upfile").click();
$("upfile").addEventListener("change", async (e) => {
  for (const f of e.target.files) {
    addMsg("tool", `uploading: ${f.name}…`);
    setBusy(true, "ANALYZING...");
    const fd = new FormData(); fd.append("file", f);
    try {
      const d = await (await fetch("/api/assets/upload", { method: "POST", body: fd })).json();
      addMsg(d.ok ? "bot" : "bot",
        d.ok ? `✓ ${f.name} added to library — ${d.asset.description}`
             : `✗ ${f.name}: ${d.error}`);
    } catch (err) { addMsg("bot", `✗ ${f.name}: ${err}`); }
  }
  e.target.value = "";
  setBusy(false);
  loadAssets();
});

/* -------------------------------------------------- sound palette (pro) */
async function loadSounds() {
  try {
    const d = await (await fetch("/api/sounds")).json();
    const list = $("soundlist");
    list.innerHTML = "";
    const items = [];
    (d.music || []).forEach(m =>
      items.push({ type: "music", name: m.name, path: m.path, mood: m.mood }));
    (d.sfx || []).forEach(k =>
      items.push({ type: "sfx", name: k, kind: k }));
    items.forEach(p => {
      const el = document.createElement("div");
      el.className = "sounditem " + p.type;
      el.innerHTML = `<span class="ic">${p.type === "music" ? "🎵" : "🔔"}</span>
        <span class="nm">${p.name}</span>
        <span class="mood">${p.type === "music" ? p.mood : "sfx"}</span>`;
      el.title = p.type === "music"
        ? "drag onto the timeline → becomes the clip's music"
        : "drag onto the timeline → sfx at the drop point";
      el.addEventListener("mousedown", (e) => startSoundDrag(e, p));
      list.appendChild(el);
    });
    $("soundsect").style.display = items.length ? "" : "none";
  } catch (e) {}
}

/* Mouse-based drag (same engine philosophy as the timeline: mouse events,
   document-level move/up — no HTML5 dnd). A ghost chip follows the cursor;
   releasing over the timeline body drops the sound there. */
let sndDrag = null;
function startSoundDrag(e, payload) {
  if (e.button !== 0) return;
  e.preventDefault();
  const g = document.createElement("div");
  g.className = "snd-ghost";
  g.textContent = (payload.type === "music" ? "🎵 " : "🔔 ") + payload.name;
  g.style.left = e.clientX + "px"; g.style.top = e.clientY + "px";
  document.body.appendChild(g);
  sndDrag = { payload, ghost: g, moved: false, sx: e.clientX, sy: e.clientY };
}
document.addEventListener("mousemove", (e) => {
  if (!sndDrag) return;
  if (Math.abs(e.clientX - sndDrag.sx) + Math.abs(e.clientY - sndDrag.sy) > 3)
    sndDrag.moved = true;
  sndDrag.ghost.style.left = e.clientX + "px";
  sndDrag.ghost.style.top = e.clientY + "px";
  window.tlSoundHover && window.tlSoundHover(e.clientX, e.clientY);
});
document.addEventListener("mouseup", (e) => {
  if (!sndDrag) return;
  const d = sndDrag; sndDrag = null;
  d.ghost.remove();
  window.tlSoundHover && window.tlSoundHover(null, null);
  if (!d.moved) return;
  window.tlSoundDrop && window.tlSoundDrop(e.clientX, e.clientY, d.payload);
});

/* ---------------------------------------------------------------- history */
function renderHistory(hist) {
  const page = $("page-history");
  page.innerHTML = "";
  if (!hist.length) {
    page.innerHTML = `<div class="sub mono" style="padding:8px;color:var(--bone-faint)">history is empty</div>`;
    return;
  }
  hist.slice().reverse().forEach(h => {
    const it = document.createElement("div");
    it.className = "histitem";
    it.innerHTML = `<span class="idx">${String(h.index + 1).padStart(2,"0")}</span>
      <span>${h.label}</span>`;
    it.title = "Revert to this point";
    it.onclick = async () => {
      const d = await (await fetch("/api/restore/" + h.index, { method: "POST" })).json();
      if (d.ok) {
        addMsg("tool", `restore(${h.label})`);
        CLIPS = d.clips; renderLibrary(); renderHistory(d.history || []);
        refreshActive();
      }
    };
    page.appendChild(it);
  });
}

/* ---------------------------------------------------------------- viewer */
function variantGroup(c) {
  const root = c.variant_of || c.id;
  return CLIPS.filter(x => (x.variant_of || x.id) === root);
}

/* A2 — which side is audible in A/B compare. Default A; B is normally muted
   (HTML), but proposals with audio changes default to B so you can HEAR them. */
function setCompareAudio(side) {
  const onB = side === "B";
  player.muted = onB;
  playerB.muted = !onB;
  const aA = $("aaA"), aB = $("aaB");
  if (aA) aA.classList.toggle("on", !onB);
  if (aB) aB.classList.toggle("on", onB);
}
$("aaA").onclick = () => setCompareAudio("A");
$("aaB").onclick = () => setCompareAudio("B");

/* Audio steps where hearing side B matters for judging the proposal. */
const AUDIO_STEPS = ["music", "sfx", "sound", "loudness", "fade", "denoise",
                     "ambience", "master"];
function planHasAudio(plan) {
  return (plan.steps || []).some(s => {
    const a = String(s.action || "").toLowerCase();
    return AUDIO_STEPS.some(k => a.includes(k));
  });
}

function exitCompare() {
  stageEl.classList.remove("compare");
  playerB.pause(); playerB.removeAttribute("src");
  $("abAudio").style.display = "none";
  setCompareAudio("A");   // restore: A audible, B muted (default outside compare)
  // Phase 3 — leave the decision state: undim the room, hide the context line.
  document.body.classList.remove("deciding");
  $("planStrip").style.display = "none";
  window.dispatchEvent(new CustomEvent("kesim:ghost", { detail: null }));
}

function enterCompare(a, b) {
  stageEl.classList.add("compare");
  document.body.classList.add("deciding");
  $("planStrip").textContent = "Variant comparison — pick the keeper";
  $("planStrip").style.display = "";
  $("abAlabel").textContent = `A — CLIP #${a.id}`;
  $("abBlabel").textContent = `B — CLIP #${b.id}`;
  $("pickA").textContent = "PICK THIS";
  $("pickB").textContent = "PICK THIS";
  $("pickA").onclick = () => { exitCompare(); sendMessage(`pick clip ${a.id}, archive the other variants`); };
  $("pickB").onclick = () => { exitCompare(); sendMessage(`pick clip ${b.id}, archive the other variants`); };
  $("abAudio").style.display = "inline-flex";
  setCompareAudio("A");
  playerB.src = b.url + "?t=" + Date.now();
  playerB.currentTime = player.currentTime || 0;
  if (!player.paused) playerB.play().catch(() => {});
}

/* Plan A/B: A = clip as it is now, B = the proposed plan, pre-rendered.
   Approving is instant — the preview render is reused as cache. */
function enterPlanCompare(plan) {
  const cur = CLIPS.find(c => c.id === plan.clip_id);
  if (cur && cur.url) {
    playClip(cur, true);
    player.pause();
  }
  stageEl.classList.add("compare");
  document.body.classList.add("deciding");
  $("planStrip").textContent = plan.summary || "Proposed edit — compare A and B";
  $("planStrip").style.display = "";
  $("abAlabel").textContent = `A — CURRENT #${plan.clip_id}`;
  $("abBlabel").textContent = "B — PROPOSAL";
  $("pickA").textContent = "KEEP A — DISCARD";
  $("pickB").textContent = "APPROVE — APPLY B";
  $("pickA").onclick = () => { exitCompare(); sendMessage("cancel the plan"); };
  $("pickB").onclick = () => { exitCompare(); sendMessage("apply the plan"); };
  // A2 — if the proposal changes audio, default to hearing side B.
  $("abAudio").style.display = "inline-flex";
  setCompareAudio(planHasAudio(plan) ? "B" : "A");
  playerB.src = "/media/plan_preview?t=" + Date.now();
  playerB.currentTime = 0;
  player.currentTime = 0;
  player.play().catch(() => {});
  // Ghost-diff: hand the plan-result timeline to timeline.js as an overlay.
  window.dispatchEvent(new CustomEvent("kesim:ghost",
    { detail: (plan.preview && plan.preview.timeline) || null }));
}

player.addEventListener("play", () => { if (stageEl.classList.contains("compare")) playerB.play().catch(() => {}); });
player.addEventListener("pause", () => { if (stageEl.classList.contains("compare")) playerB.pause(); });
player.addEventListener("seeked", () => { if (stageEl.classList.contains("compare")) playerB.currentTime = player.currentTime; });

function renderPipeline(c) {
  // The stage pipeline view (the cut/jumpcut/reframe/… node strip and the
  // "STAGES (N)" toggle) was internal pipeline jargon that confused users who
  // don't know the editing model — removed per request. Kept as a no-op that
  // hides both so any remaining callers stay safe.
  const pl = $("pipeline");
  if (pl) { pl.innerHTML = ""; pl.style.display = "none"; }
  const stt = $("stToggle");
  if (stt) stt.style.display = "none";
}

const RENDERING = {};   // clip id -> true while its lazy render job is in flight
let RENDER_JOB = null;  // {clipId, jobId} of the in-flight lazy render (preempt)

/* Preempt: clicking a DIFFERENT clip cancels the in-flight render so the new
   clip starts immediately instead of queueing behind a 1–2 min encode on the
   single worker. Without this, switching clips mid-render looks "stuck" — the
   progress bar keeps showing the old clip while the new one waits in line. */
function preemptRenderFor(clipId) {
  if (RENDER_JOB && RENDER_JOB.clipId !== clipId && RENDER_JOB.jobId) {
    fetch(`/api/jobs/${RENDER_JOB.jobId}/cancel`, { method: "POST" })
      .catch(() => {});
    if (RENDERING[RENDER_JOB.clipId]) delete RENDERING[RENDER_JOB.clipId];
    RENDER_JOB = null;
  }
}

/* Reload state and hot-swap a clip's fresh url into the player if the user is
   still on it. Preserves playback position across the swap (caption pop-in). */
async function swapIn(clipId, keepCompare, keepTime) {
  const d = await (await fetch("/api/state")).json();
  CLIPS = d.clips || CLIPS; COMPS = d.compilations || COMPS;
  renderLibrary();
  const fresh = CLIPS.find(x => x.id === clipId);
  if (!(fresh && fresh.url && activeClip === clipId)) return;
  const t = keepTime ? (player.currentTime || 0) : 0;
  playClip(fresh, keepCompare);
  if (keepTime && t > 0.2) {
    player.addEventListener("loadedmetadata", function once() {
      player.removeEventListener("loadedmetadata", once);
      try { player.currentTime = t; player.play().catch(() => {}); } catch (x) {}
    });
  }
}

/* PROGRESSIVE LAZY render: generate_clips records a clip's stage recipe but
   renders nothing. On first open we render in TWO passes:
   (1) cut→jumpcut→reframe → a fast captionless 9:16 PREVIEW the user can watch;
   (2) the subtitle tail in the background → captions hot-swap in (the head
   stages are on-disk cache hits, so pass 2 only encodes captions).
   This makes a clip playable in ~30–45s instead of waiting 1–2 min for the full
   stack. Comps always carry a url. */
async function renderThenPlay(c, keepCompare) {
  activeClip = c.id;
  showRenderingState(c);
  renderLibrary(); renderQueueChip();
  if (RENDERING[c.id]) return;                 // a job is already in flight
  preemptRenderFor(c.id);                       // cancel any other clip's render
  RENDERING[c.id] = true;
  let job;
  try {
    job = await runJob("/api/tool",
      { name: "render_clip", args: { clip_id: c.id, upto: "reframe" } },
      (jid) => { RENDER_JOB = { clipId: c.id, jobId: jid }; });
  } catch (e) {
    delete RENDERING[c.id];
    if (RENDER_JOB && RENDER_JOB.clipId === c.id) RENDER_JOB = null;
    if (activeClip !== c.id) return;            // user moved on — stay quiet
    empty.style.display = "";
    empty.innerHTML = `<b>RENDER FAILED</b>${(e && e.message) || ""}`;
    return;
  }
  delete RENDERING[c.id];
  if (RENDER_JOB && RENDER_JOB.clipId === c.id) RENDER_JOB = null;
  if (activeClip !== c.id) return;              // preempted — don't play stale
  // User pressed ✕ — the render was cancelled, so there's no playable output.
  // Show a clear stopped state instead of leaving the "building" overlay frozen.
  if (job && job.status === "cancelled") {
    empty.style.display = "";
    empty.innerHTML = `<b>CANCELLED</b>Clip #${c.id} render stopped — ` +
      `click the clip again to retry.`;
    $("nowmeta").textContent = "cancelled";
    return;
  }
  // Show the captionless preview. playClip() sees the clip is not yet 'complete'
  // and kicks off finishCaptions() itself, so captions follow automatically.
  await swapIn(c.id, keepCompare, false);
}

/* Background pass 2: render the deferred subtitle tail, then hot-swap the
   captioned url into the player if the user is still on this clip. Head stages
   are cache hits, so this only encodes the captions. Silently abandoned if the
   user switches away (preempt cancels the job). */
async function finishCaptions(clipId, keepCompare) {
  if (RENDERING[clipId]) return;
  RENDERING[clipId] = true;
  try {
    await runJob("/api/tool",
      { name: "render_clip", args: { clip_id: clipId } },
      (jid) => { RENDER_JOB = { clipId, jobId: jid }; });
  } catch (e) { /* preview already plays; captions just didn't land */ }
  delete RENDERING[clipId];
  if (RENDER_JOB && RENDER_JOB.clipId === clipId) RENDER_JOB = null;
  if (activeClip !== clipId) return;            // moved on — leave the preview
  await swapIn(clipId, keepCompare, true);      // keep playback position
}

function showRenderingState(c) {
  empty.style.display = "";
  // Phase 4 — live stage progress inside the phone (driven by showJobChip).
  empty.innerHTML = `<b>BUILDING PREVIEW…</b>Clip #${c.id} — a watchable cut ` +
    `appears first, captions follow.` +
    `<div class="ej-bar"><i id="ejFill"></i></div>` +
    `<span class="mono" id="ejLbl"></span>`;
  $("nowtitle").textContent = "#" + c.id + " — " + c.title;
  $("nowmeta").textContent = "rendering…";
}

/* Phase 4 — guided empty state: when there are no clips yet, the stage shows a
   single primary GENERATE action instead of a passive "no clip" message. */
function showGenerateCTA() {
  if (CLIPS.length) return;
  empty.style.display = "";
  empty.innerHTML = `<b>READY</b>Your video is loaded.` +
    `<button class="gen-cta" id="genCta">⚡ GENERATE CLIPS</button>`;
  const g = $("genCta");
  if (g) g.onclick = () =>
    sendMessage("extract the best moments from this video as short clips");
}

function playClip(c, keepCompare) {
  if (!c) return;
  if (!c.comp && !c.url) { renderThenPlay(c, keepCompare); return; }
  activeClip = c.comp ? activeClip : c.id;
  empty.style.display = "none";
  player.src = c.url + "?t=" + Date.now();
  player.play().catch(() => {});
  $("nowtitle").textContent = (c.comp ? "C" : "#") + c.id + " — " + c.title;
  $("nowmeta").textContent = c.comp ? "compilation" :
    `${c.start.toFixed(1)}–${c.end.toFixed(1)}s` +
    (c.style ? ` · ${c.style}` : "");
  const cc = $("ccExport");
  if (c.comp) {
    cc.style.display = "none";
  } else {
    cc.style.display = "";
    $("ccSrt").href = `/api/captions/${c.id}.srt`;
    $("ccVtt").href = `/api/captions/${c.id}.vtt`;
    $("nleXml").href = `/api/export/${c.id}.xml`;
    $("nleEdl").href = `/api/export/${c.id}.edl`;
    $("qcBtn").onclick = () => runQC(c.id);
    // Phase 5 — full-res deliverable. Reveal the download if already exported.
    const exBtn = $("exportBtn"), exDl = $("exportDl");
    exBtn.disabled = false; exBtn.textContent = "⤓ EXPORT MP4";
    exBtn.onclick = () => exportClip(c.id);
    const shBtn = $("shareBtn");
    if (shBtn) shBtn.onclick = () =>
      window.openShareModal && window.openShareModal(c.id, c);
    if (c.export_url) { exDl.href = c.export_url; exDl.style.display = ""; }
    else exDl.style.display = "none";
  }
  const grp = c.comp ? [] : variantGroup(c);
  const tog = $("cmpToggle");
  if (grp.length > 1) {
    tog.style.display = "";
    tog.onclick = () => stageEl.classList.contains("compare")
      ? exitCompare()
      : enterCompare(c, grp.find(x => x.id !== c.id));
  } else tog.style.display = "none";
  if (!keepCompare) exitCompare();
  renderPipeline(c);
  renderLibrary();
  renderQueueChip();   // Phase 4 — the focused card / queue position changed
  if (!c.comp) window.dispatchEvent(new CustomEvent("kesim:clip", { detail: c }));
  // Progressive open: re-opening a preview-only clip (captions never finished
  // because the user switched away mid-render) silently completes them now.
  if (!c.comp && c.url && c.complete === false && !RENDERING[c.id])
    finishCaptions(c.id, false);
}

function refreshActive() {
  const cur = CLIPS.find(c => c.id === activeClip) || CLIPS[0];
  if (cur) playClip(cur);
}

/* Phase 5 — render the FINAL full-res MP4 for a clip from the original source
   (interactive editing runs on the 540p proxy). Replays the clip's approved
   stage chain at native resolution; then reveals + starts the download. */
async function exportClip(id) {
  const exBtn = $("exportBtn"), exDl = $("exportDl");
  exBtn.disabled = true; exBtn.textContent = "EXPORTING…";
  try {
    await runJob("/api/tool", { name: "export_clip", args: { clip_id: id } });
  } catch (e) {
    exBtn.disabled = false; exBtn.textContent = "⤓ EXPORT MP4";
    addMsg("bot", "Export failed: " + ((e && e.message) || "unknown error"));
    return;
  }
  const d = await (await fetch("/api/state")).json();
  CLIPS = d.clips || CLIPS;
  const fresh = CLIPS.find(x => x.id === id);
  exBtn.disabled = false; exBtn.textContent = "✓ EXPORTED";
  if (fresh && fresh.export_url) {
    exDl.href = fresh.export_url; exDl.style.display = "";
    window.location.href = fresh.export_url;   // begin download
  }
  renderLibrary();
}

/* Pull fresh session state after an out-of-band edit (e.g. a transcript cut
   via /api/tool) and re-render everything. */
async function reloadState() {
  const d = await (await fetch("/api/state")).json();
  if (d.source) {
    TC_FPS = d.source.fps || 30;
    setProjName(d.source);
  }
  CLIPS = d.clips || []; COMPS = d.compilations || [];
  // Phase 4 — adopt the server's queue summary; if we don't yet have a local
  // focus, fall back to the server cursor (else CLIPS[0] via refreshActive).
  if (d.queue) QUEUE = d.queue;
  if (activeClip == null && d.active_clip_id != null) activeClip = d.active_clip_id;
  renderLibrary();
  try {
    renderHistory((await (await fetch("/api/history")).json()).history || []);
  } catch (e) {}
  refreshActive();
  if (!CLIPS.length) showGenerateCTA();   // Phase 4 — guided empty state
  renderQueueChip();
}
window.reloadState = reloadState;

/* ---------------------------------------------------------------- chat */
function addMsg(cls, text) {
  const d = document.createElement("div");
  d.className = "msg " + cls; d.textContent = text;
  chat.appendChild(d); chat.scrollTop = chat.scrollHeight;
  return d;
}

/* Per-message Revert / Regenerate on a just-applied plan (named checkpoint —
   pops to the exact pre-plan state, not LIFO). Additive: only shown when the
   chat payload carries `applied`. */
function renderAppliedActions(applied) {
  const cp = applied.checkpoint;
  const row = document.createElement("div");
  row.className = "clarify-chips";
  const mk = (label, fn) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "clarify-chip"; b.textContent = label;
    b.onclick = async () => {
      row.querySelectorAll("button").forEach(x => x.disabled = true);
      try { await fn(); } catch (e) { addMsg("bot", "Error: " + e.message); }
    };
    return b;
  };
  row.appendChild(mk("Geri al", async () => {
    const job = await runJob("/api/tool?sync=1",
      { name: "revert_plan", args: { checkpoint: cp } });
    const env = job.result || {};            // {ok, result, clips, history}
    const res = env.result || {};            // the tool's own _ok/_err dict
    if (res.error) { addMsg("bot", "Error: " + res.error); return; }
    CLIPS = env.clips || CLIPS; renderLibrary();
    renderHistory(env.history || []); refreshActive();
    addMsg("bot", res.msg || "Reverted.");
  }));
  row.appendChild(mk("Farklı dene", async () => {
    const job = await runJob("/api/tool?sync=1",
      { name: "regenerate_plan", args: { checkpoint: cp, revert: true } });
    const env = job.result || {};
    const res = env.result || {};
    if (res.error) { addMsg("bot", "Error: " + res.error); return; }
    CLIPS = env.clips || CLIPS; renderLibrary();
    renderHistory(env.history || []); refreshActive();
    if (res.msg) addMsg("bot", res.msg);
    if (res.plan) {
      renderPlanCard(res.plan);
      if (res.plan.preview) enterPlanCompare(res.plan);
    }
  }));
  chat.appendChild(row); chat.scrollTop = chat.scrollHeight;
}

/* QC card: measured loudness/true-peak/format checks → chat panel card. */
const QC_ICON = { ok: "✓", warn: "⚠", fail: "✗" };
async function runQC(clipId) {
  const btn = $("qcBtn");
  btn.disabled = true; btn.textContent = "…";
  try {
    const d = await (await fetch(`/api/qc/${clipId}`)).json();
    if (d.error) { addMsg("bot", "✗ QC: " + d.error); return; }
    const card = document.createElement("div");
    card.className = "qc-card qc-" + d.overall;
    card.innerHTML =
      `<div class="qc-head mono">QC — CLIP #${d.clip_id} · ${d.platform}` +
      ` <span class="qc-overall ${d.overall}">${QC_ICON[d.overall]}</span></div>` +
      d.checks.map(c =>
        `<div class="qc-row"><span class="qc-ic ${c.status}">${QC_ICON[c.status]}</span>` +
        `<span class="qc-lbl">${c.label}</span>` +
        `<span class="qc-det mono">${c.detail}</span></div>`).join("");
    chat.appendChild(card); chat.scrollTop = chat.scrollHeight;
  } catch (e) {
    addMsg("bot", "✗ QC measurement failed: " + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "QC";
  }
}

function renderPlanCard(plan) {
  const card = document.createElement("div");
  card.className = "plan-card";
  const steps = (plan.steps || []).map(s => {
    const args = Object.entries(s.args)
      .filter(([k]) => k !== "clip_id")
      .map(([k, v]) => `${k}=${typeof v === "string" ? v.split("/").pop() : v}`)
      .join(", ");
    return `<li><b>${s.action}</b> <span class="mono" style="font-size:.62rem">${args}</span><br>
      <span class="why">${s.why || ""}</span></li>`;
  }).join("");
  const gaps = (plan.gaps || []).map(g => `<div class="gap">⚠ missing: ${g}</div>`).join("");
  const prev = plan.preview
    ? `<div class="prevnote">▶ Result preview is in the player — watch side <b>B</b>,
       approve if you like it (applying is instant, already rendered).</div>` : "";
  card.innerHTML = `
    <div class="pt">Edit Plan — Clip #${plan.clip_id}</div>
    <div class="ps">${plan.summary || ""}</div>
    <ol>${steps}</ol>${gaps}${prev}
    <div class="acts"><button class="ok">APPLY</button>
      <button class="no">DISCARD</button></div>`;
  card.querySelector(".ok").onclick = () => { card.remove(); sendMessage("apply the plan"); };
  card.querySelector(".no").onclick = () => { card.remove(); sendMessage("cancel the plan"); };
  chat.appendChild(card); chat.scrollTop = chat.scrollHeight;
}

/* Clarifying-question chips: one-tap answers to the agent's ask_user. Tapping
   sends the option as the next message; free text in the box also works. */
function renderClarify(options) {
  const row = document.createElement("div");
  row.className = "clarify-chips";
  options.forEach(opt => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "clarify-chip"; b.textContent = opt;
    b.onclick = () => { row.querySelectorAll("button").forEach(x => x.disabled = true);
                        sendMessage(opt); };
    row.appendChild(b);
  });
  chat.appendChild(row); chat.scrollTop = chat.scrollHeight;
}

function sendMessage(text) {
  input.value = text;
  $("form").dispatchEvent(new Event("submit", { cancelable: true }));
}

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim(); if (!text || send.disabled) return;
  input.value = ""; addMsg("user", text);
  send.disabled = true; setBusy(true);
  const th = document.createElement("div");
  th.className = "thinking";
  th.innerHTML = `<div class="th-row"><span class="bar"></span>` +
    `<span class="th-friendly">working — rendering may take a while</span></div>`;
  chat.appendChild(th); chat.scrollTop = chat.scrollHeight;
  chatThinkingEl = th;
  try {
    // onStart records the job id so initEvents narrates THIS turn only (A1).
    const job = await runJob("/api/chat", { message: text, mode: MODE, tier: TIER },
                             (jid) => { chatJobId = jid; });
    const d = job.result || {};
    chatJobId = null; chatThinkingEl = null;
    th.remove();
    (d.tools || []).forEach(t => addMsg("tool", t));
    if (d.reply) addMsg("bot", d.reply);
    if (d.clarify && (d.clarify.options || []).length)
      renderClarify(d.clarify.options);
    if (d.pending_plan && (d.tools || []).some(t => t.startsWith("propose")))
      renderPlanCard(d.pending_plan);
    if (d.applied && d.applied.checkpoint) renderAppliedActions(d.applied);
    CLIPS = d.clips || CLIPS; COMPS = d.compilations || COMPS;
    renderLibrary(); renderHistory(d.history || []);
    refreshActive();
    if (d.pending_plan && d.pending_plan.preview)
      enterPlanCompare(d.pending_plan);
  } catch (err) {
    chatJobId = null; chatThinkingEl = null;
    th.remove(); addMsg("bot", "Error: " + err.message);
  }
  send.disabled = false; setBusy(false); input.focus();
});

/* ---------------------------------------------------------------- chips */
const HINTS_PRO = ["extract 3 clips from this video", "make it punchier",
  "clean up the umms", "give it a cinematic look", "master audio for tiktok"];
const HINTS_BASIT = ["clip the best moments", "make it shorter and punchier",
  "add captions", "add music"];

function renderChips() {
  const box = $("hintchips");
  box.innerHTML = "";
  (MODE === "basit" ? HINTS_BASIT : HINTS_PRO).forEach(h => {
    const c = document.createElement("button");
    c.className = "chip"; c.type = "button"; c.textContent = h;
    c.onclick = () => { input.value = h; input.focus(); };
    box.appendChild(c);
  });
}

/* ---------------------------------------------------------------- keyboard */
let IN_PT = null, OUT_PT = null;
function frameStep(dir, mult) {
  const fps = TC_FPS;
  const tf = Math.round(player.currentTime * fps) + dir * mult;
  // land just inside the target frame (not on the boundary, where FP rounding
  // could drop back a frame) — but NOT mid-frame, which would double-step.
  player.currentTime = Math.max(0, (Math.max(0, tf) + 0.05) / fps);
  player.pause();
}
document.addEventListener("keydown", (e) => {
  const el = document.activeElement;
  const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
  // allow undo/redo while typing too; everything else ignored when typing
  if ((e.key === "z" || e.key === "Z") && (e.metaKey || e.ctrlKey) && e.shiftKey) {
    e.preventDefault();      // Ctrl/Cmd+Shift+Z → redo
    runJob("/api/tool", { name: "redo", args: {} })
      .then(() => { reloadState(); window.tlReload && window.tlReload(); });
    return;
  }
  if ((e.key === "y" || e.key === "Y") && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();      // Ctrl+Y → redo
    runJob("/api/tool", { name: "redo", args: {} })
      .then(() => { reloadState(); window.tlReload && window.tlReload(); });
    return;
  }
  if ((e.key === "z" || e.key === "Z") && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    runJob("/api/tool", { name: "undo", args: {} })
      .then(() => { reloadState(); window.tlReload && window.tlReload(); });
    return;
  }
  if (typing) return;
  if (!player.src) return;
  const mult = e.shiftKey ? 10 : 1;
  switch (e.key) {
    case " ": case "k": case "K":
      e.preventDefault();
      player.paused ? player.play().catch(()=>{}) : player.pause();
      break;
    case "ArrowLeft":  e.preventDefault(); frameStep(-1, mult); break;
    case "ArrowRight": e.preventDefault(); frameStep(1, mult); break;
    case "j": case "J":
      e.preventDefault();
      player.playbackRate = 1; player.currentTime = Math.max(0, player.currentTime - 0.5);
      break;
    case "l": case "L":
      e.preventDefault();
      if (player.paused) player.play().catch(()=>{});
      else player.playbackRate = player.playbackRate >= 4 ? 1 : player.playbackRate * 2;
      break;
    case "i": case "I":
      IN_PT = player.currentTime;
      if (OUT_PT != null && OUT_PT <= IN_PT) OUT_PT = null;
      window.tlSetInOut && window.tlSetInOut(IN_PT, OUT_PT);
      setBusy(false, `IN ${fmtTC(IN_PT)}`);
      break;
    case "o": case "O":
      OUT_PT = player.currentTime;
      if (IN_PT != null && OUT_PT <= IN_PT) IN_PT = null;
      window.tlSetInOut && window.tlSetInOut(IN_PT, OUT_PT);
      setBusy(false, `OUT ${fmtTC(OUT_PT)}`);
      break;
    case "m": case "M":
      runJob("/api/tool", { name: "add_marker",
        args: { clip_id: activeClip, t: player.currentTime } })
        .then(() => window.tlReload && window.tlReload());
      break;
    case "g": case "G": e.preventDefault(); gotoTimecode(); break;
    case "s": case "S":
      e.preventDefault(); window.tlSplit && window.tlSplit(); break;
    case "b": case "B":
      if (e.ctrlKey || e.metaKey) { e.preventDefault();
        window.tlSplit && window.tlSplit(); }
      break;
    case "Delete": case "Backspace":
      e.preventDefault();
      (window.tlDelete || window.tlRippleDelete || (()=>{}))(); break;
    case "n": case "N":
      e.preventDefault(); window.tlToggleSnap && window.tlToggleSnap(); break;
    case "z": case "Z":
      if (e.shiftKey) { e.preventDefault(); window.tlFit && window.tlFit(); }
      break;
    case "+": case "=":
      e.preventDefault(); window.tlZoom && window.tlZoom(1); break;
    case "-": case "_":
      e.preventDefault(); window.tlZoom && window.tlZoom(-1); break;
    case "Escape":
      IN_PT = OUT_PT = null;
      window.tlSetInOut && window.tlSetInOut(null, null);
      break;
  }
});
window.getInOut = () => ({ inPt: IN_PT, outPt: OUT_PT });
window.clearInOut = () => { IN_PT = OUT_PT = null; window.tlSetInOut && window.tlSetInOut(null, null); };

/* ---------------------------------------------------------------- user */
function renderUserChip(me) {
  const chip = $("userchip");
  if (!chip || !me) return;
  chip.innerHTML = "";
  const nm = document.createElement("span");
  nm.textContent = me.name || me.email || "";
  const out = document.createElement("button");
  out.type = "button"; out.textContent = "SIGN OUT";
  out.onclick = async () => {
    try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) {}
    location.href = "/";
  };
  chip.appendChild(nm); chip.appendChild(out);
}

/* ---------------------------------------------------------------- boot */
async function boot() {
  try {
    const meR = await fetch("/api/me");
    if (meR.status === 401) { location.href = "/login?next=/studio"; return; }
    const me = await meR.json();
    if (!localStorage.getItem("kesim_mode")) MODE = me.default_mode || "basit";
    renderUserChip(me);
    // personalize the static greeting bubble with the user's first name
    const first = document.querySelector(".msg.bot");
    if (first && me.name) first.textContent = `Hey ${me.name.split(" ")[0]}! ` + first.textContent.replace(/^Hey! /, "");
  } catch (e) {}
  const d = await (await fetch("/api/state")).json();
  // (B) /projects switcher: no active project -> bounce to the picker.
  if (d.error === "no_active_project") { location.href = "/projects"; return; }
  TC_FPS = d.source.fps || 30;
  setProjName(d.source);
  // Keep each project's id in the URL (shareable + visible) and in the tab
  // title. The server already redirects bare /studio to ?project=<id>; this is
  // the client-side guard so the id never drops off the URL.
  if (d.name) {
    const want = "/studio?project=" + encodeURIComponent(d.name);
    if (location.pathname + location.search !== want)
      history.replaceState(null, "", want);
    document.title = d.name + " · VibeClip studio";
  }
  CLIPS = d.clips || []; COMPS = d.compilations || [];
  if (d.queue) QUEUE = d.queue;        // Phase 4 — initial batch progress
  renderLibrary();
  try {
    renderHistory((await (await fetch("/api/history")).json()).history || []);
  } catch (e) {}
  applyMode(MODE);
  loadAssets(); loadSounds(); initEvents();
  // Phase 4 — open on the server's focus cursor (top-ranked pending by default),
  // not blindly the first clip.
  if (CLIPS.length) {
    const focus = CLIPS.find(c => c.id === d.active_clip_id) || CLIPS[0];
    playClip(focus);
  } else {
    showGenerateCTA();
  }
  renderQueueChip();
  if (d.pending_plan) {
    renderPlanCard(d.pending_plan);
    if (d.pending_plan.preview) enterPlanCompare(d.pending_plan);
  }
}

boot();
