/* KESİM Studio — text-based editing (Pro Faz 2).
   Click a word → seek. Select a span → cut (remove_section). Filler chip →
   remove_fillers. Shares app.js globals (player, activeClip, setBusy, addMsg,
   reloadState) via the classic-script global scope. */

(() => {
  const $ = (id) => document.getElementById(id);
  const stage = $("stagewrap");
  const body = $("txtbody");
  const bar = $("txtbar");
  const tbInfo = $("tbInfo");
  const fillerBtn = $("thFiller");

  let WORDS = [];          // [{i,start,end,word,is_filler}]
  let CUTS = [];           // [{start,end,out_anchor,text,duration}] (source s)
  let showCuts = false;    // "show removed" toggle
  let curClip = null;
  let textMode = false;
  let sel = null;          // {anchor, focus} word indices, or null
  let dragging = false;
  let busy = false;
  const cutsBtn = $("thCuts");

  /* ---------------------------------------------------------- load + render */
  async function load(clipId) {
    if (clipId == null) return;
    curClip = clipId;
    try {
      const d = await (await fetch(`/api/transcript/${clipId}?cuts=1`)).json();
      if (d.error) { body.innerHTML = `<div class="txt-empty mono">${d.error}</div>`; return; }
      WORDS = d.words || [];
      CUTS = d.cuts || [];
      render();
    } catch (e) {
      body.innerHTML = `<div class="txt-empty mono">couldn't load transcript</div>`;
    }
  }

  function render() {
    sel = null; updateBar();
    if (!WORDS.length) {
      body.innerHTML = `<div class="txt-empty mono">no speech in this clip</div>`;
      fillerBtn.style.display = "none";
      return;
    }
    const frag = document.createDocumentFragment();
    // pending cut chips, sorted by their seam position in player time
    const cuts = showCuts
      ? [...CUTS].sort((a, b) => a.out_anchor - b.out_anchor) : [];
    let ci = 0;
    const emitCutsBefore = (t) => {
      while (ci < cuts.length && cuts[ci].out_anchor <= t + 0.01) {
        frag.appendChild(cutChip(cuts[ci])); ci++;
        frag.appendChild(document.createTextNode(" "));
      }
    };
    WORDS.forEach((w) => {
      emitCutsBefore(w.start);
      const s = document.createElement("span");
      s.className = "w" + (w.is_filler ? " filler" : "");
      s.dataset.i = w.i;
      s.textContent = w.word;
      frag.appendChild(s);
      frag.appendChild(document.createTextNode(" "));
    });
    emitCutsBefore(Infinity);
    body.innerHTML = "";
    body.appendChild(frag);
    const nf = WORDS.filter((w) => w.is_filler).length;
    if (nf) {
      fillerBtn.style.display = "";
      fillerBtn.textContent = `⌫ clean fillers (${nf})`;
    } else {
      fillerBtn.style.display = "none";
    }
    if (CUTS.length) {
      cutsBtn.style.display = "";
      cutsBtn.classList.toggle("on", showCuts);
      cutsBtn.textContent = `✂ removed (${CUTS.length})`;
    } else {
      cutsBtn.style.display = "none";
    }
  }

  function cutChip(c) {
    const chip = document.createElement("span");
    chip.className = "cutchip";
    chip.textContent = c.text
      ? (c.text.length > 42 ? c.text.slice(0, 40) + "…" : c.text)
      : `${c.duration}s silence`;
    chip.title = `removed · ${c.duration}s · click → restore`;
    chip.onclick = async (e) => {
      e.stopPropagation();
      if (busy) return;
      setBusyLocal(true, "restoring…");
      const r = await apiTool("restore_section",
        { clip_id: curClip, start: c.start, end: c.end });
      if (r.error || (r.result && !r.result.ok)) {
        typeof addMsg === "function" &&
          addMsg("bot", "✗ " + (r.error || r.result.error));
        setBusyLocal(false);
        return;
      }
      typeof addMsg === "function" &&
        addMsg("tool", `restore_section(${c.start.toFixed(1)}–${c.end.toFixed(1)}s)`);
      await window.reloadState();
      setBusyLocal(false);
    };
    return chip;
  }

  const spanAt = (i) => body.querySelector(`.w[data-i="${i}"]`);

  /* ---------------------------------------------------------- selection */
  function paintSelection() {
    body.querySelectorAll(".w.sel").forEach((e) => e.classList.remove("sel"));
    if (!sel) return;
    const lo = Math.min(sel.anchor, sel.focus), hi = Math.max(sel.anchor, sel.focus);
    for (let i = lo; i <= hi; i++) spanAt(i)?.classList.add("sel");
  }

  function updateBar() {
    paintSelection();
    if (!sel) { bar.classList.remove("on"); return; }
    const lo = Math.min(sel.anchor, sel.focus), hi = Math.max(sel.anchor, sel.focus);
    const n = hi - lo + 1;
    const dur = (WORDS[hi].end - WORDS[lo].start);
    tbInfo.textContent = `${n} words · ${dur.toFixed(1)}s selected`;
    bar.classList.add("on");
  }

  function wordIndexFromEvent(e) {
    const t = e.target.closest(".w");
    return t ? parseInt(t.dataset.i, 10) : null;
  }

  body.addEventListener("pointerdown", (e) => {
    if (busy) return;
    const i = wordIndexFromEvent(e);
    if (i == null) return;
    dragging = true;
    sel = { anchor: i, focus: i };
    updateBar();
    e.preventDefault();
  });

  body.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const i = wordIndexFromEvent(e);
    if (i == null) return;
    sel.focus = i;
    updateBar();
  });

  window.addEventListener("pointerup", () => {
    if (!dragging) return;
    dragging = false;
    if (sel && sel.anchor === sel.focus) {
      // a plain click — seek, don't select
      const w = WORDS[sel.anchor];
      if (w && typeof player !== "undefined") {
        player.currentTime = w.start + 0.001;
        if (player.paused) player.play().catch(() => {});
      }
      sel = null;
      updateBar();
    }
  });

  $("tbCancel").onclick = () => { sel = null; updateBar(); };

  cutsBtn.onclick = () => { showCuts = !showCuts; render(); };

  /* ---------------------------------------------------------- active word */
  function highlightActive(t) {
    if (!WORDS.length) return;
    let lo = 0, hi = WORDS.length - 1, found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (t < WORDS[mid].start) hi = mid - 1;
      else if (t > WORDS[mid].end) lo = mid + 1;
      else { found = mid; break; }
    }
    const prev = body.querySelector(".w.active");
    if (prev && prev.dataset.i == found) return;
    prev && prev.classList.remove("active");
    if (found >= 0) {
      const el = spanAt(found);
      if (el) {
        el.classList.add("active");
        if (textMode) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  }

  /* ---------------------------------------------------------- edits */
  async function apiTool(name, args) {
    // Async via the job queue + SSE (window.runJob from app.js). The resolved
    // job's .result is the same {ok, result, clips, ...} the sync path returns.
    try {
      const job = await window.runJob("/api/tool", { name, args });
      if (job.status === "cancelled") return { cancelled: true };
      return job.result || {};
    } catch (e) {
      return { error: e.message };
    }
  }

  $("tbCut").onclick = async () => {
    if (!sel || busy) return;
    const lo = Math.min(sel.anchor, sel.focus), hi = Math.max(sel.anchor, sel.focus);
    const start = WORDS[lo].start, end = WORDS[hi].end;
    // optimistic ghost: strike the words about to go
    for (let i = lo; i <= hi; i++) spanAt(i)?.classList.add("ghost");
    setBusyLocal(true, "cutting…");
    const r = await apiTool("remove_section", { clip_id: curClip, start, end });
    if (r.error || (r.result && !r.result.ok)) {
      const msg = r.error || r.result.error;
      typeof addMsg === "function" && addMsg("bot", "✗ " + msg);
      for (let i = lo; i <= hi; i++) spanAt(i)?.classList.remove("ghost");
      setBusyLocal(false);
      return;
    }
    typeof addMsg === "function" &&
      addMsg("tool", `remove_section(${start.toFixed(1)}–${end.toFixed(1)}s)`);
    await window.reloadState();           // re-renders player + reloads transcript
    setBusyLocal(false);
  };

  fillerBtn.onclick = async () => {
    if (busy) return;
    setBusyLocal(true, "cleaning fillers…");
    const r = await apiTool("remove_fillers", { clip_id: curClip });
    if (r.error || (r.result && !r.result.ok)) {
      typeof addMsg === "function" && addMsg("bot", "✗ " + (r.error || r.result.error));
      setBusyLocal(false);
      return;
    }
    typeof addMsg === "function" && addMsg("tool", "remove_fillers()");
    await window.reloadState();
    setBusyLocal(false);
  };

  function setBusyLocal(b, label) {
    busy = b;
    stage.classList.toggle("txtbusy", b);
    if (typeof setBusy === "function") setBusy(b, label);
  }

  /* ---------------------------------------------------------- mode toggle */
  function setTextMode(on) {
    textMode = on;
    stage.classList.toggle("withtext", on);
    $("txtToggle").classList.toggle("on", on);
    if (on && curClip != null && !WORDS.length) load(curClip);
    if (on && curClip == null && typeof activeClip !== "undefined" && activeClip != null)
      load(activeClip);
  }

  $("txtToggle").onclick = () => {
    // text mode and A/B compare are mutually exclusive
    if (!textMode && stage.classList.contains("compare")) return;
    setTextMode(!textMode);
  };

  /* ---------------------------------------------------------- app.js hooks */
  window.addEventListener("kesim:clip", (e) => {
    const clip = e.detail;
    if (!clip || clip.comp) return;
    load(clip.id);
  });
  function onTime() { highlightActive(player.currentTime); }

  // Attach the active-word tracker once — `player` exists because app.js (which
  // declares it) is a classic script loaded before this one.
  if (typeof player !== "undefined")
    player.addEventListener("timeupdate", onTime);
})();
