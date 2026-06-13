/* VibeClip Studio — /projects switcher (B design: one global SESSION).
   Lists projects with a derived pipeline status, lets the user open one in the
   studio, start/retry processing, rename, delete, or create a new one (long
   video -> auto-clip, or own finished clips). Live status via one EventSource;
   10s poll fallback. Vanilla JS, no build step. */

const $ = (id) => document.getElementById(id);
let PROJECTS = [];
let ME = null;            // /api/me payload — drives the greeting
let BUSY = false;
let UPLOADING = null;   // optimistic client-side card while a long-video upload streams
let LAST_PROGRESS = null; // {pct, message} cache so a processing card seeds its bar without a reset-jump
let FILTER = "all";       // client-only status filter (ALL/PROCESSING/READY/DONE)
let LIMITS = { max_seconds: 600, max_projects: 1 };  // per-account quota (from API)
let AT_QUOTA = false;     // user is at their project cap → block new uploads
let SEARCH = "";          // free-text name filter
let SORT = "recent";      // recent | score | clips | duration | name
let VIEW = "grid";        // grid | list
let FOLDER = null;        // active folder filter (null = all)
let SELMODE = false;      // bulk-selection mode
const SELECTED = new Set();// names of selected projects
try {
  SORT = localStorage.getItem("vc_proj_sort") || SORT;
  VIEW = localStorage.getItem("vc_proj_view") || VIEW;
} catch (e) {}

/* Does a project match the active filter pill? */
function matchesFilter(p) {
  switch (FILTER) {
    case "processing": return p.status === "processing" || p.status === "needs_processing" || p.status === "error";
    case "ready":      return p.status === "clips_ready" || p.status === "editing";
    case "done":       return p.status === "done";
    default:           return true;
  }
}

/* Does a project match the active folder + search box? */
function matchesFolder(p) {
  if (FOLDER === null) return true;
  if (FOLDER === "__none__") return !(p.folder);
  return p.folder === FOLDER;
}
function matchesSearch(p) {
  if (!SEARCH) return true;
  const hay = `${p.display_name || ""} ${p.name || ""} ${p.folder || ""}`.toLowerCase();
  return hay.includes(SEARCH);
}

/* Sort key per the active SORT mode (descending for numeric, asc for name). */
const SORTERS = {
  recent:   (a, b) => Date.parse(b.modified || 0) - Date.parse(a.modified || 0),
  score:    (a, b) => (b.best_score || 0) - (a.best_score || 0),
  clips:    (a, b) => ((b.clips && b.clips.total) || 0) - ((a.clips && a.clips.total) || 0),
  duration: (a, b) => (b.duration || 0) - (a.duration || 0),
  name:     (a, b) => String(a.display_name || a.name).localeCompare(String(b.display_name || b.name)),
};

/* The fully filtered + sorted project list for the current view. */
function visibleProjects() {
  const list = PROJECTS.filter((p) =>
    matchesFilter(p) && matchesFolder(p) && matchesSearch(p));
  const sorter = SORTERS[SORT] || SORTERS.recent;
  return list.slice().sort((a, b) => {
    if (a.active !== b.active) return a.active ? -1 : 1;  // active always first
    return sorter(a, b);
  });
}

/* score 0-100 -> color tier for the thumbnail badge. */
function scoreClass(n) { return n >= 75 ? "hi" : n >= 60 ? "mid" : "lo"; }

/* Distinct folder names across all projects (for the chip row). */
function allFolders() {
  const s = new Set();
  PROJECTS.forEach((p) => { if (p.folder) s.add(p.folder); });
  return [...s].sort((a, b) => a.localeCompare(b));
}

/* ----------------------------------------------------------------- helpers */
function ago(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}
function fmtDur(sec) {
  if (!sec) return "";
  const m = Math.round(sec / 60);
  return m >= 1 ? `${m} min` : `${Math.round(sec)}s`;
}
function modeLabel(m) { return m === "own_clips" ? "own clips" : "long video"; }

let toastTimer = null;
function toast(msg, kind) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("t-err", "t-warn", "t-ok");
  if (kind === "err") t.classList.add("t-err");
  else if (kind === "warn") t.classList.add("t-warn");
  else if (kind === "ok") t.classList.add("t-ok");
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), 3200);
}

/* Friendly status line from a job.message ("stage|args" or bare stage). */
const STAGE_TEXT = {
  "build proxy": "building preview proxy…",
  "auto-clip": "finding clip candidates…",
  "prepare clips": "preparing your clips…",
  generate_clips: "finding clip candidates…",
  cut: "cutting…", reframe: "reframing to vertical…",
  subtitles: "styling captions…", jumpcut: "tightening pauses…",
};
function jobText(msg) {
  if (!msg) return "working…";
  const key = msg.indexOf("|") < 0 ? msg : msg.slice(0, msg.indexOf("|"));
  return STAGE_TEXT[key] || key;
}


/* status -> overlaid thumbnail badge {word, cls}. */
const BADGE = {
  uploading:       { word: "Uploading",       cls: "st-uploading" },
  needs_processing:{ word: "Needs processing",cls: "st-warn" },
  processing:      { word: "Processing",       cls: "st-processing" },
  error:           { word: "Failed",           cls: "st-err" },
  clips_ready:     { word: "Ready",            cls: "st-ready" },
  editing:         { word: "In edit",          cls: "st-edit" },
  done:            { word: "Exported",          cls: "st-done" },
};

/* The single readable status line on the card (e.g. "1 clip found — ready to edit"). */
function stageLabel(p) {
  const cl = p.clips || {};
  switch (p.status) {
    case "uploading":        return `uploading ${(UPLOADING && UPLOADING.pct) || 0}%`;
    case "needs_processing": return "waiting to process";
    case "processing":       return (LAST_PROGRESS ? jobText(LAST_PROGRESS.message) : "processing…");
    case "error":            return p.error ? truncate(p.error, 60) : "processing failed";
    case "clips_ready":      return `${cl.total || 0} clip${cl.total === 1 ? "" : "s"} found — ready to edit`;
    case "editing":          return `${cl.approved || 0}/${cl.total || 0} approved`;
    case "done":             return "all clips exported";
    default:                 return "";
  }
}
function truncate(s, n) {
  s = String(s || "");
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}


/* Inline-SVG glyphs (no emoji). 9:16 outlined frame + play triangle for video;
   scissors for own clips. Returned as markup strings. */
