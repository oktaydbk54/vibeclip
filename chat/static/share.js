/* Share-to-social modal (Phase 1).
   Opened from the deliver cluster's ↗ SHARE button (wired in app.js playClip).
   Flow: pick connected account(s) → choose post type → caption → PUBLISH, which
   POSTs /api/social/share and watches the share rows until each destination
   lands. Publishing only ever happens from the explicit Publish click. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);

  // Every platform the Zernio account can connect (verified live). X/Twitter is
  // intentionally absent — the provider can't connect it. The free plan limits
  // the *number of connected accounts* (2), NOT which platforms are available.
  const PLATFORMS = [
    "instagram", "youtube", "tiktok", "linkedin",
    "facebook", "threads", "pinterest", "bluesky",
  ];
  const PLATFORM_LABEL = {
    instagram: "Instagram", linkedin: "LinkedIn", facebook: "Facebook",
    tiktok: "TikTok", youtube: "YouTube", threads: "Threads",
    pinterest: "Pinterest", bluesky: "Bluesky", x: "X",
  };
  // Brand accent (drives hover glow + placeholder-avatar background).
  const BRAND = {
    instagram: "#E1306C", youtube: "#FF0000", tiktok: "#25F4EE",
    linkedin: "#0A66C2", facebook: "#1877F2", threads: "#ffffff",
    pinterest: "#E60023", bluesky: "#0085FF", x: "#1d1d1f",
  };
  // Brand glyphs as self-contained 24×24 SVG tiles (white mark on brand fill).
  const ICONS = {
    instagram: `<svg viewBox="0 0 24 24"><defs><linearGradient id="smIg" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#feda75"/><stop offset=".45" stop-color="#d62976"/><stop offset="1" stop-color="#4f5bd5"/></linearGradient></defs><rect x="1" y="1" width="22" height="22" rx="6.5" fill="url(#smIg)"/><rect x="6" y="6" width="12" height="12" rx="4" fill="none" stroke="#fff" stroke-width="1.7"/><circle cx="12" cy="12" r="2.7" fill="none" stroke="#fff" stroke-width="1.7"/><circle cx="16.3" cy="7.7" r="1" fill="#fff"/></svg>`,
    youtube: `<svg viewBox="0 0 24 24"><rect x="1" y="4" width="22" height="16" rx="5" fill="#FF0000"/><path d="M10 8.3l6 3.7-6 3.7z" fill="#fff"/></svg>`,
    tiktok: `<svg viewBox="0 0 24 24"><rect x="1" y="1" width="22" height="22" rx="6.5" fill="#010101"/><path d="M14.2 5c.2 1.9 1.4 3.1 3.3 3.3v2.3c-1.1 0-2.2-.3-3.1-.9v4.6a4.1 4.1 0 1 1-4.1-4.1c.25 0 .5 0 .75.06v2.4a1.85 1.85 0 1 0 1.3 1.77V5z" fill="#fff"/></svg>`,
    linkedin: `<svg viewBox="0 0 24 24"><rect x="1" y="1" width="22" height="22" rx="5" fill="#0A66C2"/><circle cx="6.4" cy="6.6" r="1.6" fill="#fff"/><rect x="5.1" y="9.4" width="2.6" height="8.5" fill="#fff"/><path d="M9.6 9.4h2.5v1.2c.4-.7 1.3-1.4 2.7-1.4 2.1 0 3.3 1.3 3.3 3.8v4.9h-2.6v-4.4c0-1.1-.4-1.9-1.4-1.9s-1.6.7-1.6 1.9v4.4H9.6z" fill="#fff"/></svg>`,
    facebook: `<svg viewBox="0 0 24 24"><rect x="1" y="1" width="22" height="22" rx="6.5" fill="#1877F2"/><path d="M14.3 22v-7.3h2.4l.4-2.9h-2.8v-1.8c0-.8.3-1.4 1.5-1.4h1.4V5.9c-.3 0-1.3-.1-2.4-.1-2.4 0-4 1.4-4 4v2.1H8.6v2.9h2.6V22z" fill="#fff"/></svg>`,
    threads: `<svg viewBox="0 0 24 24"><rect x="1" y="1" width="22" height="22" rx="6.5" fill="#010101"/><path d="M16.3 11.5c-.1 0-.15-.05-.25-.06-.18-3.3-2-5.2-5-5.2-1.85 0-3.4.78-4.34 2.2l1.55 1.06c.7-1.06 1.8-1.28 2.8-1.28 1.55 0 2.7.9 2.85 2.55-.66-.15-1.36-.2-2.1-.16-2.4.14-3.95 1.36-3.85 3.34.05 1 .55 1.86 1.4 2.42.72.47 1.65.7 2.62.65 1.28-.07 2.28-.56 2.98-1.46.53-.68.86-1.55 1-2.65.6.36 1.05.84 1.3 1.42.43.99.46 2.6-.86 3.92-1.16 1.15-2.55 1.65-4.65 1.67-2.33-.02-4.1-.77-5.25-2.24C7.4 16.3 6.85 14.4 6.83 12c.02-2.4.57-4.3 1.62-5.65C9.6 4.88 11.37 4.13 13.7 4.1c2.35.02 4.13.78 5.3 2.26.57.73 1 1.65 1.3 2.74l1.83-.49c-.35-1.34-.9-2.5-1.65-3.46C20 3.2 17.65 2.2 14.66 2.18h-.02c-2.98.02-5.3 1.03-6.9 3-1.42 1.76-2.16 4.2-2.18 7.26v.02c.02 3.06.76 5.5 2.18 7.26 1.6 1.97 3.92 2.98 6.9 3h.02c2.65-.02 4.52-.72 6.06-2.26 2.02-2.02 1.96-4.55 1.3-6.1-.48-1.12-1.4-2.03-2.66-2.64a8 8 0 0 0-.36-.18z" fill="#fff"/></svg>`,
    pinterest: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" fill="#E60023"/><path d="M12.4 5.5c-3.7 0-5.6 2.4-5.6 4.7 0 1.4.6 2.6 1.7 3 .2.1.4 0 .4-.2l.15-.6c.05-.2.03-.27-.12-.45-.32-.38-.53-.87-.53-1.56 0-2 1.55-3.8 4.05-3.8 2.2 0 3.42 1.3 3.42 3.05 0 2.3-1.04 4.24-2.58 4.24-.85 0-1.5-.7-1.29-1.57.25-1.03.73-2.14.73-2.88 0-.66-.36-1.22-1.1-1.22-.87 0-1.58.9-1.58 2.12 0 .77.27 1.3.27 1.3l-1.06 4.45c-.3 1.3-.04 2.9-.02 3.06 0 .1.13.12.18.05.08-.1 1.1-1.36 1.45-2.62.1-.36.57-2.2.57-2.2.28.53 1.1.99 1.97.99 2.6 0 4.36-2.36 4.36-5.52 0-2.4-2.03-4.63-5.12-4.63z" fill="#fff"/></svg>`,
    bluesky: `<svg viewBox="0 0 24 24"><rect x="1" y="1" width="22" height="22" rx="6.5" fill="#0085FF"/><path d="M12 11.1C10.9 9 8.1 6.6 6.7 6.9c-1 .25-.7 2.1-.4 3.4.3 1.3 1.3 4 4 4.3-2.6.45-3.2 2.2-2.1 3.3.85.85 2.4.2 3.8-2.6 1.4 2.8 2.95 3.45 3.8 2.6 1.1-1.1.5-2.85-2.1-3.3 2.7-.3 3.7-3 4-4.3.3-1.3.6-3.15-.4-3.4-1.4-.3-4.2 2.1-5.3 4.2z" fill="#fff"/></svg>`,
  };
  // Soft per-platform caption guidance (counter turns amber past it).
  const CAPTION_SOFT = { instagram: 2200, linkedin: 3000, tiktok: 2200,
    youtube: 5000, threads: 500, pinterest: 500, bluesky: 300, facebook: 5000 };

  let CFG = null;                 // /api/social/status payload
  let ACCOUNTS = [];              // connected accounts
  const picked = new Set();       // selected account ids
  let kind = "post";
  let clip = null;                // {id, title, url, export_url}
  let polling = false;

  async function openShareModal(clipId, c) {
    clip = c || { id: clipId };
    picked.clear();
    kind = "post";
    $("smIssues").innerHTML = "";
    $("smStatus").innerHTML = "";
    $("smCaption").value = "";
    const dlg = $("shareModal");
    if (!dlg.open) dlg.showModal();
    // preview: prefer the full-res export, fall back to the proxy preview
    const src = (clip.export_url || clip.url || `/media/${clip.id}`) +
      "?t=" + Date.now();
    const v = $("smPreview"); v.src = src; v.play().catch(() => {});
    $("smClip").textContent = "#" + clip.id + " — " + (clip.title || "");
    await refresh();
  }
  window.openShareModal = openShareModal;

  async function refresh() {
    try {
      const s = await (await fetch("/api/social/status")).json();
      CFG = s;
    } catch (e) { CFG = { enabled: false }; }
    if (!CFG.enabled) { renderDisabled(); return; }
    await loadAccounts();
    renderAccounts();
    renderConnect();
    renderKinds();
    bindCaption();
    updatePublish();
    const hint = $("smPlanHint");
    if (hint) hint.textContent = ACCOUNTS.length
      ? `${ACCOUNTS.length} connected` : "8 platforms";
  }

  function renderDisabled() {
    $("smAccounts").innerHTML =
      `<div class="sm-empty">Sharing isn't configured yet. Add ` +
      `<code>ZERNIO_API_KEY</code> to <code>.env</code> ` +
      `(free key at zernio.com), then restart the studio.</div>`;
    $("smConnect").innerHTML = "";
    $("smKinds").innerHTML = "";
    $("smPublish").disabled = true;
  }

  async function loadAccounts() {
    try {
      const d = await (await fetch("/api/social/accounts")).json();
      ACCOUNTS = d.accounts || [];
      if (d.warning) setStatus(d.warning, "warn");
    } catch (e) { ACCOUNTS = []; }
  }

  function avatar(a) {
    if (a.avatar_url)
      return `<img class="sm-av" src="${a.avatar_url}" alt="" ` +
        `onerror="this.style.display='none'">`;
    const ch = (a.display_name || PLATFORM_LABEL[a.platform] || "?")[0]
      .toUpperCase();
    const bg = BRAND[a.platform] || "var(--accent-dim)";
    return `<span class="sm-av sm-av-ph" style="background:${bg}">${ch}</span>`;
  }

  function renderAccounts() {
    const box = $("smAccounts");
    if (!ACCOUNTS.length) {
      box.innerHTML = `<div class="sm-empty">No accounts connected yet — ` +
        `pick a platform below to connect one.</div>`;
      return;
    }
    box.innerHTML = ACCOUNTS.map(a => {
      const on = picked.has(a.id) ? " on" : "";
      const lbl = a.display_name || PLATFORM_LABEL[a.platform] || a.platform;
      return `<button class="sm-acct${on}" data-id="${a.id}">` +
        `${avatar(a)}<span class="sm-acct-meta">` +
        `<span class="sm-acct-nm">${lbl}</span>` +
        `<span class="sm-acct-pl">${PLATFORM_LABEL[a.platform] ||
          a.platform}</span></span>` +
        `<span class="sm-acct-x" data-x="${a.id}" title="Disconnect">✕` +
        `</span></button>`;
    }).join("");
    box.querySelectorAll(".sm-acct").forEach(el => {
      el.onclick = (e) => {
        if (e.target.dataset.x) return;        // the ✕ handles itself
        const id = +el.dataset.id;
        if (picked.has(id)) picked.delete(id); else picked.add(id);
        renderAccounts(); renderKinds(); updatePublish();
      };
    });
    box.querySelectorAll(".sm-acct-x").forEach(x => {
      x.onclick = async (e) => {
        e.stopPropagation();
        const id = +x.dataset.x;
        await fetch(`/api/social/accounts/${id}`, { method: "DELETE" });
        picked.delete(id);
        await loadAccounts(); renderAccounts(); renderKinds(); updatePublish();
      };
    });
  }

  function renderConnect() {
    // show a connect tile for every platform not already connected
    const have = new Set(ACCOUNTS.map(a => a.platform));
    const todo = PLATFORMS.filter(p => !have.has(p));
    $("smConnect").innerHTML = todo.map(p =>
      `<button class="sm-link" data-pl="${p}" style="--brand:${BRAND[p]}">` +
      `${ICONS[p] || ""}<span>${PLATFORM_LABEL[p]}</span></button>`).join("");
    $("smConnect").querySelectorAll(".sm-link").forEach(b => {
      b.onclick = () => connect(b.dataset.pl);
    });
  }

  async function connect(platform) {
    setStatus("Opening " + (PLATFORM_LABEL[platform] || platform) +
      " connect…", "");
    try {
      const r = await fetch("/api/social/connect", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform }),
      });
      const d = await r.json();
      if (d.error) { setStatus(d.error, "err"); return; }
      window.open(d.url, "_blank", "noopener");
      setStatus("Authorize in the new tab, then come back — " +
        "we'll detect it.", "");
      // re-sync when the user returns to the studio tab
      const onFocus = async () => {
        window.removeEventListener("focus", onFocus);
        await loadAccounts(); renderAccounts(); renderKinds(); updatePublish();
        if (ACCOUNTS.length) setStatus("", "");
      };
      window.addEventListener("focus", onFocus);
    } catch (e) { setStatus("Network error.", "err"); }
  }

  // kinds available across the selected accounts (intersection of each
  // platform's supported kinds); default to the first.
  function allowedKinds() {
    const sel = ACCOUNTS.filter(a => picked.has(a.id));
    if (!sel.length || !CFG || !CFG.kinds) return ["post"];
    let inter = null;
    sel.forEach(a => {
      const ks = CFG.kinds[a.platform] || ["post"];
      inter = inter === null ? ks.slice() : inter.filter(k => ks.includes(k));
    });
    return (inter && inter.length) ? inter : ["post"];
  }

  function renderKinds() {
    const ks = allowedKinds();
    if (!ks.includes(kind)) kind = ks[0];
    $("smKinds").innerHTML = ks.map(k =>
      `<button class="sm-kind${k === kind ? " on" : ""}" data-k="${k}">` +
      `${k.toUpperCase()}</button>`).join("");
    $("smKinds").querySelectorAll(".sm-kind").forEach(b => {
      b.onclick = () => { kind = b.dataset.k; renderKinds(); };
    });
  }

  function bindCaption() {
    const t = $("smCaption");
    t.oninput = () => {
      const sel = ACCOUNTS.filter(a => picked.has(a.id));
      const soft = Math.min(...(sel.length
        ? sel.map(a => CAPTION_SOFT[a.platform] || 2200) : [2200]));
      const n = t.value.length;
      const c = $("smCount");
      c.textContent = n + (soft ? " / " + soft : "");
      c.classList.toggle("over", soft && n > soft);
    };
    t.oninput();
  }

  function updatePublish() {
    const n = picked.size;
    const btn = $("smPublish");
    btn.disabled = n === 0 || polling;
    btn.textContent = n > 1 ? `PUBLISH TO ${n} ACCOUNTS` : "PUBLISH";
  }

  function setStatus(msg, cls) {
    const el = $("smStatus");
    el.className = "sm-status" + (cls ? " sm-" + cls : "");
    el.textContent = msg || "";
  }

  async function publish() {
    if (!picked.size || polling) return;
    $("smIssues").innerHTML = "";
    polling = true; updatePublish();
    setStatus("Publishing…", "");
    let r, d;
    try {
      r = await fetch("/api/social/share", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clip_id: clip.id, account_ids: [...picked], kind,
          caption: $("smCaption").value,
        }),
      });
      d = await r.json();
    } catch (e) { polling = false; updatePublish();
      setStatus("Network error.", "err"); return; }

    if (r.status === 422 && d.issues) {     // destinations rejected pre-flight
      $("smIssues").innerHTML = `<div class="sm-issue-h">Can't publish:</div>` +
        Object.entries(d.issues).map(([name, list]) =>
          `<div class="sm-issue"><b>${name}</b>: ${list.join(" ")}</div>`
        ).join("");
      polling = false; updatePublish(); setStatus("", ""); return;
    }
    if (d.error) { polling = false; updatePublish();
      setStatus(d.error, "err"); return; }

    setStatus("Uploading & posting… (this exports full-res first)", "");
    await watchShares(d.share_ids || []);
    polling = false; updatePublish();
  }

  // Poll the share rows until every destination leaves the publishing state.
  async function watchShares(ids) {
    const want = new Set(ids);
    for (let i = 0; i < 120 && want.size; i++) {           // ~3 min budget
      await new Promise(res => setTimeout(res, 1500));
      let rows;
      try {
        const d = await (await fetch(
          "/api/social/shares?clip_id=" + clip.id)).json();
        rows = d.shares || [];
      } catch (e) { continue; }
      const mine = rows.filter(x => want.has(x.id));
      renderResults(mine);
      mine.forEach(x => {
        if (x.status !== "publishing") want.delete(x.id);
      });
    }
    if (!want.size) setStatus("Done.", "ok");
  }

  function renderResults(rows) {
    if (!rows.length) return;
    $("smStatus").innerHTML = "";
    const el = $("smIssues");
    el.innerHTML = `<div class="sm-issue-h">Results</div>` + rows.map(x => {
      const name = x.account_name || x.platform;
      if (x.status === "published" || x.status === "scheduled") {
        const link = x.post_url
          ? ` <a href="${x.post_url}" target="_blank">view ↗</a>` : "";
        return `<div class="sm-res ok">✓ <b>${name}</b> — ${x.status}${link}` +
          `</div>`;
      }
      if (x.status === "failed")
        return `<div class="sm-res err">✕ <b>${name}</b> — ${x.error ||
          "failed"}</div>`;
      return `<div class="sm-res">… <b>${name}</b> — ${x.status}</div>`;
    }).join("");
  }

  document.addEventListener("DOMContentLoaded", () => {
    const x = $("smClose");
    if (x) x.onclick = () => { const v = $("smPreview"); v.pause(); v.src = "";
      $("shareModal").close(); };
    const p = $("smPublish");
    if (p) p.onclick = publish;
    const dlg = $("shareModal");
    if (dlg) dlg.addEventListener("click", (e) => {   // click backdrop = close
      if (e.target === dlg) { const v = $("smPreview"); v.pause(); v.src = "";
        dlg.close(); }
    });
  });
})();
