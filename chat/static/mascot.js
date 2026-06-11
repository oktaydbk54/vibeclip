/* VibeClip mascot cursor — "Snip".
   Self-contained: no dependencies, no required DOM. Builds an inline-SVG
   scissors character that replaces the cursor with spring/lerp easing.
   Activates only on fine-pointer devices that allow motion.
   Wire into any page with:
     <link rel="stylesheet" href="/static/mascot.css">
     <script src="/static/mascot.js" defer></script>
*/
(function () {
  "use strict";

  // --- guard: skip on touch / coarse pointers and reduced-motion ---
  var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  var noMotion =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var noHover = window.matchMedia && window.matchMedia("(hover: none)").matches;
  if (coarse || noMotion || noHover) return;

  var ACCENT = "#9b5cff";
  var RED = "#f4524d";
  var GRAPHITE = "#130d1f";

  document.documentElement.classList.add("has-mascot");

  // --- build mascot element ---
  var el = document.createElement("div");
  el.id = "kesim-mascot";
  el.setAttribute("aria-hidden", "true");
  el.innerHTML =
    '<svg viewBox="0 0 44 44" xmlns="http://www.w3.org/2000/svg">' +
    // top blade (cyan, sharp X arm to upper-right)
    '<g class="km-blade km-blade-top">' +
    '<path d="M22 27 L40 7 L42 11 L25 28 Z" fill="' + ACCENT + '"/>' +
    "</g>" +
    // bottom blade (cyan, sharp X arm to lower-right)
    '<g class="km-blade km-blade-bot">' +
    '<path d="M22 27 L40 41 L42 37 L25 26 Z" fill="' + ACCENT + '"/>' +
    "</g>" +
    // handles (loops) — left side, one with red accent
    '<circle cx="11" cy="14" r="6" fill="none" stroke="' + ACCENT +
    '" stroke-width="3"/>' +
    '<circle cx="11" cy="34" r="6" fill="none" stroke="' + RED +
    '" stroke-width="3"/>' +
    // blade connectors to handles
    '<path d="M22 27 L13 16" stroke="' + ACCENT +
    '" stroke-width="3" stroke-linecap="round"/>' +
    '<path d="M22 27 L13 32" stroke="' + ACCENT +
    '" stroke-width="3" stroke-linecap="round"/>' +
    // pivot head (round graphite)
    '<circle cx="22" cy="27" r="7.5" fill="' + GRAPHITE +
    '" stroke="' + ACCENT + '" stroke-width="1.5"/>' +
    // eyes (white) with pupils
    '<g class="km-eye"><circle cx="19.4" cy="26" r="2.2" fill="#fff"/>' +
    '<circle class="km-pupil km-pupil-l" cx="19.4" cy="26" r="1" fill="#0a0c10"/></g>' +
    '<g class="km-eye"><circle cx="24.6" cy="26" r="2.2" fill="#fff"/>' +
    '<circle class="km-pupil km-pupil-r" cx="24.6" cy="26" r="1" fill="#0a0c10"/></g>' +
    "</svg>";
  document.body.appendChild(el);

  var pupilL = el.querySelector(".km-pupil-l");
  var pupilR = el.querySelector(".km-pupil-r");

  // --- pointer + spring state ---
  var px = window.innerWidth / 2,
    py = window.innerHeight / 2; // target (pointer)
  var x = px,
    y = py; // eased position
  var lastX = x;
  var vx = 0; // horizontal velocity for rotation
  var hasMoved = false;
  var rot = 0;

  var idleTimer = null;
  var IDLE_MS = 4000;

  function onMove(e) {
    px = e.clientX;
    py = e.clientY;
    if (!hasMoved) {
      hasMoved = true;
      x = px;
      y = py;
      el.classList.add("km-on");
    }
    clearIdle();
  }

  // --- interactive / text-entry detection ---
  var INTERACTIVE = "a,button,[role=button],.opt,input[type=submit],label,summary,.chip,.tab";
  function isTextEntry(t) {
    if (!t || !t.closest) return false;
    if (t.closest('input:not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"])'))
      return true;
    if (t.closest("textarea")) return true;
    if (t.closest('[contenteditable="true"],[contenteditable=""]')) return true;
    return false;
  }
  function onOver(e) {
    var t = e.target;
    if (isTextEntry(t)) {
      el.classList.add("km-dim");
      el.classList.remove("km-ready");
    } else {
      el.classList.remove("km-dim");
      el.classList.toggle("km-ready", !!(t.closest && t.closest(INTERACTIVE)));
    }
  }

  // --- snip on mousedown + spark ---
  function snip(e) {
    el.classList.add("km-snip");
    setTimeout(function () { el.classList.remove("km-snip"); }, 110);
    if (e) spark(e.clientX, e.clientY);
  }
  function spark(sx, sy) {
    var s = document.createElement("div");
    s.className = "km-spark";
    var ang = Math.random() * Math.PI - Math.PI / 2;
    s.style.setProperty("--sx", Math.cos(ang) * 16 + "px");
    s.style.setProperty("--sy", (Math.sin(ang) * 16 - 6) + "px");
    document.body.appendChild(s);
    // position then fire
    s.style.transform = "translate3d(" + sx + "px," + sy + "px,0)";
    requestAnimationFrame(function () { s.classList.add("km-fire"); });
    setTimeout(function () { if (s.parentNode) s.parentNode.removeChild(s); }, 460);
  }

  // --- blink every 3-6s ---
  function scheduleBlink() {
    setTimeout(function () {
      el.classList.add("km-blink");
      setTimeout(function () { el.classList.remove("km-blink"); }, 130);
      scheduleBlink();
    }, 3000 + Math.random() * 3000);
  }

  // --- idle: bob + one snip after >4s still ---
  function clearIdle() {
    el.classList.remove("km-idle");
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(goIdle, IDLE_MS);
  }
  function goIdle() {
    el.classList.add("km-idle");
    snip();
  }

  // --- page leave / enter fade ---
  function onLeave() { el.classList.remove("km-on"); }
  function onEnter() { if (hasMoved) el.classList.add("km-on"); }

  // --- single rAF loop: spring follow + rotation + pupil look ---
  function tick() {
    // lerp toward pointer
    x += (px - x) * 0.22;
    y += (py - y) * 0.22;
    vx = x - lastX;
    lastX = x;
    // rotation from horizontal velocity (clamped), eased
    var targetRot = Math.max(-16, Math.min(16, vx * 1.4));
    rot += (targetRot - rot) * 0.18;
    el.style.transform =
      "translate3d(" + x + "px," + y + "px,0) rotate(" + rot + "deg)" +
      (el.classList.contains("km-ready") ? " scale(1.15)" : " scale(1)");
    // pupils look toward travel direction
    var lookX = Math.max(-1, Math.min(1, vx * 0.5));
    if (pupilL) pupilL.setAttribute("cx", 19.4 + lookX);
    if (pupilR) pupilR.setAttribute("cx", 24.6 + lookX);
    requestAnimationFrame(tick);
  }

  document.addEventListener("mousemove", onMove, { passive: true });
  document.addEventListener("mouseover", onOver, { passive: true });
  document.addEventListener("mousedown", snip, { passive: true });
  document.addEventListener("mouseleave", onLeave);
  document.addEventListener("mouseenter", onEnter);

  scheduleBlink();
  clearIdle();
  requestAnimationFrame(tick);
})();