const SVG_VIDEO = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 9.5l4 2.5-4 2.5z" fill="currentColor" stroke="none"/></svg>`;
const SVG_SCISSORS = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><path d="M8 7.5L20 18M8 16.5L20 6"/></svg>`;
function glyphFor(mode) { return mode === "own_clips" ? SVG_SCISSORS : SVG_VIDEO; }

/* ----------------------------------------------------------------- render */
function render() {
  const grid = $("grid");
  grid.innerHTML = "";
  const cards = [];
  // optimistic uploading card only in the unfiltered "all" context
  if (UPLOADING && (FILTER === "all" || FILTER === "processing")
      && FOLDER === null && !SEARCH) cards.push(uploadingCard());
  const vis = visibleProjects();
  vis.forEach((p) => cards.push(projectCard(p)));
  cards.forEach((c, i) => { c.style.setProperty("--i", Math.min(i, 8)); grid.appendChild(c); });
  grid.classList.toggle("list", VIEW === "list");

  // total count is across ALL projects (not the filtered view)
  const total = PROJECTS.length + (UPLOADING ? 1 : 0);
  $("count").textContent = total ? `${total} project${total === 1 ? "" : "s"}` : "";
  // empty hero only when there are genuinely zero projects; the toolbar appears
  // once there are any projects to filter.
  const has = total > 0;
  $("empty").style.display = has ? "none" : "";
  $("toolbar").classList.toggle("on", has);
  $("selectBtn").style.display = has ? "" : "none";
  $("grid").style.display = has ? "" : "none";  // grid|list applied via class
  // greeting + at-a-glance stats + persistent upload band live above the grid,
  // only once there are projects (the empty hero owns the zero-state).
  $("welcome").style.display = has ? "" : "none";
  $("ingest").classList.toggle("on", has);
  if (has) { updateWelcome(); renderFolderChips(); renderRecents(); syncControls(); }
  else { $("recents").classList.remove("on"); }
  updateIngest();
  updateSelbar();
  // when a filter/search yields nothing but projects exist, show a small note
  if (total > 0 && cards.length === 0) {
    const note = document.createElement("div");
    note.className = "cmeta mono";
    note.style.cssText = "grid-column:1/-1; padding:30px 4px; color:var(--bone-faint)";
    note.textContent = SEARCH ? `No projects match “${SEARCH}”.`
      : "No projects match this filter.";
    grid.appendChild(note);
  }
}

/* Reflect persisted SORT/VIEW + active FILTER pill into the toolbar controls. */
function syncControls() {
  const s = $("sort"); if (s && s.value !== SORT) s.value = SORT;
  document.querySelectorAll("#viewtoggle button").forEach((b) =>
    b.classList.toggle("on", b.dataset.view === VIEW));
  document.querySelectorAll("#filters .pill").forEach((p) =>
    p.classList.toggle("on", p.dataset.filter === FILTER));
}

/* Folder chip row: "All folders" + one chip per distinct folder + "No folder". */
function renderFolderChips() {
  const wrap = $("folderChips");
  if (!wrap) return;
  const folders = allFolders();
  if (!folders.length) { wrap.innerHTML = ""; return; }
  const chip = (val, label) =>
    `<button class="pill ${FOLDER === val ? "on" : ""}" data-folder="${escapeHtml(val === null ? "" : val)}">${escapeHtml(label)}</button>`;
  let html = chip(null, "ALL FOLDERS");
  folders.forEach((f) => { html += chip(f, "🗂 " + f); });
  wrap.innerHTML = html;
  wrap.querySelectorAll("[data-folder]").forEach((b) => {
    b.onclick = () => {
      const v = b.dataset.folder;
      FOLDER = v === "" ? null : v;
      render();
    };
  });
}

/* Recents strip: up to 5 most-recently-edited openable projects. Hidden unless
   there are more than 4 projects (otherwise it just duplicates the grid). */
function renderRecents() {
  const rec = $("recents"), strip = $("recStrip");
  if (!rec || !strip) return;
  const openable = PROJECTS
    .filter((p) => ["clips_ready", "editing", "done"].includes(p.status))
    .sort(SORTERS.recent).slice(0, 5);
  if (PROJECTS.length <= 4 || openable.length < 2) {
    rec.classList.remove("on"); return;
  }
  rec.classList.add("on");
  strip.innerHTML = "";
  openable.forEach((p) => {
    const c = document.createElement("div");
    c.className = "rec-card";
    const sub = [];
    if (p.clips && p.clips.total) sub.push(`${p.clips.total} clip${p.clips.total === 1 ? "" : "s"}`);
    if (p.modified) sub.push(ago(p.modified));
    c.innerHTML = `
      <div class="rec-thumb">
        <img loading="lazy" alt="" src="${p.thumb}" onerror="this.replaceWith(filmGlyph('${p.mode}'))">
      </div>
      <div class="rec-meta">
        <div class="rec-nm">${escapeHtml(p.display_name || p.name)}</div>
        <div class="rec-sub mono">${escapeHtml(sub.join(" · "))}</div>
      </div>`;
    c.onclick = () => openProject(p.name);
    strip.appendChild(c);
  });
}

/* Reflect the per-account quota in the persistent upload band. */
function updateIngest() {
  const main = document.querySelector(".ingest-main");
  const sub = document.querySelector(".ingest-sub");
  const drop = $("ingestDrop");
  if (!main || !sub || !drop) return;
  const mins = Math.round((LIMITS.max_seconds || 0) / 60);
  const cap = LIMITS.max_projects || 0;
  if (AT_QUOTA) {
    drop.classList.add("quota");
    main.textContent = `You've used your ${cap} project${cap === 1 ? "" : "s"}`;
    sub.textContent = "Delete it below to upload a new video";
  } else {
    drop.classList.remove("quota");
    main.textContent = "Drop a long video here — we'll find the clips";
    const bits = [];
    if (mins) bits.push(`max ${mins} min`);
    bits.push("MP4, MOV, WEBM, MKV");
    if (cap) bits.push(`${cap} video${cap === 1 ? "" : "s"} / account`);
    sub.textContent = "or click to choose · " + bits.join(" · ");
  }
}

