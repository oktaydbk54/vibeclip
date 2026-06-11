/* KESİM landing — auth-aware nav, scroll reveal, interactive mini-studio. */
(function () {
  "use strict";

  /* ---- auth-aware nav: swap CTAs if already logged in ---- */
  (async function () {
    try {
      const r = await fetch("/api/me");
      if (!r.ok) return;
      const cta = document.getElementById("navCta");
      if (cta) {
        cta.innerHTML =
          '<a href="/studio" class="btn btn-solid">GO TO STUDIO →</a>';
      }
    } catch (e) {
      /* backend not live while building — ignore */
    }
  })();

  /* ---- scroll reveal ---- */
  const io = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add("in");
          io.unobserve(en.target);
        }
      });
    },
    { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
  );
  document.querySelectorAll(".reveal").forEach(function (el) {
    io.observe(el);
  });
  // Safety: IO can miss above-the-fold elements when it observes before the
  // first paint (then never re-fires without a scroll) — so the hero would
  // load blank. Reveal anything already in view right after layout settles.
  requestAnimationFrame(function () {
    document.querySelectorAll(".reveal:not(.in)").forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (r.top < window.innerHeight && r.bottom > 0) {
        el.classList.add("in");
        io.unobserve(el);
      }
    });
  });

  /* ---- interactive studio demo: REAL pre-rendered A/B swap ---- */
  (function () {
    var root = document.getElementById("try");
    if (!root) return;

    var stage = document.getElementById("tryStage");
    var videoA = document.getElementById("demoA");
    var videoB = document.getElementById("demoB");
    var abAlabel = document.getElementById("abAlabel");
    var abBlabel = document.getElementById("abBlabel");
    var pickA = document.getElementById("pickA");
    var pickB = document.getElementById("pickB");
    var shimB = document.getElementById("shimB");
    var playA = document.getElementById("playA");
    var soundA = document.getElementById("soundA");
    var soundB = document.getElementById("soundB");
    var log = document.getElementById("tcLog");
    var empty = document.getElementById("tcEmpty");
    var line = document.getElementById("tcLine");
    var chipsWrap = document.getElementById("tcChips");
    var chips = Array.prototype.slice.call(chipsWrap.querySelectorAll(".chip"));
    var applied = document.getElementById("tcApplied");

    // 10 reachable states -> 10 real renders. Cache-busted with ?v=2
    // (bumped when the demo source footage was replaced — forces fresh fetch).
    var V = "?v=2";
    var FILES = {
      base: "/static/demo/demo_base.mp4" + V,
      s: "/static/demo/demo_s.mp4" + V,
      c: "/static/demo/demo_c.mp4" + V,
      p: "/static/demo/demo_p.mp4" + V,
      sc: "/static/demo/demo_sc.mp4" + V,
      sp: "/static/demo/demo_sp.mp4" + V,
      cp: "/static/demo/demo_cp.mp4" + V,
      scp: "/static/demo/demo_scp.mp4" + V,
      mb: "/static/demo/demo_mb.mp4" + V,
      mbp: "/static/demo/demo_mbp.mp4" + V,
    };

    // Truthful tool lines — name the REAL pipeline calls that produced each file.
    var COMMANDS = {
      mrbeast: { text: "make clip 1 mrbeast style",
        tool: "apply_style(clip_id=1, style=mrbeast)",
        label: "B — MRBEAST STYLE" },
      silence: { text: "cut the silences",
        tool: "cut_silences(clip_id=1, max_pause=0.4)",
        label: "B — SILENCES CUT" },
      captions: { text: "add captions",
        tool: "set_subtitles(clip_id=1, karaoke=true)",
        label: "B — CAPTIONS ADDED" },
      punchier: { text: "make it punchier",
        tool: "auto_zoom(density=0.35) + auto_pace(max_static=3.0)",
        label: "B — PUNCHED UP" },
    };

    // state is a SET; canonical key so click-order doesn't matter.
    var state = { style: null, s: false, c: false, p: false };

    function keyOf(st) {
      if (st.style === "mrbeast") return st.p ? "mbp" : "mb";  // mb subsumes s+c
      var k = (st.s ? "s" : "") + (st.c ? "c" : "") + (st.p ? "p" : "");
      return k || "base";
    }

    // Apply a command onto a COPY of state, returning the next state (or null
    // if it's a no-op — already included / subsumed).
    function nextState(cmd, st) {
      var n = { style: st.style, s: st.s, c: st.c, p: st.p };
      if (cmd === "mrbeast") {
        if (n.style === "mrbeast") return null;
        n.style = "mrbeast"; n.s = true; n.c = true; return n;  // subsumes s+c
      }
      if (cmd === "silence") {
        if (n.style === "mrbeast" || n.s) return null;
        n.s = true; return n;
      }
      if (cmd === "captions") {
        if (n.style === "mrbeast" || n.c) return null;
        n.c = true; return n;
      }
      if (cmd === "punchier") {
        if (n.p) return null;
        n.p = true; return n;
      }
      return null;
    }

    // Real output durations (seconds, rounded) — kept truthful per the footnote.
    var DUR = { base: "0:14", s: "0:13", c: "0:14", p: "0:14",
      sc: "0:13", sp: "0:13", cp: "0:14", scp: "0:13",
      mb: "0:13", mbp: "0:13" };

    function labelA() {
      var k = keyOf(state);
      abAlabel.textContent = (k === "base" ? "A — ORIGINAL · " : "A — CURRENT · ")
        + DUR[k];
    }

    var running = false;
    var pendingKey = null;   // the next-state key staged in B
    var t = [];
    function at(ms, fn) { t.push(setTimeout(fn, ms)); }

    function addMsg(cls, html) {
      if (empty) { empty.style.display = "none"; }
      var d = document.createElement("div");
      d.className = "tc-msg " + cls;
      d.innerHTML = html;
      log.appendChild(d);
      log.scrollTop = log.scrollHeight;
      return d;
    }

    // typewriter into the composer line (reused verbatim from the old IIFE)
    function typeLine(text, done) {
      var i = 0;
      (function tick() {
        if (i <= text.length) {
          line.innerHTML = text.slice(0, i) + '<span class="tc-caret"></span>';
          i++;
          at(38, tick);
        } else {
          at(260, done);
        }
      })();
    }

    // --- chip availability reflects the state set (incl. mrbeast subsumption) ---
    function syncChips() {
      chips.forEach(function (c) {
        var cmd = c.dataset.cmd;
        if (cmd === "reset") { c.disabled = running; c.classList.remove("done"); return; }
        c.classList.remove("busy");
        var subsumed = state.style === "mrbeast"
          && (cmd === "silence" || cmd === "captions");
        var already = (cmd === "mrbeast" && state.style === "mrbeast")
          || (cmd === "silence" && state.s)
          || (cmd === "captions" && state.c)
          || (cmd === "punchier" && state.p);
        var done = subsumed || already;
        c.classList.toggle("done", done);
        if (done) {
          c.disabled = true;
          if (subsumed) c.textContent = "included in style";
        } else {
          c.disabled = running;
        }
      });
    }

    function setChipLabels() {
      chips.forEach(function (c) {
        var cmd = c.dataset.cmd;
        if (COMMANDS[cmd]) c.textContent = COMMANDS[cmd].text;
        else if (cmd === "reset") c.textContent = "↺ reset demo";
      });
    }

    // --- audio: only one phone unmuted at a time; everything muted for autoplay ---
    function setSound(which) {
      var aOn = which === "A", bOn = which === "B";
      videoA.muted = !aOn; videoB.muted = !bOn;
      soundA.classList.toggle("on", aOn);
      soundB.classList.toggle("on", bOn);
    }

    // --- A autoplay with graceful tap fallback ---
    function playA_() {
      videoA.play().then(function () { playA.hidden = true; })
        .catch(function () { playA.hidden = false; });
    }
    playA.addEventListener("click", function () {
      playA.hidden = true; videoA.play().catch(function () {});
    });
    // Robust autoplay for A: muted playback needs no gesture, but applyB/reset
    // call videoA.load() which ABORTS the in-flight play() (promise rejects ->
    // the tap overlay would wrongly latch on, darkening a frame that then plays
    // anyway and trapping the mute button under it). These two listeners keep it
    // honest: retry play() once the new src is ready, and hide the overlay the
    // instant real playback starts.
    videoA.addEventListener("playing", function () { playA.hidden = true; });
    videoA.addEventListener("canplay", function () {
      videoA.play().then(function () { playA.hidden = true; })
        .catch(function () { playA.hidden = false; });
    });

    // ============================ COMPARE ============================
    function enterCompare(nextKey, cmd) {
      pendingKey = nextKey;
      abBlabel.textContent = cmd.label + " · " + DUR[nextKey];
      // B buffers; shimmer until canplaythrough (cap 2.5s), then show compare.
      shimB.hidden = false;
      var revealed = false;
      function reveal() {
        if (revealed) return; revealed = true;
        shimB.hidden = true;
        stage.classList.add("compare");
        // both restart at 0, play muted (no time-sync — B duration differs)
        videoA.currentTime = 0; videoB.currentTime = 0;
        videoA.play().catch(function () {});
        videoB.play().catch(function () {});
        running = false;          // decision buttons are live; chips stay locked
        line.innerHTML = '<span class="tc-caret"></span>';
      }
      videoB.onerror = function () {
        shimB.hidden = true;
        addMsg("tool mono", "preview unavailable — try another command");
        exitCompare();
        finishIdle();
      };
      videoB.oncanplaythrough = reveal;
      at(2500, reveal);           // cap: show compare even if buffering is slow
      videoB.src = FILES[nextKey];
      videoB.load();
    }

    function exitCompare() {
      stage.classList.remove("compare");
      videoB.oncanplaythrough = null;
      videoB.onerror = null;
      setSound("A");              // back to A audible-toggle baseline (muted)
    }

    // APPLY B: B becomes the new A, state advances, edits STACK.
    function applyB() {
      if (pendingKey == null) return;
      var k = pendingKey;
      // adopt the staged next-state computed at click time
      state = stagedState || state;
      videoA.src = FILES[k];
      videoA.load();
      videoA.currentTime = 0;
      videoA.play().catch(function () { playA.hidden = false; });
      exitCompare();
      labelA();
      addMsg("tool mono", "approve() — B is the new A");
      applied.classList.add("on", "flash");
      at(500, function () { applied.classList.remove("flash"); });
      pendingKey = null; stagedState = null;
      finishIdle();
    }

    // KEEP A — DISCARD: A unchanged.
    function discard() {
      addMsg("tool mono", "discard() — kept A");
      exitCompare();
      pendingKey = null; stagedState = null;
      finishIdle();
    }

    pickA.addEventListener("click", discard);
    pickB.addEventListener("click", applyB);

    function finishIdle() {
      running = false;
      syncChips();
      line.innerHTML = '<span class="tc-caret"></span>';
    }

    // ============================ RESET ============================
    function reset() {
      state = { style: null, s: false, c: false, p: false };
      pendingKey = null; stagedState = null;
      exitCompare();
      videoA.src = FILES.base;
      videoA.load();
      videoA.currentTime = 0;
      playA_();
      setSound("A");
      applied.classList.remove("on", "flash");
      log.innerHTML = "";
      log.appendChild(empty); empty.style.display = "";
      setChipLabels();
      finishIdle();
      labelA();
    }

    // ============================ RUN A CHIP ============================
    var stagedState = null;
    function start(key) {
      if (running) return;
      if (key === "reset") { reset(); return; }
      var next = nextState(key, state);
      if (next == null) return;          // no-op (already applied/subsumed)
      var cmd = COMMANDS[key];
      stagedState = next;
      running = true;
      syncChips();                       // locks chips while running
      chipsWrap.querySelector('[data-cmd="' + key + '"]').classList.add("busy");
      applied.classList.remove("on", "flash");
      line.innerHTML = '<span class="tc-caret"></span>';

      typeLine(cmd.text, function () {
        addMsg("user", cmd.text);
        line.innerHTML = '<span class="tc-caret"></span>';
        at(180, function () {
          addMsg("tool mono", cmd.tool);
          var r = addMsg("render", "loading preview<span class=\"dots\"></span>");
          at(600, function () {
            if (r.parentNode) r.parentNode.removeChild(r);
            enterCompare(keyOf(next), cmd);
          });
        });
      });
    }

    chipsWrap.addEventListener("click", function (e) {
      var chip = e.target.closest(".chip");
      if (!chip || chip.disabled) return;
      start(chip.dataset.cmd);
    });

    // optional: warm-fetch B on first chip hover so the swap feels instant
    var warmed = false;
    chipsWrap.addEventListener("mouseover", function () {
      if (warmed) return; warmed = true;
      ["s", "c", "mb"].forEach(function (k) {
        var im = new Image(); im.src = FILES[k];  // hint the cache
      });
    });

    // ---- boot ----
    setChipLabels();
    videoA.src = FILES.base;
    videoA.load();
    labelA();
    setSound("A");
    soundA.addEventListener("click", function () {
      setSound(soundA.classList.contains("on") ? null : "A");
    });
    soundB.addEventListener("click", function () {
      setSound(soundB.classList.contains("on") ? null : "B");
    });
    playA_();
    syncChips();
  })();
})();
