/* VibeClip Studio — CapCut-style pro timeline (Pro Faz 4 → Faz 5 MVP).
   View of /api/timeline/{id}. x-axis = player time. A MAIN VIDEO track (row 0)
   shows a thumbnail filmstrip; below it the stage tracks (zoom/broll/…).
   Gestures: scrub, split (S) at playhead, ripple-delete a segment, trim the
   head/tail with edge handles, drag/resize stage events with magnetic snapping,
   undo/redo, zoom-to-fit. Every mutating gesture funnels into an existing tool
   (remove_section / nudge_edit / edit_event / …) — no new render path.
   Shares app.js globals (player, activeClip, runJob, reloadState, fmtTC). */

(() => {
  const $ = (id) => document.getElementById(id);
  const dock = $("tlpanel");
  const gutter = $("tlGutter");
  const body = $("tlBody");
  const scroll = $("tlScroll");
  const ruler = $("tlRuler");
  const playhead = $("tlPlayhead");
  const inout = $("tlInOut");

  const RULER_H = 22, ROW_H = 22, MAIN_H = 56;
  let DATA = null;
  let GHOST = null;   // plan-preview timeline (ghost-diff overlay) or null
  let pxPerSec = 80;
  let shown = false;
  let inPt = null, outPt = null;
  let SEL = null;     // selected main-track segment {start,end} or null

  // filmstrip sprite (persists across re-renders; refetched on artifact change)
  let FILM = null, FILM_KEY = "";
  // audio waveform peaks (min/max buckets), decoded once per artifact
  let PEAKS = null, PEAKS_KEY = "", AUDIO_CTX = null;
  const PEAK_BUCKETS = 1600;
  // multi-select of editable events ("stage:idx" keys)
  const SELSET = new Set();
  // removed-material cut spans (lazy, per clip) for the seam-restore popover
  let CUTS = null, CUTS_CLIP = null;
  // snapping
  let SNAP_ON = localStorage.getItem("kesim_snap") !== "0";
  let ALT_HELD = false;
  let SNAP_PTS = [];
  const snapline = document.createElement("div");
  snapline.className = "tl-snapline"; snapline.style.display = "none";

  /* ----------------------------------------------------------- load + render */
  async function load(clipId) {
    if (clipId == null) return;
    try {
      DATA = await (await fetch(`/api/timeline/${clipId}`)).json();
      if (DATA.error) { DATA = null; return; }
      SEL = null; SELSET.clear();
      if (CUTS_CLIP !== DATA.clip_id) { CUTS = null; CUTS_CLIP = null; }
      loadFilmstrip();
      loadPeaks();
      fitZoom();
      render();
      updateHistoryButtons();
    } catch (e) { DATA = null; }
  }

  function fitZoom() {
    if (!DATA || !DATA.duration) return;
    const vis = scroll.clientWidth || 600;
    pxPerSec = Math.max(8, Math.min(200, vis / DATA.duration));
  }

  function totalH() {
    if (!DATA) return MAIN_H;
    const ghostOn = GHOST && GHOST.clip_id === DATA.clip_id;
    const extra = ghostOn
      ? GHOST.tracks.filter(g => !DATA.tracks.some(t => t.key === g.key)).length
      : 0;
    return MAIN_H + (DATA.tracks.length + extra) * ROW_H;
  }

  function splitTimes() {
    return (DATA && DATA.markers || [])
      .filter(m => m.color === "split").map(m => m.t).sort((a, b) => a - b);
  }
  function segments() {
    if (!DATA) return [];
    const bounds = [0, ...splitTimes(), DATA.duration];
    const segs = [];
    for (let i = 0; i < bounds.length - 1; i++)
      if (bounds[i + 1] - bounds[i] > 1e-3)
        segs.push({ start: bounds[i], end: bounds[i + 1] });
    return segs;
  }

  function render() {
    if (!DATA) return;
    updateSpeedButton();
    const ghostOn = GHOST && GHOST.clip_id === DATA.clip_id;
    const ghostExtra = ghostOn
      ? GHOST.tracks.filter(g => !DATA.tracks.some(t => t.key === g.key))
      : [];
    const dur = Math.max(DATA.duration, ghostOn ? GHOST.duration : 0);
    const W = Math.max(scroll.clientWidth, dur * pxPerSec);
    // gutter: ruler-height spacer + a vertically-scrolling label list (Video
    // label first, then the stage tracks), kept in sync with body scroll.
    gutter.innerHTML =
      `<div class="tl-gspacer" style="height:${RULER_H}px">` +
        `<button id="tlLock" class="tl-lock${DATA.locked ? " on" : ""}" ` +
        `title="${DATA.locked ? "Picture locked — timing frozen" :
          "Lock picture (cut/timing frozen)"}">` +
        `${DATA.locked ? "🔒" : "🔓"}</button>` +
      `</div>` +
      `<div class="tl-glist" id="tlGlist">` +
      `<div class="tl-glabel tl-gmain" style="height:${MAIN_H}px">Video</div>` +
      DATA.tracks.map(t =>
        `<div class="tl-glabel" style="height:${ROW_H}px">${t.label}</div>`).join("") +
      ghostExtra.map(t =>
        `<div class="tl-glabel ghost" style="height:${ROW_H}px">+ ${t.label}</div>`).join("") +
      `</div>`;
    // body
    body.style.width = W + "px";
    body.style.height = totalH() + "px";
    body.querySelectorAll(".tl-row,.tl-split,.tl-snapline").forEach(e => e.remove());

    // ---- row 0: MAIN VIDEO TRACK (filmstrip + trim handles + selection) ----
    const main = document.createElement("div");
    main.className = "tl-row tl-mainrow";
    main.style.cssText = `top:0;height:${MAIN_H}px`;
    const film = document.createElement("canvas");
    film.className = "tl-film";
    main.appendChild(film);
    // seam ticks (removed-material anchors) — click to restore
    (DATA.seams || []).forEach(s => {
      const tick = document.createElement("div");
      tick.className = "tl-seam";
      tick.style.left = (s * pxPerSec) + "px";
      tick.title = "removed material — click to restore";
      tick.onclick = (e) => { e.stopPropagation(); openSeamPopover(s, tick); };
      main.appendChild(tick);
    });
    // segment-selection highlight
    if (SEL) {
      const hl = document.createElement("div");
      hl.className = "tl-seg-hl";
      hl.style.left = (SEL.start * pxPerSec) + "px";
      hl.style.width = ((SEL.end - SEL.start) * pxPerSec) + "px";
      main.appendChild(hl);
    }
    // trim handles (hidden when locked)
    if (!DATA.locked) {
      const tl = document.createElement("div");
      tl.className = "tl-trim tl-trim-l"; tl.title = "Trim head";
      const tr = document.createElement("div");
      tr.className = "tl-trim tl-trim-r"; tr.title = "Trim tail";
      tr.style.left = (DATA.duration * pxPerSec) + "px";
      main.appendChild(tl); main.appendChild(tr);
    }
    body.insertBefore(main, playhead);
    drawFilmstrip(film, W);

    // ---- stage rows (offset below the main track) ----
    DATA.tracks.forEach((t, ri) => {
      const row = document.createElement("div");
      row.className = "tl-row";
      row.style.cssText = `top:${MAIN_H + ri * ROW_H}px;height:${ROW_H}px`;
      t.items.forEach(it => {
        const box = document.createElement("div");
        const x = it.start * pxPerSec;
        const ed = !!t.editable;
        if (t.kind === "point") {
          box.className = "tl-pt" + (ed ? " tl-ed" : "");
          box.style.cssText = `left:${x}px`;
          box.title = it.label + (ed ? " · drag to move · right-click delete" : "");
        } else {
          const w = Math.max(3, ((it.end ?? it.start) - it.start) * pxPerSec);
          box.className = "tl-box tl-" + t.key + (it.span ? " span" : "") +
            (ed ? " tl-ed" : "");
          box.style.cssText = `left:${x}px;width:${w}px`;
          box.textContent = it.label || "";
          box.title = `${it.label || t.label} · ${fmtTC(it.start)}` +
            (ed ? " · drag/pull edges · right-click delete" : "");
          if (ed && !it.span) {
            const l = document.createElement("div"); l.className = "tl-rz tl-rz-l";
            const r = document.createElement("div"); r.className = "tl-rz tl-rz-r";
            box.appendChild(l); box.appendChild(r);
          }
        }
        if (ed) {
          box.dataset.stage = t.key; box.dataset.idx = it.idx;
          box.dataset.kind = t.kind; box.dataset.start = it.start;
          box.dataset.end = (it.end ?? "");
          if (t.key === "zoom") box.dataset.motion = it.motion || "center";
          if (SELSET.has(t.key + ":" + it.idx)) box.classList.add("selected");
        }
        row.appendChild(box);
      });
      body.insertBefore(row, playhead);
    });

    // ---- ghost-diff overlay (offset below the main track) ----
    if (ghostOn) {
      const rowIdx = {};
      DATA.tracks.forEach((t, i) => { rowIdx[t.key] = i; });
      ghostExtra.forEach((t, i) => {
        rowIdx[t.key] = DATA.tracks.length + i;
        const row = document.createElement("div");
        row.className = "tl-row";
        row.style.cssText =
          `top:${MAIN_H + rowIdx[t.key] * ROW_H}px;height:${ROW_H}px`;
        body.insertBefore(row, playhead);
      });
      const rows = body.querySelectorAll(".tl-row:not(.tl-mainrow)");
      GHOST.tracks.forEach(t => {
        const row = rows[rowIdx[t.key]];
        if (!row) return;
        t.items.forEach(it => {
          const g = document.createElement("div");
          const x = it.start * pxPerSec;
          if (t.kind === "point") {
            g.className = "tl-pt tl-ghost";
            g.style.cssText = `left:${x}px`;
          } else {
            const w = Math.max(3, ((it.end ?? it.start) - it.start) * pxPerSec);
            g.className = "tl-box tl-ghost";
            g.style.cssText = `left:${x}px;width:${w}px`;
            g.textContent = it.label || "";
          }
          g.title = `PLAN: ${it.label || t.label}`;
          row.appendChild(g);
        });
      });
    }

    // ---- split boundaries: full-height cut lines across all tracks ----
    splitTimes().forEach(t => {
      const m = (DATA.markers || []).find(mk => mk.color === "split" && mk.t === t);
      const line = document.createElement("div");
      line.className = "tl-split";
      line.style.left = (t * pxPerSec) + "px";
      line.style.height = totalH() + "px";
      line.title = "split — right-click to remove";
      line.oncontextmenu = (e) => {
        e.preventDefault();
        if (!m) return;
        runJob("/api/tool", { name: "remove_marker",
          args: { clip_id: DATA.clip_id, marker_id: m.id } })
          .then(() => load(DATA.clip_id));
      };
      body.insertBefore(line, playhead);
    });

    body.appendChild(snapline);
    drawRuler(W);
    drawMarkers(W);
    paintInOut();
    updatePlayhead();
    const glist = $("tlGlist");
    scroll.onscroll = () => { if (glist) glist.style.transform =
      `translateY(${-scroll.scrollTop}px)`; };
    const lockBtn = $("tlLock");
    if (lockBtn) lockBtn.onclick = async () => {
      await runJob("/api/tool", {
        name: DATA.locked ? "unlock_clip" : "lock_clip",
        args: { clip_id: DATA.clip_id } });
      load(DATA.clip_id);
    };
  }

  /* ----------------------------------------------------------- filmstrip */
  function loadFilmstrip() {
    if (!DATA) return;
    const key = DATA.artifact_key || "";
    if (!key) { FILM = null; FILM_KEY = ""; return; }
    if (key === FILM_KEY && FILM) return;          // already have this artifact
    FILM_KEY = key;
    const img = new Image();
    img.onload = () => { if (FILM_KEY === key) { FILM = img;
      if (shown && DATA) render(); } };
    img.onerror = () => { FILM = null; };
    img.src = `/api/filmstrip/${DATA.clip_id}?n=80&h=54&k=${key}`;
  }

  // decode the clip's audio once per artifact → fixed min/max peak buckets
  function loadPeaks() {
    if (!DATA) return;
    const key = DATA.artifact_key || "";
    if (!key) { PEAKS = null; PEAKS_KEY = ""; return; }
    if (key === PEAKS_KEY && PEAKS) return;
    PEAKS_KEY = key;
    const url = DATA.media_url, clip = DATA.clip_id;
    fetch(url).then(r => r.arrayBuffer()).then(buf => {
      try {
        AUDIO_CTX = AUDIO_CTX ||
          new (window.AudioContext || window.webkitAudioContext)();
      } catch (e) { return; }
      AUDIO_CTX.decodeAudioData(buf.slice(0), (audio) => {
        if (PEAKS_KEY !== key) return;
        const ch = audio.getChannelData(0);
        const nb = PEAK_BUCKETS, per = Math.max(1, Math.floor(ch.length / nb));
        const mn = new Float32Array(nb), mx = new Float32Array(nb);
        for (let b = 0; b < nb; b++) {
          let lo = 0, hi = 0;
          const s = b * per, e = Math.min(ch.length, s + per);
          for (let i = s; i < e; i++) { const v = ch[i];
            if (v < lo) lo = v; if (v > hi) hi = v; }
          mn[b] = lo; mx[b] = hi;
        }
        PEAKS = { min: mn, max: mx };
        if (shown && DATA && DATA.clip_id === clip) render();
      }, () => {});
    }).catch(() => {});
  }

  /* ----------------------------------------------------------- popovers */
  let popEl = null;
  function closePop() { if (popEl) { popEl.remove(); popEl = null; } }
  document.addEventListener("mousedown", (e) => {
    if (popEl && !e.target.closest(".tl-pop") && !e.target.closest(".tl-ctx"))
      closePop();
    if (ctxEl && !e.target.closest(".tl-ctx")) closeCtx();
  }, true);

  function openSeamPopover(seamT, anchorEl) {
    closePop();
    const showWith = (cuts) => {
      // match the cut whose output anchor sits at this seam
      let cut = null, bd = 0.25;
      (cuts || []).forEach(c => { const d = Math.abs((c.out_anchor || 0) - seamT);
        if (d <= bd) { bd = d; cut = c; } });
      const pop = document.createElement("div");
      pop.className = "tl-pop";
      if (!cut) {
        pop.innerHTML = `<div class="tl-pop-h">Removed material</div>` +
          `<div class="tl-pop-b">Couldn't locate this cut's text.</div>`;
      } else {
        const txt = (cut.text || "").slice(0, 160) || "(no speech)";
        pop.innerHTML = `<div class="tl-pop-h">Removed · ${cut.duration}s</div>` +
          `<div class="tl-pop-b">“${txt}”</div>` +
          `<button class="tl-pop-btn">↩ Restore this</button>`;
        pop.querySelector(".tl-pop-btn").onclick = () => {
          closePop(); note("Restoring…");
          runJob("/api/tool", { name: "restore_section", args: {
            clip_id: DATA.clip_id, start: cut.start, end: cut.end } })
            .then(async (job) => {
              const res = (job.result && job.result.result) || {};
              if (job.result && job.result.ok === false)
                note("✗ " + (res.error || "restore failed"));
              else if (res.notes && res.notes.length) note(res.notes.join(" "));
              await reloadState(); load(DATA.clip_id);
            });
        };
      }
      document.body.appendChild(pop);
      const r = anchorEl.getBoundingClientRect();
      pop.style.position = "fixed";
      pop.style.left = Math.min(r.left, window.innerWidth - 250) + "px";
      pop.style.top = (r.bottom + 6) + "px";
      popEl = pop;
    };
    if (CUTS && CUTS_CLIP === DATA.clip_id) { showWith(CUTS); return; }
    note("Loading cut…");
    fetch(`/api/transcript/${DATA.clip_id}?cuts=1`).then(r => r.json())
      .then(d => { CUTS = d.cuts || []; CUTS_CLIP = DATA.clip_id;
        note(""); showWith(CUTS); })
      .catch(() => note("Couldn't load cut info."));
  }

  /* ----------------------------------------------------------- context menu */
  let ctxEl = null;
  function closeCtx() { if (ctxEl) { ctxEl.remove(); ctxEl = null; } }
  function openCtxMenu(cx, cy, items) {
    closeCtx();
    const m = document.createElement("div");
    m.className = "tl-ctx";
    items.forEach(it => {
      const b = document.createElement("button");
      b.className = "tl-ctx-item" + (it.danger ? " danger" : "");
      b.textContent = it.label;
      b.onclick = () => { closeCtx(); it.fn(); };
      m.appendChild(b);
    });
    document.body.appendChild(m);
    m.style.left = Math.min(cx, window.innerWidth - 180) + "px";
    m.style.top = Math.min(cy, window.innerHeight - 20 - items.length * 30) + "px";
    ctxEl = m;
  }

  /* ----------------------------------------------------------- multi-delete */
  async function batchDeleteEvents() {
    // group selected by stage, delete in descending index (indices shift)
    const byStage = {};
    SELSET.forEach(k => { const [s, i] = k.split(":");
      (byStage[s] = byStage[s] || []).push(+i); });
    const n = SELSET.size;
    SELSET.clear();
    note(`Deleting ${n} event${n > 1 ? "s" : ""}…`);
    for (const stage of Object.keys(byStage))
      for (const idx of byStage[stage].sort((a, b) => b - a))
        await runJob("/api/tool", { name: "delete_event",
          args: { clip_id: DATA.clip_id, stage, index: idx } });
    await reloadState(); load(DATA.clip_id);
  }
  // unified Delete: selected events first, else the selected segment
  window.tlDelete = () => {
    if (SELSET.size) { batchDeleteEvents(); return; }
    window.tlRippleDelete();
  };
  function drawFilmstrip(canvas, W) {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr; canvas.height = MAIN_H * dpr;
    canvas.style.width = W + "px"; canvas.style.height = MAIN_H + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.fillStyle = "#08060f"; ctx.fillRect(0, 0, W, MAIN_H);
    if (FILM && FILM.naturalWidth > 0) {
      ctx.imageSmoothingEnabled = true;
      // the sprite's tiles are evenly spaced in time, so stretching the whole
      // sprite to the full timeline width lands each tile at its time slot.
      ctx.drawImage(FILM, 0, 0, FILM.naturalWidth, FILM.naturalHeight,
        0, 0, W, MAIN_H);
    } else {
      ctx.fillStyle = "#2a2040"; ctx.font = "10px 'IBM Plex Mono',monospace";
      ctx.fillText("render to see frames", 8, MAIN_H / 2);
    }
    drawWave(ctx, W);
  }

  // audio waveform as a translucent band along the bottom of the filmstrip
  function drawWave(ctx, W) {
    if (!PEAKS) return;
    const BAND = 22, cy = MAIN_H - BAND / 2 - 1;
    ctx.fillStyle = "rgba(8,6,15,.55)";
    ctx.fillRect(0, MAIN_H - BAND - 1, W, BAND + 1);
    ctx.strokeStyle = "rgba(192,139,255,.85)"; ctx.lineWidth = 1;
    ctx.beginPath();
    const nb = PEAKS.max.length;
    for (let x = 0; x < W; x++) {
      const b = Math.min(nb - 1, Math.floor(x / W * nb));
      const a = Math.max(Math.abs(PEAKS.min[b]), Math.abs(PEAKS.max[b]));
      const h = Math.max(0.5, a * (BAND / 2 - 1));
      ctx.moveTo(x + 0.5, cy - h); ctx.lineTo(x + 0.5, cy + h);
    }
    ctx.stroke();
  }

  function drawRuler(W) {
    const dpr = window.devicePixelRatio || 1;
    ruler.width = W * dpr; ruler.height = RULER_H * dpr;
    ruler.style.width = W + "px"; ruler.style.height = RULER_H + "px";
    const ctx = ruler.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, RULER_H);
    ctx.fillStyle = "#0a0712"; ctx.fillRect(0, 0, W, RULER_H);
    const targets = [0.5, 1, 2, 5, 10, 15, 30, 60];
    let step = targets.find(s => s * pxPerSec >= 60) || 60;
    ctx.strokeStyle = "#3a2c54"; ctx.fillStyle = "#918aa6";
    ctx.font = "9px 'IBM Plex Mono', monospace"; ctx.textBaseline = "top";
    for (let t = 0; t <= DATA.duration + step; t += step) {
      const x = Math.round(t * pxPerSec) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, RULER_H - 7); ctx.lineTo(x, RULER_H); ctx.stroke();
      ctx.fillText(fmtTC(t).slice(3), x + 3, 3);   // MM:SS:FF
    }
  }

  function drawMarkers(W) {
    body.querySelectorAll(".tl-marker").forEach(e => e.remove());
    (DATA.markers || []).filter(m => m.color !== "split").forEach(m => {
      const pin = document.createElement("div");
      pin.className = "tl-marker";
      pin.style.left = (m.t * pxPerSec) + "px";
      pin.title = m.label + " — right-click: delete";
      pin.oncontextmenu = (e) => {
        e.preventDefault();
        runJob("/api/tool", { name: "remove_marker",
          args: { clip_id: DATA.clip_id, marker_id: m.id } }).then(() => load(DATA.clip_id));
      };
      pin.onclick = () => { player.currentTime = m.t; player.pause(); };
      body.appendChild(pin);
    });
  }

  function note(msg) {
    const el = $("tlNote");
    if (!el) return;
    el.textContent = msg || "";
    if (msg) { clearTimeout(note._t);
      note._t = setTimeout(() => { if (el.textContent === msg) el.textContent = ""; }, 5000); }
  }

  /* ----------------------------------------------------------- playhead */
  function updatePlayhead() {
    if (!DATA) return;
    const x = (player.currentTime || 0) * pxPerSec;
    playhead.style.transform = `translateX(${x}px)`;
    playhead.style.height = totalH() + "px";
  }
  function rafLoop() {
    if (shown) updatePlayhead();
    requestAnimationFrame(rafLoop);
  }
  requestAnimationFrame(rafLoop);

  /* ----------------------------------------------------------- interaction */
  body.addEventListener("click", (e) => {
    if (e.target.closest(".tl-marker") || e.target.closest(".tl-ed") ||
        e.target.closest(".tl-trim") || e.target.closest(".tl-split")) return;
    const rect = body.getBoundingClientRect();
    const t = Math.max(0, (e.clientX - rect.left) / pxPerSec);
    // clicking the main video track selects the segment under the cursor
    if (e.target.closest(".tl-mainrow")) {
      const seg = segments().find(s => t >= s.start && t < s.end) || null;
      SEL = (SEL && seg && SEL.start === seg.start && SEL.end === seg.end)
        ? null : seg;   // toggle
      render();
    }
    player.currentTime = t;
    player.pause();
  });

  /* --- snapping ---------------------------------------------------------- */
  function collectSnapPoints(exStage, exIdx) {
    const pts = [0, DATA.duration, player.currentTime || 0];
    (DATA.markers || []).forEach(m => pts.push(m.t));
    (DATA.seams || []).forEach(s => pts.push(s));
    DATA.tracks.forEach(t => t.items.forEach(it => {
      if (t.key === exStage && it.idx === exIdx) return;
      pts.push(it.start);
      if (it.end != null) pts.push(it.end);
    }));
    SNAP_PTS = pts.filter(v => v != null && !isNaN(v)).sort((a, b) => a - b);
  }
  function snap(t) {
    if (!SNAP_ON || ALT_HELD || !SNAP_PTS.length) return { t, snapped: false };
    const thr = 8 / pxPerSec;
    let best = null, bd = thr;
    for (const p of SNAP_PTS) {
      const d = Math.abs(p - t);
      if (d <= bd) { bd = d; best = p; }
      if (p > t + thr) break;
    }
    return best != null ? { t: best, snapped: true } : { t, snapped: false };
  }
  function showSnap(t) {
    snapline.style.display = "block";
    snapline.style.left = (t * pxPerSec) + "px";
    snapline.style.height = totalH() + "px";
  }
  function hideSnap() { snapline.style.display = "none"; }

  /* --- editable events: drag to move, edge-pull to resize, right-click del */
  let drag = null;
  function applyDrag(d, dx) {
    const dt = dx / pxPerSec;
    if (d.mode === "move") {
      let ns = Math.max(0, d.origStart + dt);
      let ne = d.origEnd != null ? d.origEnd + (ns - d.origStart) : null;
      const s1 = snap(ns), s2 = ne != null ? snap(ne) : null;
      if (s1.snapped && (!s2 || !s2.snapped ||
          Math.abs(s1.t - ns) <= Math.abs(s2.t - ne))) {
        const sh = s1.t - ns; ns += sh; if (ne != null) ne += sh; showSnap(s1.t);
      } else if (s2 && s2.snapped) {
        const sh = s2.t - ne; ns += sh; ne += sh; showSnap(s2.t);
      } else hideSnap();
      d.preview = { start: ns, end: ne };
    } else if (d.mode === "l") {
      let ns = Math.max(0, Math.min(d.origEnd - 0.1, d.origStart + dt));
      const s = snap(ns);
      if (s.snapped && s.t < d.origEnd - 0.1) { ns = s.t; showSnap(s.t); } else hideSnap();
      d.preview = { start: ns, end: d.origEnd };
    } else {  // "r"
      let ne = Math.max(d.origStart + 0.1, d.origEnd + dt);
      const s = snap(ne);
      if (s.snapped && s.t > d.origStart + 0.1) { ne = s.t; showSnap(s.t); } else hideSnap();
      d.preview = { start: d.origStart, end: ne };
    }
  }
  function paintDrag(d) {
    d.el.style.left = (d.preview.start * pxPerSec) + "px";
    if (d.kind !== "point" && d.preview.end != null)
      d.el.style.width =
        Math.max(3, (d.preview.end - d.preview.start) * pxPerSec) + "px";
  }
  body.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    // trim-handle drag (main track head/tail)
    const th = e.target.closest(".tl-trim");
    if (th && DATA && !DATA.locked) {
      trimDrag = { side: th.classList.contains("tl-trim-l") ? "l" : "r",
                   startX: e.clientX, dt: 0, el: th };
      collectSnapPoints();
      document.body.style.cursor = "ew-resize";
      e.stopPropagation(); e.preventDefault(); return;
    }
    const el = e.target.closest(".tl-ed");
    if (!el) return;
    // shift-click toggles an event into the multi-selection (no drag)
    if (e.shiftKey) {
      const key = el.dataset.stage + ":" + el.dataset.idx;
      if (SELSET.has(key)) SELSET.delete(key); else SELSET.add(key);
      render(); e.stopPropagation(); e.preventDefault(); return;
    }
    const rz = e.target.closest(".tl-rz");
    const mode = rz ? (rz.classList.contains("tl-rz-l") ? "l" : "r") : "move";
    const os = parseFloat(el.dataset.start);
    const oe = el.dataset.end !== "" ? parseFloat(el.dataset.end) : null;
    drag = { stage: el.dataset.stage, idx: +el.dataset.idx, kind: el.dataset.kind,
             mode, el, origStart: os, origEnd: oe, startX: e.clientX,
             moved: false, preview: { start: os, end: oe } };
    collectSnapPoints(drag.stage, drag.idx);
    el.classList.add("dragging");
    e.stopPropagation(); e.preventDefault();
  });

  // ---- trim-handle drag state ----
  let trimDrag = null;
  let trimGhost = null;
  function paintTrim() {
    if (!trimGhost) {
      trimGhost = document.createElement("div");
      trimGhost.className = "tl-trim-ghost";
      const main = body.querySelector(".tl-mainrow");
      if (main) main.appendChild(trimGhost);
    }
    const d = trimDrag;
    if (d.side === "l") {
      const w = Math.max(0, d.dt * pxPerSec);
      trimGhost.style.left = "0px"; trimGhost.style.width = w + "px";
      d.el.style.left = (d.dt * pxPerSec) + "px";
    } else {
      const x = (DATA.duration - d.dt) * pxPerSec;
      trimGhost.style.left = x + "px";
      trimGhost.style.width = Math.max(0, d.dt * pxPerSec) + "px";
      d.el.style.left = x + "px";
    }
    note((d.side === "l" ? "Trim head −" : "Trim tail −") + fmtTC(d.dt).slice(3));
  }
  // output-time delta → source frames, using the kept-spans time map.
  // Incoming t is PLAYER (sped) time; the kept-spans map is PRE-speed output
  // time, so undo the speed first (u = p · factor).
  function outToSrc(t) {
    t = t * (DATA.speed || 1);
    const kept = DATA.kept;
    if (!kept || !kept.length) return t;   // no interior removals
    let acc = 0;
    for (const [a, b] of kept) {
      if (t <= acc + (b - a)) return a + (t - acc);
      acc += b - a;
    }
    return kept[kept.length - 1][1];
  }
  function outDeltaToFrames(t0, t1) {
    return Math.round((outToSrc(t1) - outToSrc(t0)) * (DATA.fps || 30));
  }

  document.addEventListener("mousemove", (e) => {
    if (trimDrag) {
      const raw = (e.clientX - trimDrag.startX) / pxPerSec;
      let dt = trimDrag.side === "l" ? raw : -raw;          // amount trimmed off
      dt = Math.max(0, Math.min(DATA.duration - 0.2, dt));
      // snap the moving edge to nearby points
      const edge = trimDrag.side === "l" ? dt : DATA.duration - dt;
      const s = snap(edge);
      if (s.snapped) { dt = trimDrag.side === "l" ? s.t : DATA.duration - s.t;
        dt = Math.max(0, Math.min(DATA.duration - 0.2, dt)); showSnap(s.t); }
      else hideSnap();
      trimDrag.dt = dt; paintTrim();
      return;
    }
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    if (Math.abs(dx) > 2) drag.moved = true;
    applyDrag(drag, dx);
    paintDrag(drag);
  });
  document.addEventListener("mouseup", (e) => {
    if (trimDrag) {
      const d = trimDrag; trimDrag = null;
      document.body.style.cursor = "";
      hideSnap();
      if (trimGhost) { trimGhost.remove(); trimGhost = null; }
      if (d.dt < 0.04) { render(); return; }                // negligible
      const frames = d.side === "l"
        ? outDeltaToFrames(0, d.dt)
        : outDeltaToFrames(DATA.duration - d.dt, DATA.duration);
      if (frames <= 0) { render(); return; }
      note("Trimming…");
      runJob("/api/tool", { name: "nudge_edit", args: {
        clip_id: DATA.clip_id, edge: d.side === "l" ? "start" : "end",
        frames: d.side === "l" ? frames : -frames } }).then(async (job) => {
          const res = (job.result && job.result.result) || {};
          if (job.result && job.result.ok === false)
            note("✗ " + (res.error || "trim failed"));
          else if (res.notes && res.notes.length) note(res.notes.join(" "));
          await reloadState(); load(DATA.clip_id);
        });
      return;
    }
    if (!drag) return;
    const d = drag; drag = null;
    d.el.classList.remove("dragging");
    hideSnap();
    const dx = e.clientX - d.startX;
    if (Math.abs(dx) > 2) d.moved = true;
    if (!d.moved) {
      player.currentTime = Math.max(0, d.origStart); player.pause();
      return;
    }
    applyDrag(d, dx);
    const args = { clip_id: DATA.clip_id, stage: d.stage, index: d.idx,
                   start: d.preview.start };
    if (d.kind !== "point") args.end = d.preview.end;
    runJob("/api/tool", { name: "edit_event", args }).then(() => load(DATA.clip_id));
  });
  const ZOOM_MOTIONS = [
    { k: "center", label: "⊙ Static punch" }, { k: "left", label: "← Pan left" },
    { k: "right", label: "→ Pan right" }, { k: "up", label: "↑ Pan up" },
    { k: "down", label: "↓ Pan down" },
  ];
  body.addEventListener("contextmenu", (e) => {
    const el = e.target.closest(".tl-ed");
    if (el) {
      e.preventDefault();
      // zoom events get a Ken-Burns motion menu (+ delete); others quick-delete
      if (el.dataset.stage === "zoom") {
        const idx = +el.dataset.idx, cur = el.dataset.motion || "center";
        const setMotion = (m) => runJob("/api/tool", { name: "edit_event",
          args: { clip_id: DATA.clip_id, stage: "zoom", index: idx, motion: m } })
          .then(() => { reloadState(); load(DATA.clip_id); });
        openCtxMenu(e.clientX, e.clientY, [
          ...ZOOM_MOTIONS.map(m => ({
            label: (m.k === cur ? "● " : "  ") + m.label, fn: () => setMotion(m.k) })),
          { label: "Delete zoom", danger: true, fn: () =>
              runJob("/api/tool", { name: "delete_event", args: {
                clip_id: DATA.clip_id, stage: "zoom", index: idx } })
                .then(() => load(DATA.clip_id)) },
        ]);
        return;
      }
      runJob("/api/tool", { name: "delete_event", args: {
        clip_id: DATA.clip_id, stage: el.dataset.stage, index: +el.dataset.idx } })
        .then(() => load(DATA.clip_id));
      return;
    }
    // right-click on the main video track → segment context menu
    if (e.target.closest(".tl-mainrow") && DATA && !DATA.locked) {
      e.preventDefault();
      const rect = body.getBoundingClientRect();
      const t = Math.max(0, (e.clientX - rect.left) / pxPerSec);
      const seg = segments().find(s => t >= s.start && t < s.end) || null;
      openCtxMenu(e.clientX, e.clientY, [
        { label: "Split here", fn: () => {
            player.currentTime = t; window.tlSplit(); } },
        seg && { label: "Delete this segment", danger: true, fn: () => {
            SEL = seg; window.tlRippleDelete(); } },
        { label: "Add marker here", fn: () =>
            runJob("/api/tool", { name: "add_marker",
              args: { clip_id: DATA.clip_id, t } }).then(() => load(DATA.clip_id)) },
      ].filter(Boolean));
    }
  });

  /* --- double-click: add/cycle a zoom on the zoom row --- */
  const ZOOM_STEPS = [1.1, 1.2, 1.35, 1.5];
  body.addEventListener("dblclick", (e) => {
    if (!DATA) return;
    const rect = body.getBoundingClientRect();
    const el = e.target.closest(".tl-ed");
    if (el && el.dataset.stage === "zoom") {
      const it = (DATA.tracks.find(t => t.key === "zoom") || { items: [] })
        .items[+el.dataset.idx];
      const cur = (it && it.value) || 1.2;
      const next = ZOOM_STEPS.find(v => v > cur + 0.01) || ZOOM_STEPS[0];
      el.textContent = next.toFixed(2) + "×";
      runJob("/api/tool", { name: "edit_event", args: {
        clip_id: DATA.clip_id, stage: "zoom", index: +el.dataset.idx,
        value: next } }).then(() => load(DATA.clip_id));
      return;
    }
    if (el) return;
    const ri = Math.floor((e.clientY - rect.top - MAIN_H) / ROW_H);
    const tr = ri >= 0 ? DATA.tracks[ri] : null;
    if (tr && tr.key === "zoom") {
      const t = Math.max(0, (e.clientX - rect.left) / pxPerSec);
      runJob("/api/tool", { name: "add_zoom", args: {
        clip_id: DATA.clip_id, time: t } })
        .then(() => { load(DATA.clip_id); reloadState(); });
    }
  });

  /* --- sound palette drop target (app.js drives the mouse-drag) --- */
  const dropLine = document.createElement("div");
  dropLine.className = "tl-drop"; dropLine.style.display = "none";
  body.appendChild(dropLine);
  function _dropPos(cx, cy) {
    if (!shown || !DATA || cx == null) return null;
    const r = body.getBoundingClientRect();
    if (cy < r.top - 30 || cy > r.bottom + 30 || cx < r.left || cx > r.right)
      return null;
    return Math.max(0, (cx - r.left) / pxPerSec);
  }
  window.tlSoundHover = (cx, cy) => {
    const t = _dropPos(cx, cy);
    dock.classList.toggle("droptarget", t != null);
    dropLine.style.display = t == null ? "none" : "block";
    if (t != null) {
      dropLine.style.left = (t * pxPerSec) + "px";
      dropLine.style.height = totalH() + "px";
    }
  };
  window.tlSoundDrop = async (cx, cy, p) => {
    const t = _dropPos(cx, cy);
    if (t == null) return;
    const call = p.type === "music"
      ? { name: "set_music", args: { clip_id: DATA.clip_id, file: p.path } }
      : { name: "add_sound_effect",
          args: { clip_id: DATA.clip_id, time: t, kind: p.kind } };
    if (typeof addMsg === "function")
      addMsg("tool", `${call.name}(${p.name}` +
        (p.type === "sfx" ? ` @ ${t.toFixed(1)}s)` : ")"));
    const job = await runJob("/api/tool", call);
    const res = (job.result && job.result.result) || job.result || {};
    if (job.result && job.result.ok === false && typeof addMsg === "function")
      addMsg("bot", "✗ " + (res.error || "couldn't add"));
    await reloadState();
    load(DATA.clip_id);
  };

  scroll.addEventListener("wheel", (e) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const rect = body.getBoundingClientRect();
      const tAt = (e.clientX - rect.left) / pxPerSec;
      pxPerSec = Math.max(8, Math.min(400, pxPerSec * (e.deltaY < 0 ? 1.12 : 0.89)));
      render();
      scroll.scrollLeft = tAt * pxPerSec - (e.clientX - scroll.getBoundingClientRect().left);
    }
  }, { passive: false });

  /* ------------------------------------------------------- zoom + gestures */
  window.tlZoom = (dir) => {
    if (!DATA) return;
    pxPerSec = Math.max(8, Math.min(400, pxPerSec * (dir > 0 ? 1.25 : 0.8)));
    render();
  };
  window.tlFit = () => { if (!DATA) return; fitZoom(); render(); };
  window.tlToggleSnap = () => {
    SNAP_ON = !SNAP_ON;
    localStorage.setItem("kesim_snap", SNAP_ON ? "1" : "0");
    const b = $("tlSnap"); if (b) b.classList.toggle("on", SNAP_ON);
    note("Snapping " + (SNAP_ON ? "on" : "off"));
  };

  const SPEED_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2];
  function fmtSpeed(f) { return (Number.isInteger(f) ? f : f) + "×"; }
  window.tlSpeedMenu = (anchorEl) => {
    if (!DATA) return;
    if (DATA.locked) { note("Clip is locked — unlock to change speed."); return; }
    const cur = DATA.speed || 1;
    const r = (anchorEl || $("tlSpeed")).getBoundingClientRect();
    openCtxMenu(r.left, r.bottom + 2, SPEED_STEPS.map(f => ({
      label: (Math.abs(f - cur) < 1e-3 ? "● " : "  ") + fmtSpeed(f),
      fn: () => window.tlSetSpeed(f),
    })));
  };
  window.tlSetSpeed = (f) => {
    if (!DATA) return;
    if (Math.abs((DATA.speed || 1) - f) < 1e-3) return;
    note("Retiming…");
    runJob("/api/tool", { name: "set_speed",
      args: { clip_id: DATA.clip_id, factor: f } }).then(async (job) => {
        const res = (job.result && job.result.result) || {};
        if (job.result && job.result.ok === false)
          note("✗ " + (res.error || "speed failed"));
        else note(`Speed ${fmtSpeed(f)} — captions kept in sync`);
        await reloadState(); load(DATA.clip_id);
      });
  };

  window.tlSplit = () => {
    if (!DATA) return;
    if (DATA.locked) { note("Clip is locked — unlock to split."); return; }
    const fps = DATA.fps || 30;
    let t = Math.round((player.currentTime || 0) * fps) / fps;
    if (t < 0.1 || t > DATA.duration - 0.1) { note("Move the playhead inside the clip."); return; }
    if (splitTimes().some(s => Math.abs(s - t) < 0.1)) { note("Already split here."); return; }
    runJob("/api/tool", { name: "add_marker", args: {
      clip_id: DATA.clip_id, t, label: "split", color: "split" } })
      .then(() => load(DATA.clip_id));
  };

  window.tlRippleDelete = () => {
    if (!DATA) return;
    if (DATA.locked) { note("Clip is locked — unlock to delete."); return; }
    if (!SEL) { note("Select a segment first (split, then click it)."); return; }
    if (SEL.start <= 1e-3 && SEL.end >= DATA.duration - 1e-3) {
      note("Can't delete the whole clip."); return; }
    const sel = SEL;
    note("Removing…");
    runJob("/api/tool", { name: "remove_section", args: {
      clip_id: DATA.clip_id, start: sel.start, end: sel.end } })
      .then(async (job) => {
        const res = (job.result && job.result.result) || {};
        if (job.result && job.result.ok === false) {
          note("✗ " + (res.error || "couldn't remove")); return;
        }
        // drop now-stale split markers that fell inside the removed segment
        const stale = (DATA.markers || []).filter(m => m.color === "split" &&
          m.t > sel.start + 1e-3 && m.t < sel.end - 1e-3);
        for (const m of stale)
          await runJob("/api/tool", { name: "remove_marker",
            args: { clip_id: DATA.clip_id, marker_id: m.id } });
        if (res.notes && res.notes.length) note(res.notes.join(" "));
        SEL = null;
        await reloadState();
        load(DATA.clip_id);
      });
  };

  /* ----------------------------------------------------------- in / out */
  function paintInOut() {
    if (inPt == null && outPt == null) { inout.style.display = "none"; }
    else {
      const a = (inPt != null ? inPt : 0) * pxPerSec;
      const b = (outPt != null ? outPt : DATA ? DATA.duration : 0) * pxPerSec;
      inout.style.display = "block";
      inout.style.left = Math.min(a, b) + "px";
      inout.style.width = Math.abs(b - a) + "px";
      inout.style.height = totalH() + "px";
    }
    const canCut = inPt != null && outPt != null && outPt > inPt;
    $("tlCut").style.display = canCut ? "" : "none";
  }
  window.tlSetInOut = (i, o) => { inPt = i; outPt = o; paintInOut(); };
  $("tlCut").onclick = async () => {
    if (inPt == null || outPt == null || outPt <= inPt) return;
    const job = await runJob("/api/tool", { name: "remove_section",
      args: { clip_id: DATA.clip_id, start: inPt, end: outPt } });
    if (job.result && job.result.ok) {
      window.clearInOut && window.clearInOut();
      await reloadState();
    } else if (typeof addMsg === "function") {
      addMsg("bot", "✗ " + ((job.result && job.result.error) || "cut failed"));
    }
  };

  /* ----------------------------------------------------------- toolbar */
  async function updateHistoryButtons() {
    try {
      const h = await (await fetch("/api/history")).json();
      const u = $("tlUndo"), r = $("tlRedo");
      if (u) u.disabled = !(h.history && h.history.length);
      if (r) r.disabled = !(h.redo_depth);
    } catch (e) {}
  }
  function bindToolbar() {
    const on = (id, fn) => { const b = $(id); if (b) b.onclick = fn; };
    on("tlUndo", () => runJob("/api/tool", { name: "undo", args: {} })
      .then(() => { reloadState(); window.tlReload && window.tlReload(); }));
    on("tlRedo", () => runJob("/api/tool", { name: "redo", args: {} })
      .then(() => { reloadState(); window.tlReload && window.tlReload(); }));
    on("tlSplit", () => window.tlSplit());
    on("tlDelete", () => window.tlDelete());
    on("tlSnap", () => window.tlToggleSnap());
    on("tlZoomIn", () => window.tlZoom(1));
    on("tlZoomOut", () => window.tlZoom(-1));
    on("tlFit", () => window.tlFit());
    on("tlSpeed", (e) => window.tlSpeedMenu(e.currentTarget));
    const sb = $("tlSnap"); if (sb) sb.classList.toggle("on", SNAP_ON);
  }
  function updateSpeedButton() {
    const b = $("tlSpeed"); if (!b || !DATA) return;
    const f = DATA.speed || 1;
    b.textContent = (Number.isInteger(f) ? f : f) + "×";
    b.classList.toggle("on", Math.abs(f - 1) > 1e-3);
  }

  /* ----------------------------------------------------------- toggle + hooks */
  let userHid = false;
  function setShown(on) {
    shown = on;
    dock.classList.toggle("on", on);
    $("tlToggle").classList.toggle("on", on);
    if (on) {
      if (typeof activeClip !== "undefined" && activeClip != null) load(activeClip);
      else if (DATA) render();
    }
  }
  $("tlToggle").onclick = () => { userHid = shown; setShown(!shown); };
  bindToolbar();

  window.tlReload = () => { if (shown && DATA) load(DATA.clip_id); };
  window.addEventListener("kesim:clip", (e) => {
    const clip = e.detail;
    if (!clip || clip.comp) return;
    if (shown) load(clip.id);
  });
  window.addEventListener("kesim:ghost", (e) => {
    GHOST = e.detail;
    if (e.detail && !shown && !userHid) setShown(true);
    if (shown && DATA) render();
  });
  window.addEventListener("resize", () => { if (shown && DATA) render(); });
  window.addEventListener("keydown", (e) => { if (e.key === "Alt") ALT_HELD = true; });
  window.addEventListener("keyup", (e) => { if (e.key === "Alt") ALT_HELD = false; });
})();