/* Greeting name + at-a-glance stat chips, derived from the loaded projects. */
function updateWelcome() {
  if (ME) $("welcomeName").textContent = ME.name ? `, ${ME.name}` : "";
  const ps = PROJECTS;
  const clipsTotal = ps.reduce((s, p) => s + ((p.clips && p.clips.total) || 0), 0);
  const review = ps.filter((p) => p.status === "clips_ready" || p.status === "editing").length;
  const mins = Math.round(ps.reduce((s, p) => s + (p.duration || 0), 0) / 60);
  const chip = (cls, n, label) =>
    `<span class="wstat ${cls}"><span class="dot"></span><b>${n}</b> ${label}</span>`;
  const chips = [chip("", ps.length, `project${ps.length === 1 ? "" : "s"}`)];
  if (clipsTotal) chips.push(chip("", clipsTotal, `clip${clipsTotal === 1 ? "" : "s"}`));
  if (review) chips.push(chip("is-review", review, "ready to review"));
  if (mins) chips.push(chip("", mins, "min of footage"));
  $("welcomeStats").innerHTML = chips.join("");
}

function uploadingCard() {
  const el = document.createElement("div");
  el.className = "card";
  el.dataset.status = "uploading";
  const pct = UPLOADING.pct || 0;
  el.innerHTML = `
    <div class="thumb">
      <span class="tbadge st-uploading mono">Uploading</span>
      <span class="glyph">${SVG_VIDEO}</span>
    </div>
    <div class="cbody">
      <div class="crow"><span class="cname">${escapeHtml(UPLOADING.name)}</span></div>
      <div class="cmeta mono">long video · uploading</div>
      <div class="seglabel">uploading ${pct}%</div>
      <div class="progress on"><i style="width:${pct}%"></i></div>
      <div class="cfoot">
        <button class="act ghost" data-act="cancel-upload">CANCEL UPLOAD</button>
      </div>
    </div>`;
  el.querySelector('[data-act="cancel-upload"]').addEventListener("click", (e) => {
    e.stopPropagation();
    if (UPLOADING && UPLOADING.xhr) UPLOADING.xhr.abort();
  });
  return el;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function projectCard(p) {
  const el = document.createElement("div");
  el.className = "card" + (p.active ? " active" : "");
  el.dataset.name = p.name;
  el.dataset.status = p.status;
  el.dataset.procJob = p.processing_job || "";

  if (SELECTED.has(p.name)) el.classList.add("selected");

  const cl = p.clips || {};
  const metaBits = [];
  metaBits.push(modeLabel(p.mode));
  if (cl.total) metaBits.push(`${cl.total} clip${cl.total === 1 ? "" : "s"}`);
  if (p.modified) metaBits.push(ago(p.modified));
  if (p.folder) metaBits.push("🗂 " + p.folder);
  const meta = metaBits.join(" · ");

  const processing = p.status === "processing";
  const openable = ["clips_ready", "editing", "done"].includes(p.status);
  if (openable) el.classList.add("openable");
  // Seed a processing card's bar from the cached live progress (no reset-jump).
  const seedPct = (processing && LAST_PROGRESS) ? (LAST_PROGRESS.pct || 0) : 0;

  const badge = BADGE[p.status] || { word: p.status, cls: "" };

  // footer action(s)
  let foot = "";
  if (p.status === "done") {
    foot = `<button class="act ghost" data-act="open">OPEN STUDIO</button>`;
  } else if (["clips_ready", "editing"].includes(p.status)) {
    foot = `<button class="act" data-act="open">OPEN STUDIO</button>`;
  } else if (p.status === "needs_processing") {
    foot = `<button class="act" data-act="process">START PROCESSING</button>`;
  } else if (p.status === "error") {
    foot = `<button class="act" data-act="process">RETRY</button>`;
  } else if (processing) {
    foot = `<button class="act ghost" disabled>PROCESSING… ${seedPct}%</button>`;
  } else {
    foot = `<button class="act ghost" disabled>…</button>`;
  }

  const dur = p.duration ? fmtDur(p.duration) : "";
  const score = p.best_score || 0;
  const showScore = score > 0 && ["clips_ready", "editing", "done"].includes(p.status);
  const check = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg>`;

  el.innerHTML = `
    <div class="thumb">
      <span class="selbox" data-act="select" aria-label="Select project">${check}</span>
      <span class="tbadge ${badge.cls} mono">${escapeHtml(badge.word)}</span>
      ${showScore ? `<span class="tscore ${scoreClass(score)} mono" title="Best clip score (hook · flow · value)">${score}<span class="lbl">SCORE</span></span>` : ""}
      <img loading="lazy" alt="${escapeHtml(p.display_name || p.name)}"
       src="${p.thumb}" onerror="this.replaceWith(filmGlyph('${p.mode}'))">
      ${dur ? `<span class="tdur mono">${escapeHtml(dur)}</span>` : ""}
      ${p.source === "youtube" ? `<span class="tauto" title="Auto-ingested from your YouTube channel"><svg viewBox="0 0 24 24" fill="none"><rect x="1" y="4" width="22" height="16" rx="5" fill="currentColor"/><path d="M10 8.3l6 3.7-6 3.7z" fill="#fff"/></svg>AUTO</span>` : ""}
    </div>
    <div class="cbody">
      <div class="crow">
        <span class="cname">${escapeHtml(p.display_name || p.name)}</span>
        ${p.active ? `<span class="badge-active mono">ACTIVE</span>` : ""}
      </div>
      <div class="cmeta mono">${escapeHtml(meta)}</div>
      <div class="seglabel ${p.status === "error" ? "errline" : ""}"
           ${p.status === "error" && p.error ? `title="${escapeHtml(p.error)}"` : ""}>${escapeHtml(stageLabel(p))}</div>
      <div class="progress ${processing ? "on" : ""}"><i style="width:${seedPct}%"></i></div>
      <div class="cfoot">
        ${foot}
        <div class="more">
          <button class="ovf" data-act="menu" aria-haspopup="true" aria-label="Project actions">⋯</button>
          <div class="menu" role="menu">
            ${openable ? `<button data-act="open">Open in studio</button>` : ""}
            <button data-act="rename">Rename</button>
            <button data-act="folder">Move to folder…</button>
            <button class="danger" data-act="delete">Delete</button>
          </div>
        </div>
      </div>
    </div>`;

  el.addEventListener("click", (e) => onCardClick(e, p, el));
  return el;
}

window.filmGlyph = function (mode) {
  const d = document.createElement("span");
  d.className = "glyph"; d.innerHTML = glyphFor(mode);
  return d;
};

/* ----------------------------------------------------------------- actions */
async function onCardClick(e, p, el) {
  const btn = e.target.closest("[data-act]");
  // selection mode: a click anywhere (except the ⋯ menu) toggles selection.
  if (SELMODE && (!btn || btn.dataset.act === "select")) {
    e.stopPropagation();
    return toggleSelect(p.name, el);
  }
  if (!btn) {
    // Whole-card click (not on an action) opens openable projects.
    if (["clips_ready", "editing", "done"].includes(p.status)) {
      if (e.target.closest(".rename-in")) return;  // don't hijack a rename input
      return openProject(p.name);
    }
    return;
  }
  const act = btn.dataset.act;
  e.stopPropagation();

  if (act === "select") return toggleSelect(p.name, el);
  if (act === "menu") {
    const m = btn.parentElement.querySelector(".menu");
    document.querySelectorAll(".menu.on").forEach((x) => { if (x !== m) x.classList.remove("on"); });
    m.classList.toggle("on");
    return;
  }
  if (act === "open") return openProject(p.name);
  if (act === "process") return processProject(p.name, btn);
  if (act === "rename") return startRename(p, el);
  if (act === "folder") return moveToFolder([p.name]);
  if (act === "delete") return confirmDelete(p, el);
}

/* ---- bulk selection ---- */
function setSelMode(on) {
  SELMODE = on;
  document.body.classList.toggle("selmode", on);
  if (!on) SELECTED.clear();
  const btn = $("selectBtn");
  if (btn) { btn.classList.toggle("on", on); btn.textContent = on ? "DONE" : "SELECT"; }
  render();
}
function toggleSelect(name, el) {
  if (SELECTED.has(name)) { SELECTED.delete(name); el && el.classList.remove("selected"); }
  else { SELECTED.add(name); el && el.classList.add("selected"); }
  if (typeof disarmDelete === "function") disarmDelete();  // count changed
  updateSelbar();
}
function updateSelbar() {
  const bar = $("selbar");
  if (!bar) return;
  const n = SELECTED.size;
  bar.classList.toggle("on", SELMODE && n > 0);
  const c = $("selCount"); if (c) c.textContent = n;
}
/* Two-click inline confirm on the Delete button (no blocking native dialog). */
let delArmed = false, delTimer = null;
function disarmDelete() {
  delArmed = false; clearTimeout(delTimer);
  const btn = $("selDelete");
  if (btn) { btn.textContent = "Delete"; btn.classList.remove("armed"); }
}
async function bulkDelete() {
  const names = [...SELECTED];
  if (!names.length) return;
  const btn = $("selDelete");
  if (!delArmed) {
    delArmed = true;
    if (btn) { btn.textContent = `Confirm · delete ${names.length}`; btn.classList.add("armed"); }
    clearTimeout(delTimer);
    delTimer = setTimeout(disarmDelete, 3500);
    return;
  }
  disarmDelete();
  let ok = 0, fail = 0;
  for (const name of names) {
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(name)}/delete`, { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (r.status === 409) { busyToast(); break; }
      d.ok ? ok++ : fail++;
    } catch (e) { fail++; }
  }
  toast(`Deleted ${ok}${fail ? `, ${fail} failed` : ""}.`, fail ? "warn" : "ok");
  setSelMode(false);
  await load();
}

