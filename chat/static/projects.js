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

/* Does a project match the active filter pill? */
function matchesFilter(p) {
  switch (FILTER) {
    case "processing": return p.status === "processing" || p.status === "needs_processing" || p.status === "error";
    case "ready":      return p.status === "clips_ready" || p.status === "editing";
    case "done":       return p.status === "done";
    default:           return true;
  }
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
  if (UPLOADING && (FILTER === "all" || FILTER === "processing")) cards.push(uploadingCard());
  PROJECTS.filter(matchesFilter).forEach((p) => cards.push(projectCard(p)));
  cards.forEach((c, i) => { c.style.setProperty("--i", Math.min(i, 8)); grid.appendChild(c); });

  // total count is across ALL projects (not the filtered view)
  const total = PROJECTS.length + (UPLOADING ? 1 : 0);
  $("count").textContent = total ? `${total} project${total === 1 ? "" : "s"}` : "";
  // empty hero only when there are genuinely zero projects; filter bar appears
  // once there are any projects to filter.
  const has = total > 0;
  $("empty").style.display = has ? "none" : "";
  $("filters").style.display = has ? "flex" : "none";
  $("grid").style.display = has ? "grid" : "none";
  // greeting + at-a-glance stats + persistent upload band live above the grid,
  // only once there are projects (the empty hero owns the zero-state).
  $("welcome").style.display = has ? "" : "none";
  $("ingest").classList.toggle("on", has);
  if (has) updateWelcome();
  // when a filter yields nothing but projects exist, show a small note
  if (total > 0 && cards.length === 0) {
    const note = document.createElement("div");
    note.className = "cmeta mono";
    note.style.cssText = "grid-column:1/-1; padding:30px 4px; color:var(--bone-faint)";
    note.textContent = "No projects match this filter.";
    grid.appendChild(note);
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

  const cl = p.clips || {};
  const metaBits = [];
  metaBits.push(modeLabel(p.mode));
  if (cl.total) metaBits.push(`${cl.total} clip${cl.total === 1 ? "" : "s"}`);
  if (p.modified) metaBits.push(ago(p.modified));
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

  el.innerHTML = `
    <div class="thumb">
      <span class="tbadge ${badge.cls} mono">${escapeHtml(badge.word)}</span>
      <img loading="lazy" alt="${escapeHtml(p.display_name || p.name)}"
       src="${p.thumb}" onerror="this.replaceWith(filmGlyph('${p.mode}'))">
      ${dur ? `<span class="tdur mono">${escapeHtml(dur)}</span>` : ""}
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
            <button data-act="rename">Rename</button>
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

  if (act === "menu") {
    const m = btn.parentElement.querySelector(".menu");
    document.querySelectorAll(".menu.on").forEach((x) => { if (x !== m) x.classList.remove("on"); });
    m.classList.toggle("on");
    return;
  }
  if (act === "open") return openProject(p.name);
  if (act === "process") return processProject(p.name, btn);
  if (act === "rename") return startRename(p, el);
  if (act === "delete") return confirmDelete(p, el);
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

/* ----------------------------------------------------------------- boot */
function boot() {
  loadUser();
  load();
  initEvents();
  setInterval(load, 10000);   // fallback poll

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