/* Move-to-folder via a small inline modal (no blocking prompt). */
let FOLDER_TARGETS = [];
function moveToFolder(names) {
  if (!names.length) { toast("Select projects first."); return; }
  FOLDER_TARGETS = names.slice();
  const sub = $("folderSub");
  if (sub) sub.textContent = names.length === 1
    ? `“${names[0]}”` : `${names.length} projects`;
  const inp = $("folderInput");
  if (inp) inp.value = "";
  // existing folders as one-tap suggestions
  const sug = $("folderSuggest");
  if (sug) {
    sug.innerHTML = "";
    allFolders().forEach((f) => {
      const b = document.createElement("button");
      b.className = "pill"; b.textContent = f;
      b.onclick = () => { if (inp) inp.value = f; };
      sug.appendChild(b);
    });
  }
  $("folderScrim").classList.add("on");
  if (inp) setTimeout(() => inp.focus(), 30);
}
async function commitFolder() {
  const val = ($("folderInput").value || "").trim();
  $("folderScrim").classList.remove("on");
  let fail = 0;
  for (const name of FOLDER_TARGETS) {
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(name)}/folder`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder: val }),
      });
      const d = await r.json().catch(() => ({}));
      if (!d.ok) fail++;
    } catch (e) { fail++; }
  }
  toast(val ? `Moved to “${val}”${fail ? `, ${fail} failed` : ""}.`
    : "Removed from folder.", fail ? "warn" : "ok");
  if (SELMODE) setSelMode(false);  // exits select mode + re-renders
  await load();                    // refetch so the folder chip/meta updates
}

document.addEventListener("click", () =>
  document.querySelectorAll(".menu.on").forEach((m) => m.classList.remove("on")));

async function openProject(name) {
  try {
    const r = await fetch("/api/projects/open", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (r.status === 401) { location.href = "/login?next=/projects"; return; }
    const d = await r.json();
    if (r.status === 409) { busyToast(); return; }
    if (!d.ok) { toast(d.error || "Could not open project."); return; }
    location.href = d.next || "/studio";
  } catch (e) { toast("Network error."); }
}

async function processProject(name, btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`/api/projects/${encodeURIComponent(name)}/process`, {
      method: "POST", headers: { "Content-Type": "application/json" },
    });
    if (r.status === 401) { location.href = "/login?next=/projects"; return; }
    const d = await r.json();
    if (r.status === 409) { busyToast(); if (btn) btn.disabled = false; return; }
    if (!d.ok) { toast(d.error || "Could not start processing."); if (btn) btn.disabled = false; return; }
    await load();
  } catch (e) { toast("Network error."); if (btn) btn.disabled = false; }
}

function startRename(p, el) {
  const nameEl = el.querySelector(".cname");
  const cur = p.display_name || p.name;
  const input = document.createElement("input");
  input.className = "rename-in"; input.value = cur;
  nameEl.replaceWith(input); input.focus(); input.select();
  // Escape and blur can both fire (Escape removes the input → triggers blur),
  // so guard against a double revert/commit — replaceWith() throws once the
  // input is already detached.
  let done = false;
  const revert = () => { if (done) return; done = true; input.replaceWith(nameEl); };
  const commit = async () => {
    if (done) return;
    const v = input.value.trim();
    if (!v || v === cur) { revert(); return; }
    done = true;
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(p.name)}/rename`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: v }),
      });
      const d = await r.json();
      if (!d.ok) { toast(d.error || "Rename failed."); input.replaceWith(nameEl); return; }
    } catch (e) { toast("Network error."); }
    await load();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") commit();
    if (ev.key === "Escape") revert();
  });
  input.addEventListener("blur", commit);
}

function confirmDelete(p, el) {
  const foot = el.querySelector(".cfoot");
  foot.innerHTML = `
    <span class="errline" style="flex:1">Delete this project?</span>
    <button class="act ghost" data-x="no">No</button>
    <button class="act" style="background:var(--red);color:#fff" data-x="yes">DELETE</button>`;
  foot.querySelector('[data-x="no"]').onclick = (e) => { e.stopPropagation(); load(); };
  foot.querySelector('[data-x="yes"]').onclick = async (e) => {
    e.stopPropagation();
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(p.name)}/delete`, { method: "POST" });
      const d = await r.json();
      if (r.status === 409) { busyToast(); return; }
      if (!d.ok) { toast(d.error || "Delete failed."); return; }
    } catch (e2) { toast("Network error."); }
    await load();
  };
}

function busyToast() {
  toast("Another project is rendering — try again when it finishes");
}

/* Disable mutating buttons while the single worker is busy. The "+ New project"
   button stays enabled (the modal shows an inline busy note instead). */
function applyBusy() {
  document.querySelectorAll('.act[data-act="process"]').forEach((b) => {
    b.disabled = BUSY;
    if (BUSY) b.title = "A project is processing"; else b.removeAttribute("title");
  });
  // If the modal is open, reflect busy state in its note + create button.
  const note = $("busyNote");
  if (note && $("scrim").classList.contains("on")) {
    note.style.display = BUSY ? "block" : "none";
    if (BUSY) $("mGo").disabled = true; else refreshGo();
  }
}

/* ----------------------------------------------------------------- load */
async function load() {
  try {
    const r = await fetch("/api/projects");
    if (r.status === 401) { location.href = "/login?next=/projects"; return; }
    const d = await r.json();
    PROJECTS = (d.projects || []).map((p) => ({
      ...p, processing_job: null,
    }));
    BUSY = !!d.busy;
    LIMITS = d.limits || LIMITS;
    AT_QUOTA = !!d.at_quota;
    // Render guard: don't wipe an interaction the user is mid-way through.
    // A 10s poll or SSE job_done must not nuke an open menu, an in-progress
    // rename, or a delete confirmation. Update state + busy chrome, skip render.
    const interacting =
      document.querySelector(".menu.on") ||
      (document.activeElement && document.activeElement.classList &&
        document.activeElement.classList.contains("rename-in")) ||
      document.querySelector("[data-x]");
    if (interacting) { applyBusy(); return; }
    render();
    applyBusy();
  } catch (e) { /* keep last render */ }
}

/* --------------------------------------------------------------- new modal */
let MODE = null;
let longFile = null;
let ownFiles = [];

function openModal() {
  // Do NOT early-return when busy: let the user prepare a project. We show an
  // inline #busyNote and keep #mGo disabled until the worker is free.
  MODE = null; longFile = null; ownFiles = [];
  $("fnLong").textContent = ""; $("fnOwn").textContent = "";
  $("ownName").value = ""; $("upbar").classList.remove("on");
  $("upfill").style.width = "0";
  $("uplabel").style.display = "none";
  $("tileLong").classList.remove("on"); $("tileOwn").classList.remove("on");
  $("panelLong").classList.remove("on"); $("ownFields").classList.remove("on");
  if ($("longName")) $("longName").value = "";
  $("mGo").disabled = true; $("mGo").textContent = "Create";
  if ($("busyNote")) $("busyNote").style.display = BUSY ? "block" : "none";
  $("scrim").classList.add("on");
}
function closeModal() { $("scrim").classList.remove("on"); }

/* Cancel button / scrim-click handler. If a long-video upload is in flight,
   abort it (which resets state + reloads) instead of just hiding the modal. */
function dismissModal() {
  if (UPLOADING && UPLOADING.xhr) { UPLOADING.xhr.abort(); closeModal(); return; }
  closeModal();
}

function pickMode(mode) {
  MODE = mode;
  $("tileLong").classList.toggle("on", mode === "long_video");
  $("tileOwn").classList.toggle("on", mode === "own_clips");
  $("panelLong").classList.toggle("on", mode === "long_video");
  $("ownFields").classList.toggle("on", mode === "own_clips");
  $("mGo").textContent = mode === "long_video" ? "UPLOAD & FIND CLIPS"
    : mode === "own_clips" ? "CREATE PROJECT" : "Create";
  refreshGo();
}

/* "12 MB" / "1.4 GB" for a byte count. */
function fmtSize(bytes) {
  const mb = bytes / 1048576;
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`;
}

function refreshGo() {
  let ok = false;
  if (MODE === "long_video") ok = !!longFile;
  else if (MODE === "own_clips") ok = ownFiles.length > 0 && $("ownName").value.trim();
  $("mGo").disabled = !ok || BUSY;   // worker must be free to create
}

function wireDrop(dropId, inputId, onFiles) {
  const drop = $(dropId), input = $(inputId);
  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => onFiles([...input.files]));
  ["dragover", "dragenter"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => onFiles([...e.dataTransfer.files]));
}

function goCreate() {
  if (MODE === "long_video") return createLong();
  if (MODE === "own_clips") return createOwn();
}

/* Start a long-video upload straight from the persistent ingest band (no modal).
   Reuses the same optimistic-card + progress + SSE flow as the modal path. */
function startLongUpload(file) {
  if (!file) return;
  if (AT_QUOTA) { toast("You've reached your project limit — delete one to upload a new video.", "err"); return; }
  if (UPLOADING && UPLOADING.xhr) { toast("An upload is already in progress."); return; }
  longFile = file; MODE = "long_video";
  if ($("longName")) $("longName").value = "";
  createLong();
}

/* Long video: POST /api/upload-video with process=1 via XHR (progress bar).
   An optimistic 'uploading' card appears immediately. */
function createLong() {
  if (!longFile) return;
  const desiredName = ($("longName") && $("longName").value.trim()) || "";
  $("mGo").disabled = true; $("upbar").classList.add("on");
  $("uplabel").style.display = "block"; $("uplabel").textContent = "uploading 0% — keep this tab open";
  UPLOADING = { name: desiredName || longFile.name.replace(/\.[^.]+$/, ""), pct: 0 };
  render();
  const fd = new FormData();
  fd.append("file", longFile); fd.append("process", "1");
  const xhr = new XMLHttpRequest();
  UPLOADING.xhr = xhr;   // so Cancel / scrim-close can abort the in-flight upload
  xhr.open("POST", "/api/upload-video");
  xhr.upload.onprogress = (ev) => {
    if (ev.lengthComputable) {
      const pct = Math.round((ev.loaded / ev.total) * 100);
      $("upfill").style.width = pct + "%";
      $("uplabel").textContent = `uploading ${pct}% — keep this tab open`;
      if (UPLOADING) { UPLOADING.pct = pct; render(); }
    }
  };
  xhr.onload = async () => {
    UPLOADING = null;
    $("uplabel").style.display = "none";
    if (xhr.status === 401) { location.href = "/login?next=/projects"; return; }
    let d = {}; try { d = JSON.parse(xhr.responseText); } catch (x) {}
    if (xhr.status === 409) { busyToast(); closeModal(); await load(); return; }
    if (!d.ok) { toast(d.error || "Upload failed.", "err"); closeModal(); await load(); return; }
    // Optional rename: if the user typed a project name that differs from the
    // server-assigned name, apply it via the existing rename endpoint (non-fatal).
    if (d.name && desiredName && desiredName !== d.name) {
      try {
        const rr = await fetch(`/api/projects/${encodeURIComponent(d.name)}/rename`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: desiredName }),
        });
        const rd = await rr.json().catch(() => ({}));
        if (!rd.ok) toast("Project created, but rename failed.", "warn");
      } catch (e) { toast("Project created, but rename failed.", "warn"); }
    }
    closeModal();
    await load();
  };
  xhr.onerror = async () => { UPLOADING = null; $("uplabel").style.display = "none"; toast("Network error.", "err"); closeModal(); await load(); };
  xhr.onabort = async () => {
    UPLOADING = null;
    $("upbar").classList.remove("on"); $("upfill").style.width = "0";
    $("uplabel").style.display = "none";
    $("mGo").disabled = false;
    toast("Upload cancelled.");
    await load();
  };
  xhr.send(fd);
}

/* Own clips: multipart name + files -> /api/projects/upload-clips. */
async function createOwn() {
  const name = $("ownName").value.trim();
  if (!name || !ownFiles.length) return;
  $("mGo").disabled = true; $("upbar").classList.add("on"); $("upfill").style.width = "30%";
  const fd = new FormData();
  fd.append("name", name);
  ownFiles.forEach((f) => fd.append("files", f));
  try {
    const r = await fetch("/api/projects/upload-clips", { method: "POST", body: fd });
    if (r.status === 401) { location.href = "/login?next=/projects"; return; }
    const d = await r.json();
    if (r.status === 409) { busyToast(); $("mGo").disabled = false; return; }
    if (!d.ok) { toast(d.error || "Upload failed.", "err"); $("mGo").disabled = false; return; }
    closeModal();
    await load();
  } catch (e) { toast("Network error.", "err"); $("mGo").disabled = false; }
}

/* Appbar busy chip — mirrors the studio's .jobchip. Driven by SSE for ALL job
   kinds (auto-clip / prepare clips / build proxy). Pass null/idle to hide. */
function updateBusyChip(job) {
  const chip = $("busychip");
  if (!chip) return;
  if (!job || (job.status && !["queued", "running"].includes(job.status))) {
    chip.style.display = "none";
    chip.innerHTML = "";
    return;
  }
  const pct = Math.round((job.progress || 0) * 100);
  const label = jobText(job.message) || job.label || "working…";
  chip.style.display = "flex";
  chip.innerHTML = `
    <span class="jc-label mono">${escapeHtml((job.label || "job").toUpperCase())}</span>
    <span class="jc-bar"><i style="width:${pct}%"></i></span>
    <span class="jc-pct mono">${pct}%</span>`;
  chip.title = label;
}

/* ----------------------------------------------------------------- live */
function initEvents() {
  try {
    const es = new EventSource("/api/events");
    es.onmessage = (e) => {
      let evt; try { evt = JSON.parse(e.data); } catch (x) { return; }
      const job = evt.job; if (!job) return;
      const pct = Math.round((job.progress || 0) * 100);
      if (evt.type === "job_done") {
        LAST_PROGRESS = null;
        updateBusyChip(null);    // hide appbar chip
        load();                  // refetch derived statuses
      } else {
        // job_progress (or job_queued). Always update the appbar busy chip for
        // ALL job kinds (incl. "build proxy"). Cache progress so a re-render
        // seeds the bar instead of resetting it.
        BUSY = true; applyBusy();
        LAST_PROGRESS = { pct, message: job.message };
        updateBusyChip(job);
        // "build proxy" is chip-only — do NOT overwrite the processing card text.
        if (job.label === "build proxy") return;
        const card = document.querySelector('.card[data-status="processing"]');
        if (card) {
          const bar = card.querySelector(".progress");
          const fill = card.querySelector(".progress i");
          const line = card.querySelector(".seglabel");
          const footBtn = card.querySelector(".cfoot .act.ghost");
          if (bar) bar.classList.add("on");
          if (fill) fill.style.width = pct + "%";
          if (line) line.textContent = jobText(job.message);
          if (footBtn) footBtn.textContent = `PROCESSING… ${pct}%`;
        }
      }
    };
    es.onerror = () => {};
  } catch (e) {}
}

/* ----------------------------------------------------------------- user */
async function loadUser() {
  try {
    const r = await fetch("/api/me");
    if (r.status === 401) { location.href = "/login?next=/projects"; return; }
    const me = await r.json();
    ME = me;
    if ($("welcomeName")) $("welcomeName").textContent = me.name ? `, ${me.name}` : "";
    const chip = $("userchip");
    chip.innerHTML = "";
    const nm = document.createElement("span");
    nm.textContent = me.name || me.email || "";
    const out = document.createElement("button");
    out.type = "button"; out.textContent = "SIGN OUT";
    out.onclick = async () => {
      try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) {}
      location.href = "/";
    };
    chip.appendChild(nm);
    if (me.is_admin) {
      const adm = document.createElement("a");
      adm.href = "/admin"; adm.textContent = "ADMIN";
      adm.style.cssText = "margin:0 4px; padding:5px 9px; border:1px solid var(--line-hi,#3a2c54);" +
        "border-radius:6px; color:var(--accent-hi,#c08bff); text-decoration:none;" +
        "font-size:.66rem; letter-spacing:.14em";
      chip.appendChild(adm);
    }
    chip.appendChild(out);
  } catch (e) {}
}

/* ---- publishing panel: scheduled + recently published shares ---- */
const PUB_STATUS = {
  scheduled:  { cls: "sched", word: "scheduled" },
  publishing: { cls: "work",  word: "publishing" },
  draft:      { cls: "work",  word: "draft" },
  published:  { cls: "pub",   word: "published" },
  error:      { cls: "err",   word: "failed" },
};
function fmtWhen(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (isNaN(t)) return iso;
  const d = new Date(t);
  return d.toLocaleString(undefined,
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
async function loadShares() {
  const sec = $("publishing");
  if (!sec) return;
  let shares = [];
  try {
    const r = await fetch("/api/social/shares");
    if (!r.ok) { sec.style.display = "none"; return; }
    const d = await r.json();
    shares = d.shares || [];
  } catch (e) { sec.style.display = "none"; return; }
  if (!shares.length) { sec.style.display = "none"; return; }
  // scheduled (upcoming) first by scheduled_at asc, then the rest by created_at desc
  const sched = shares.filter((s) => s.status === "scheduled")
    .sort((a, b) => Date.parse(a.scheduled_at || 0) - Date.parse(b.scheduled_at || 0));
  const rest = shares.filter((s) => s.status !== "scheduled").slice(0, 6);
  const rows = sched.concat(rest).slice(0, 10);
  const proj = (name) => {
    const p = PROJECTS.find((x) => x.name === name);
    return p ? (p.display_name || p.name) : name;
  };
  $("pubCount").textContent =
    `${sched.length} scheduled · ${shares.length} total`;
  $("pubList").innerHTML = rows.map((s) => {
    const st = PUB_STATUS[s.status] || { cls: "work", word: s.status || "" };
    const when = s.status === "scheduled" ? fmtWhen(s.scheduled_at)
      : fmtWhen(s.created_at);
    const sub = [proj(s.project), s.account_name].filter(Boolean).join(" · ");
    const link = s.post_url
      ? `<a class="pub-link" href="${escapeHtml(s.post_url)}" target="_blank" rel="noopener">View</a>` : "";
    return `<div class="pub-row ${st.cls}">
      <span class="pub-when">${escapeHtml(when)}<span class="st">${escapeHtml(st.word)}</span></span>
      <span class="pub-plat">${escapeHtml(s.platform || "social")}</span>
      <span class="pub-body">
        <span class="pub-cap">${escapeHtml(s.caption || "(no caption)")}</span>
        <span class="pub-sub mono">${escapeHtml(sub)}</span>
      </span>
      ${link}
    </div>`;
  }).join("");
  sec.style.display = "";
}

/* ---- connected accounts strip ---- */
const SM_BRAND = {
  instagram: "#E1306C", youtube: "#FF0000", tiktok: "#25F4EE",
  linkedin: "#0A66C2", facebook: "#1877F2", threads: "#888",
  pinterest: "#E60023", bluesky: "#0085FF", x: "#1d1d1f",
};
const SM_PLATFORM = {
  instagram: "Instagram", youtube: "YouTube", tiktok: "TikTok",
  linkedin: "LinkedIn", facebook: "Facebook", threads: "Threads",
  pinterest: "Pinterest", bluesky: "Bluesky", x: "X",
};
async function loadAccounts() {
  const card = $("accountsCard"), row = $("acctRow");
  if (!card || !row) return;
  let data = null;
  try {
    const r = await fetch("/api/social/accounts");
    if (!r.ok) { card.style.display = "none"; syncHub(); return; }
    data = await r.json();
  } catch (e) { card.style.display = "none"; syncHub(); return; }
  const accts = (data && data.accounts) || [];
  // Show the card if sharing is enabled (so the user sees the connect hint) or
  // there are accounts; hide entirely when the provider isn't configured.
  if (!data || (!data.enabled && !accts.length)) { card.style.display = "none"; syncHub(); return; }
  card.style.display = "";
  if (!accts.length) {
    row.innerHTML = `<div class="acct-empty">No accounts connected yet — ` +
      `<a href="/studio">connect one in the studio</a> to publish your clips.</div>`;
    syncHub(); return;
  }
  row.innerHTML = accts.map((a) => {
    const bg = SM_BRAND[a.platform] || "var(--accent-dim)";
    const nm = a.display_name || SM_PLATFORM[a.platform] || a.platform;
    const av = a.avatar_url
      ? `<img class="av" src="${escapeHtml(a.avatar_url)}" alt="">`
      : `<span class="av av-ph" style="background:${bg}">${escapeHtml((nm[0] || "?"))}</span>`;
    return `<span class="acct" title="${escapeHtml(nm)}">${av}<span class="am">` +
      `<span class="nm">${escapeHtml(nm)}</span>` +
      `<span class="pl mono">${escapeHtml(SM_PLATFORM[a.platform] || a.platform)}</span>` +
      `</span></span>`;
  }).join("");
  syncHub();
}

/* ---- YouTube automation status card ---- */
async function loadAutomation() {
  const card = $("autoCard");
  if (!card) return;
  let a = null;
  try {
    const r = await fetch("/api/automation");
    if (!r.ok) { card.style.display = "none"; syncHub(); return; }
    a = (await r.json()).automation;
  } catch (e) { card.style.display = "none"; syncHub(); return; }
  if (!a || !a.channel_id) {
    // Not set up — show a gentle CTA card so the feature is discoverable.
    card.style.display = "";
    $("autoDot").className = "auto-dot off";
    $("autoState").textContent = "Not set up";
    $("autoSub").innerHTML = `Auto-turn every new YouTube upload into edited, ` +
      `publish-ready shorts. <a class="hub-link" style="border:none;padding:0;color:var(--accent-hi)" href="/settings">Set it up →</a>`;
    $("autoMeta").textContent = "";
    syncHub(); return;
  }
  card.style.display = "";
  const ch = a.channel_title || a.channel_handle || "your channel";
  $("autoDot").className = "auto-dot " + (a.enabled ? "on" : "off");
  $("autoState").innerHTML = a.enabled
    ? `Watching <b>${escapeHtml(ch)}</b>`
    : `Paused — <b>${escapeHtml(ch)}</b>`;
  $("autoSub").textContent = a.enabled
    ? `New uploads auto-edit with the “${a.auto_edit_style}” style and arrive here ready to review.`
    : "Watching is off. Turn it on in Settings to resume.";
  const meta = $("autoMeta");
  if (a.last_error) { meta.className = "auto-meta err"; meta.textContent = "⚠ " + a.last_error; }
  else {
    meta.className = "auto-meta";
    meta.textContent = a.last_checked ? "Last checked " + ago(a.last_checked) : "";
  }
  syncHub();
}

/* Show the hub wrapper if either card inside it is visible. */
function syncHub() {
  const hub = $("hub"); if (!hub) return;
  const acct = $("accountsCard"), auto = $("autoCard");
  const any = (acct && acct.style.display !== "none") ||
              (auto && auto.style.display !== "none");
  hub.style.display = any ? "" : "none";
}

/* ----------------------------------------------------------------- boot */
function boot() {
  loadUser();
  load();
  loadShares();
  loadAccounts();
  loadAutomation();
  initEvents();
  setInterval(load, 10000);   // fallback poll
  setInterval(loadShares, 30000);  // refresh the publishing panel less often
  setInterval(loadAutomation, 30000);  // refresh automation status

  $("newBtn").onclick = openModal;

  // persistent ingest band: click/keyboard to choose, plus page-level drag-drop.
  const ingest = $("ingest"), ingestFile = $("ingestFile");
  const modalOpen = () => $("scrim").classList.contains("on");
  $("ingestDrop").addEventListener("click", () => ingestFile.click());
  $("ingestDrop").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ingestFile.click(); }
  });
  ingestFile.addEventListener("change", () => {
    if (ingestFile.files[0]) startLongUpload(ingestFile.files[0]);
    ingestFile.value = "";
  });
  let dragDepth = 0;
  const bandActive = () => ingest.classList.contains("on") && !modalOpen();
  document.addEventListener("dragenter", () => {
    if (!bandActive()) return;
    dragDepth++; ingest.classList.add("drag");
  });
  document.addEventListener("dragleave", () => {
    if (!bandActive()) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) ingest.classList.remove("drag");
  });
  document.addEventListener("dragover", (e) => { if (bandActive()) e.preventDefault(); });
  document.addEventListener("drop", (e) => {
    if (!bandActive()) return;          // modal drops are handled by the modal's own zones
    e.preventDefault();
    dragDepth = 0; ingest.classList.remove("drag");
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) startLongUpload(f);
  });

  $("emptyLong").onclick = () => { openModal(); pickMode("long_video"); };
  $("emptyOwn").onclick = () => { openModal(); pickMode("own_clips"); };
  $("mCancel").onclick = dismissModal;
  $("mClose").onclick = dismissModal;
  $("scrim").addEventListener("click", (e) => { if (e.target === $("scrim")) dismissModal(); });
  // Esc closes the modal — unless an upload is in flight (require explicit Cancel).
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("scrim").classList.contains("on")) {
      if (UPLOADING && UPLOADING.xhr) return;  // don't drop an in-progress upload silently
      closeModal();
    }
  });
  $("tileLong").onclick = () => pickMode("long_video");
  $("tileOwn").onclick = () => pickMode("own_clips");
  [["tileLong", "long_video"], ["tileOwn", "own_clips"]].forEach(([id, m]) => {
    $(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickMode(m); }
    });
  });
  $("mGo").onclick = goCreate;
  $("ownName").addEventListener("input", refreshGo);
  if ($("longName")) $("longName").addEventListener("input", refreshGo);

  document.querySelectorAll("#filters .pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      FILTER = pill.dataset.filter;
      document.querySelectorAll("#filters .pill").forEach((x) =>
        x.classList.toggle("on", x === pill));
      render();
    });
  });

  // search (debounced), sort, view toggle
  let searchTimer = null;
  const searchEl = $("search");
  if (searchEl) searchEl.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { SEARCH = searchEl.value.trim().toLowerCase(); render(); }, 140);
  });
  const sortEl = $("sort");
  if (sortEl) { sortEl.value = SORT; sortEl.addEventListener("change", () => {
    SORT = sortEl.value;
    try { localStorage.setItem("vc_proj_sort", SORT); } catch (e) {}
    render();
  }); }
  document.querySelectorAll("#viewtoggle button").forEach((b) =>
    b.addEventListener("click", () => {
      VIEW = b.dataset.view;
      try { localStorage.setItem("vc_proj_view", VIEW); } catch (e) {}
      render();
    }));

  // bulk selection
  const selectBtn = $("selectBtn");
  if (selectBtn) selectBtn.onclick = () => setSelMode(!SELMODE);
  const wire = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };
  wire("selCancel", () => setSelMode(false));
  wire("selDelete", bulkDelete);
  wire("selMove", () => moveToFolder([...SELECTED]));
  wire("selAll", () => {
    const vis = visibleProjects();
    const allSel = vis.every((p) => SELECTED.has(p.name));
    vis.forEach((p) => allSel ? SELECTED.delete(p.name) : SELECTED.add(p.name));
    disarmDelete();
    render();
  });

  // move-to-folder modal
  wire("folderGo", commitFolder);
  wire("folderCancel", () => $("folderScrim").classList.remove("on"));
  wire("folderClose", () => $("folderScrim").classList.remove("on"));
  $("folderScrim").addEventListener("click", (e) => {
    if (e.target === $("folderScrim")) $("folderScrim").classList.remove("on");
  });
  $("folderInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") commitFolder();
    if (e.key === "Escape") $("folderScrim").classList.remove("on");
  });

  wireDrop("dropLong", "fileLong", (files) => {
    longFile = files[0] || null;
    $("fnLong").textContent = longFile
      ? `${longFile.name} · ${fmtSize(longFile.size)}` : "";
    refreshGo();
  });
  wireDrop("dropOwn", "fileOwn", (files) => {
    ownFiles = files;
    if (files.length) {
      const total = files.reduce((s, f) => s + f.size, 0);
      $("fnOwn").textContent =
        `${files.length} file${files.length === 1 ? "" : "s"} · ${fmtSize(total)}`;
    } else { $("fnOwn").textContent = ""; }
    refreshGo();
  });
}

boot();
