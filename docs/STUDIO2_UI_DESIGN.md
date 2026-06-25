# VibeClip studio2 — UI Redesign Spec

**Build-ready specification — from "thin lanes" to a CapCut/DaVinci/Palmier-grade generation-native editor, implementable on the existing per-clip stage-recipe backend.**

Audience: the engineer who implements this next. Stack: React 18 + Vite, plain CSS (`studio/src/styles.css`). Backend is fixed (FastAPI, project-keyed `/api/v2/*`); the timeline is **derived lanes over a per-clip stage-recipe**, not a free-form NLE document. Every UI affordance below maps to a real endpoint named in the research and verified against `studio/src/*` and `chat/timeline_view.py` / `chat/app.py`.

---

## 1. DESIGN PRINCIPLES (north star)

1. **Density over whitespace.** Pro editors earn trust by showing many controls legibly, separated by *value steps* (background elevation), not borders or padding. Target ~22–26px rows, 11–13px type, ≤8px gutters. Today's studio2 wastes vertical space (fixed 300px timeline, big preview margins) — reclaim it.
2. **The video track must *read* like footage.** The single biggest "toy tell" is the flat purple bar on the main lane. A real **filmstrip** (we already have `/api/filmstrip`) is Phase 1. A timeline you can scan = a tool you trust.
3. **Generation is a first-class track citizen, not a modal.** Generated clips carry their provenance (prompt/model/seed) in the inspector and are re-promptable forever (Palmier's core idea). Pending generations get a *visible body* (a shimmer card / a "Generating…" overlay on the clip), never a hidden spinner.
4. **The chrome recedes; the media carries the color.** Charcoal near-black shell, one restrained accent (our purple `#7C5CFF`) for selection/primary actions, one warm timecode/playhead color. Track tints are low-saturation and *semantic* (one hue per lane type), tied to a legend — never decoration.
5. **Context-swapping inspector + toolbar.** The right panel and the selected-clip toolbar morph to exactly the selected lane type (broll vs zoom vs caption vs audio). Depth without clutter — the user only ever sees relevant controls.
6. **Honor the backend model; don't fake an NLE.** We have *one* main video track (the rendered clip) + *derived* effect/audio/caption lanes. We do **not** invent free-floating multi-video-track compositing. Every edit is a `POST /api/v2/tool` mutation that returns fresh `{state, timeline}` — the UI reconciles in one round-trip (already wired via `onMutated`).
7. **Calm, technical, filmmaker-facing voice** (adopted from Palmier's AGENTS.md). Microcopy leads with action verbs; status names its subject ("Render failed", not "Oops!"). No marketing chatter in the editor.

---

## 2. OVERALL LAYOUT

Default (editing) state, ~1680×1050 window. Four-region NLE shell + a thin **clip rail** (our per-clip model's analogue to "sequences in a project").

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ TOPBAR  VibeClip·studio2 │ project name │ ⌖fit ▷play ◇undo ◇redo · ⬇Render ⬆Export │ ~44px
├────────┬───────────────────────────────────┬───────────────────────────────────┤
│ CLIP   │   PREVIEW MONITOR                 │   LIBRARY + GENERATION             │
│ RAIL   │   (9:16 phone stage)             │   [Media·Generate·Audio·Styles]    │
│ ~180px │   ~46% width                     │   ~25% width (300–340px)           │
│        │   ┌─────────────┐                 │                                    │
│ #12 ●  │   │  video      │                 │  ▢ ▢ ▢  asset grid                 │
│ #13    │   │  +caption   │                 │  ▢ ▢ ▢  (Generating… shimmer card) │
│ #14 ●  │   │  overlay    │                 │                                    │
│        │   └─────────────┘                 │  ── prompt composer (docked) ──    │
│        │  ◀◀ ▷ ▶▶  00:04.20 / 00:12.4  ½▿ │  [describe…]  ⬡ model ▿  4,646 cr │
│        ├───────────────────────────────────┴───────────────────────────────────┤
│        │ TL-TOOLBAR  ▤select ✂split T-text │ ⤢snap ◇mark ⊟fit ──●── zoom        │ ~38px
│        ├──────────┬────────────────────────────────────────────────────────────┤
│        │ HEADERS  │ RULER  0    1s   2s   3s   4s ▾marker  5s   6s …            │
│        │ (gutter) │ ┌────────────────── playhead│ (red) ──────────────────────┐ │
│  RIGHT │ ▣ Video  │ ▓▓▓▒▒▒░░░ FILMSTRIP thumbnails ░░░▒▒▒▓▓▓ [trim◀ ▶]         │ │
│  EDGE: │ ◆ Zoom   │   ◇──╱‾‾╲──◇  keyframe curve                                │ │
│ INSPEC-│ ▤ B-roll │      ▭ neon city  ✦      ▭ logo reveal ✦                   │ │
│ TOR    │ T Capt.  │  ▪word ▪word ▪filler ▪word ▪word  (chips)                  │ │
│ (over- │ ♪ Music  │ ∿∿∿∿∿∿∿∿ WAVEFORM ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿                          │ │
│ lays   │ ♪ SFX    │        ◆ whoosh        ◆ impact                            │ │
│ stage) │ ⊞ Master │ ▭fade-in────────────────────────────fade-out▭             │ │
│        └──────────┴────────────────────────────────────────────────────────────┘ │
└────────┴───────────────────────────────────────────────────────────────────────┘
```

**Region proportions (default):** Clip rail ~180px fixed · Center column flex (preview top ~50% of column height, timeline bottom ~50% — timeline becomes `flex:1`, not fixed 300px) · Library ~300–340px fixed. The **Inspector** is a right-docked panel that slides over the stage on selection (keep it floating-but-docked initially; promote to a 4th fixed column in Phase 4).

**Resizable gutters:** preview↔timeline vertical split (drag), and both side panels collapsible (`◀`/`▶` chevrons in their headers, mirroring Palmier's collapsible rails). Persist widths to `localStorage`.

**Responsive:** below ~1200px, collapse the clip rail into a dropdown in the topbar; below ~960px, the Library becomes an overlay drawer (button in topbar). Timeline always full-width of the center column. Track-header gutter stays a fixed `--track-header-w` so headers and lanes align via CSS Grid.

---

## 3. DESIGN TOKENS

Adopt Palmier's **AppTheme token contract** as the single source of truth, mapped to CSS variables in `:root`. **Rule (enforce in review): no hardcoded hex/px in components — add a token first.** This replaces the ad-hoc `:root` block in `styles.css` today.

### Color — dark editor palette, purple + teal accents

```css
:root {
  /* Background elevation ramp (Palmier 4-step, slightly warmed for our brand) */
  --bg-0: #0A0A0F;   /* window base            */
  --bg-1: #121219;   /* panels (rail/library)  */
  --bg-2: #1A1B24;   /* raised cards, headers  */
  --bg-3: #24262F;   /* inputs, control wells, hover */

  /* Text — white-with-alpha (copy Palmier; consistent on any elevation) */
  --tx-1: rgba(255,255,255,0.92);  /* primary           */
  --tx-2: rgba(255,255,255,0.62);  /* secondary labels  */
  --tx-3: rgba(255,255,255,0.40);  /* muted / hint      */
  --tx-4: rgba(255,255,255,0.24);  /* disabled          */

  /* Borders — white-alpha hairlines (separation by value, not lines) */
  --bd-1: rgba(255,255,255,0.08);
  --bd-2: rgba(255,255,255,0.14);
  --bd-strong: rgba(255,255,255,0.44);

  /* Accents */
  --accent:      #7C5CFF;  /* brand purple — selection, primary CTA, active tab */
  --accent-hi:   #A78BFA;  /* hover/glow                                        */
  --accent-2:    #5AD1C4;  /* teal — generation/secondary, "rendered" ok        */
  --timecode:    #F39933;  /* orange — ruler labels + timecode readouts (Palmier) */
  --playhead:    #FF4444;  /* red — the one warm UI color, playhead + record    */
  --ok:          #4FB85F;
  --err:         #E54F4F;

  /* Track colors — SEMANTIC, one hue per lane key (legend-bound) */
  --trk-zoom:        #F6C453;  /* amber — motion/keyframes */
  --trk-splitscreen: #8B9CFF;  /* periwinkle — gameplay BG */
  --trk-broll:       #5AD1C4;  /* teal — generated/video b-roll */
  --trk-overlay:     #C08BFF;  /* lilac */
  --trk-brand:       #FF9F6E;  /* coral */
  --trk-subtitles:   #7C5CFF;  /* purple — captions */
  --trk-fx:          #FF6EA9;  /* pink */
  --trk-sfx:         #FFCE5A;  /* yellow */
  --trk-music:       #69D27A;  /* green — audio */
  --trk-ambience:    #5AA9FF;  /* blue — audio */
  --trk-fade:        #9AA3B2;  /* grey — master/fade */
}
```

(The track hues intentionally preserve today's `LANE_COLOR` values from `Timeline.jsx` so the redesign is a visual upgrade, not a re-theme — but they now live in CSS, shared by headers, items, and the legend.)

### Spacing, radii, type, elevation, icons (Palmier ramps)

```css
:root {
  /* Spacing (half-steps give fine density control) */
  --sp-1: 2px;  --sp-2: 4px;  --sp-3: 6px;  --sp-4: 8px;
  --sp-5: 10px; --sp-6: 12px; --sp-7: 14px; --sp-8: 16px;
  --sp-9: 20px; --sp-10: 24px;

  /* Radii */
  --r-xs: 3px; --r-sm: 6px; --r-md: 10px; --r-lg: 14px;

  /* Type scale */
  --fs-micro: 9px; --fs-xs: 10px; --fs-sm: 11px; --fs-md: 12px;
  --fs-lg: 13px; --fs-xl: 15px; --fs-h: 18px;
  --fw-reg: 400; --fw-med: 500; --fw-semi: 600; --fw-bold: 700;

  /* Borders */
  --bw-hair: 0.5px; --bw-thin: 1px; --bw-med: 1.5px; --bw-thick: 2px;

  /* Elevation */
  --sh-sm: 0 0.5px 1px rgba(0,0,0,0.30);
  --sh-md: 0 2px 4px rgba(0,0,0,0.30);
  --sh-lg: 0 8px 24px rgba(0,0,0,0.45);

  /* Track heights (Phase 1 introduces these) */
  --track-header-w: 116px;
  --ruler-h: 26px;
  --th-video: 64px;   /* tall — filmstrip      */
  --th-audio: 44px;   /* medium — waveform     */
  --th-range: 26px;   /* range chips           */
  --th-point: 22px;   /* point/marker lanes    */

  /* Motion */
  --anim-hover: 0.15s ease; --anim-trans: 0.2s ease;
}
```

**Typography:** system sans (current stack is fine; Inter-equivalent). **Tabular figures everywhere for timecode/values** (`font-variant-numeric: tabular-nums` — already used in spots; make it global on `.mono`, ruler, inspector values). Three functional sizes: tiny captions (`--fs-xs`), body labels (`--fs-md`), section headers (`--fs-xl`).

**Iconography:** monochrome 1.25–1.5px line icons, ~16px on ~28px hit targets; filled/accent-glow on active. Use a single tiny set — recommend **lucide-react** (tree-shakable, ~no weight per-icon) as the *one* justified dependency, OR hand-rolled inline SVGs to avoid the dep. Icon+label in the track-header rail and library tabs; icon-only in the dense timeline toolbar.

---

## 4. REGION-BY-REGION SPEC

For each: **Component** · **Data source (real endpoint)** · **Key interactions**.

### 4.1 TOP BAR — `Topbar.jsx` (new, extracted from `App.jsx` header)

- **Component:** `Topbar` — brand + `studio2` badge, project name (`state.display_name`), a centered **transport/global cluster**, and right-pinned **Render / Export**.
- **Data:** `state.display_name`, `state.project` (already in `App`). Global actions call `runTool(project, 'undo'|'redo'|'render_clip'|'export_clip', {clip_id}, clip)` — all four are whitelisted today and unused.
- **Interactions:** Undo/Redo buttons (wire the whitelisted `undo`/`redo` tools; disable optimistically when no history is known). **Render** (build/refresh the clip mp4 → sets `rendering` true, reuses the existing `onRendering` flag → `.rendering-overlay`). **Export** (download). Keep the `legacy studio →` link but demote it to an overflow `⋯` menu. Layout-preset buttons (Default / Library-wide / Inspector-wide) are a Phase-4 nicety — CapCut's "most important pro affordance"; defer.

### 4.2 LEFT — CLIP RAIL — `ClipRail.jsx` (refactor of `App.jsx` `<aside.rail>`)

This is **our** equivalent of DaVinci's "sequences" / project bin: the list of clips in the project. Keep it thin.

- **Component:** `ClipRail` — `Clips · N` header, scrollable list.
- **Data:** `state.clips[] = {id, title, status, score, rendered}` (from `getState`). `state.active_clip`.
- **Interactions:** click → `setClipId` (already wired). Add: a `rendered` swatch (teal dot, exists), a small **status chip** (`status`) and **score** badge if present, and a mini filmstrip-poster thumbnail per clip (first tile of `/api/v2/filmstrip` sprite — cheap, makes the rail scannable). Right-click → Render / Duplicate (later).

### 4.3 RIGHT — LIBRARY + GENERATION — `LibraryPanel.jsx` (rework of `AssetPanel.jsx`)

Palmier's signature region: generation folded **into** the asset browser. Vertical structure top→bottom: **tab row → filter/search → asset grid → docked prompt composer**.

- **Tabs:** `Media` · `Generate` · `Audio` · `Styles`. (Today only Generate/Assets exist.)
  - **Media** (`MediaTab`): grid of imported + generated assets. Data: `listAssets()` → `{assets:[{id,kind,description,tags,name,thumb,path}]}` (`/api/assets`, `/asset_thumb/{id}`). Add a **kind filter** (image/video/audio) + tag search (client-side over `tags`). Upload button → `uploadAsset` (`/api/assets/upload`).
  - **Generate** (`GenerateTab`): current form, but restyled as a **docked composer** (Palmier pattern) pinned to the panel floor: `kind` segment (image/video), prompt textarea, **model selector chip** anchored bottom (`getModels()` → `/api/v2/genmedia/models` `{available,image[],video[],i2v[]}`), seed, primary **Generate** button. Add a **credit/ambient cost line** if the backend exposes one (optional; omit if not — don't invent).
  - **Audio** (`AudioTab`): music + SFX browser. Data: filter `listAssets()` by `kind==='audio'`. Drop → `set_music` / `add_sound_effect` (whitelisted). (No dedicated audio catalog endpoint exists; reuse assets. If a curated SFX list is later wanted, that's a backend add — note, don't block.)
  - **Styles** (`StylesTab`): list available styles → `apply_style` (whitelisted; `assets/styles/learned_friend.json` exists). **Backend gap:** no list endpoint today → Phase 3 adds optional `GET /api/v2/styles`; **fallback**: hardcode the known built-ins + read `assets/styles/*.json` names client-side via a static manifest. Cards render the style name + a sample chip ("Aa" rendered in the style) — CapCut's "see the result, not the filename".
- **Asset grid (`AssetGrid` / `AssetCard`):** responsive 2–3 col, rounded cards. Card = thumb (`a.thumb`), kind badge, 2-line description, hover-reveal actions: `→ Video` (i2v popover → `imageToVideo`), `+ Timeline` (→ `addBrollFromAsset` at playhead). **Pending-generation card:** when a generation is in flight, render a **shimmer placeholder card** occupying a real grid slot (Palmier's "pending state has a body"); swap in-place to the real thumb on completion (poll `listAssets()` or read the tool result).
- **Drag-and-drop:** make cards `draggable`; the **timeline owns a single container-level drop handler** (Palmier's hard-won lesson: never nest drop zones — one spanning handler + geometry hit-test to resolve the target track/time). Drop a video/image → `add_broll` at the dropped time; drop audio on Music/SFX lane → `set_music`/`add_sound_effect`.

### 4.4 CENTER — PREVIEW MONITOR — `Preview.jsx` (enhance) + new `Transport.jsx`

- **Component:** `Preview` (the 9:16 `.phone` stage) + a real **`Transport`** bar beneath it (today there's none — it relies on native `controls`).
- **Data:** `src = timeline.media_url || mediaUrl(project, clip)` (`/api/v2/media`, artifact-versioned). `timeline.duration`, `fps`, `speed`, `cut`. `rendered`/`rendering` flags from `App`.
- **Frame:** keep the phone frame but **remove native `controls`** and draw our own transport, so timecode is frame-accurate and tabular. Render **overlays on the canvas**: optional **title-safe / action-safe** guides, and a live caption preview is already burned into the rendered mp4 (no extra work).
- **Transport bar:** left = current TC + total (`00:04.20 / 00:12.40`, `--timecode` orange, tabular, frame-accurate from `fps`). Center = `⏮ ◀frame  ▷/❚❚  frame▶ ⏭`, plus a **keyframe diamond** placeholder (Phase 4, for zoom). Right = **aspect/zoom-to-fit** badge and a **playback-resolution** dropdown (½, ¼) as a CSS `transform: scale` on the video for perf on weak machines (cosmetic; no backend). Scrubber is the timeline ruler (shared `videoRef` already lifted in `App`).
- **Interactions:** Space = play/pause; `,`/`.` = frame-step (`currentTime ± 1/fps`); J/K/L shuttle (later). All operate on the single shared `videoRef`.

### 4.5 CENTER — THE MULTI-TRACK TIMELINE — `Timeline.jsx` (major rework) + sub-components

This is the heart — fully detailed in §6. Region summary here:

- **Component:** `Timeline` (layout/coordinator) → `TimelineToolbar`, `Ruler`, `PlayheadOverlay`, `TrackHeader` (per lane), and per-kind lane renderers: `VideoTrack`→`Filmstrip`, `Waveform`, `CaptionTrack`, `KeyframeLane` (zoom), `RangeLane`/`PointLane` (`TimelineItem`).
- **Data:** `getTimeline(project, clip)` → `{duration, fps, tracks[], markers, cut, media_url, speed}`. Each track `{key, label, kind:'range'|'point'|'span', editable, items[{start,end,label,idx,value,motion,generated,gen,query}]}`. Track order is fixed by backend `TRACK_ORDER`: `zoom, splitscreen, broll, overlay, brand, subtitles, fx, sfx, music, ambience, fade`. **Video** (the main rendered track) is rendered *first/top* as the hero lane; derived tracks follow in `TRACK_ORDER`.
- **Track headers (`TrackHeader`):** replace the flat 96px text column. Per row: color swatch (`--trk-*`), kind icon, label, and **mute/solo/lock/visibility** toggles. *Honesty note:* our model has no per-lane mute/lock tool yet — render these as **visual toggles that hide/dim the lane client-side** (visibility/collapse work immediately; mute/lock are visual-only placeholders wired to tools later). Add per-track **collapse** (hide lane body, keep header) and **height** affordance.
- **Filmstrip main track:** `/api/filmstrip` sprite as `background-image`. **Backend gap (the one required task):** see §6.
- **Ruler/playhead/snapping/toolbar/zoom:** detailed in §6.

### 4.6 RIGHT — INSPECTOR — `Inspector.jsx` (generalize)

- **Component:** `Inspector` — context-sensitive to the selected lane/item. Today it's a floating window that **only edits b-roll**. Generalize to all editable tracks.
- **Data:** the selected `{track, item}` (from `onSelectEvent`). For generated b-roll: `item.gen {model, provider, seed, prompt}`, `item.query`, `item.idx`.
- **Sections (collapsible, Palmier `InspectorSection`/`InspectorRow` primitives):**
  - **Window** (all items): `start`/`end` as **scrubbable number fields** (drag-to-scrub) → commit via `edit_event`/`nudge_edit` (whitelisted). Delete via `delete_event`.
  - **GENERATED** (b-roll/overlay with `generated`): `Model`, `Aspect`, `Seed`, editable **Prompt** textarea → **Rerun (new take)** / **Regenerate** → `rerunBroll(clip,idx,prompt,seed)`. References strip (if `gen` carries them). This is Palmier's "provenance travels with the clip, re-promptable forever".
  - **Zoom** (key=zoom): `value` (strength) + `motion` glyph as a scrubbable field → `add_zoom`/`edit_event`.
  - **Audio** (music/sfx/ambience): volume/fade placeholders → `set_music`/`add_sound_effect` args where supported.
  - **Caption** (subtitles): read-only segment text + a "regenerate captions" → `set_subtitles`.
- **Control anatomy (the load-bearing primitive):** each row = `[label] … [slider] [scrubby numeric] [◇ keyframe (Phase 4)] [⟲ reset]`. Build `<InspectorSection>`, `<InspectorRow>`, `<ScrubbableNumberField>` as reusable primitives (direct port of Palmier's `Inspector/Components/`).

---

## 5. COMPONENT INVENTORY

Tree under `studio/src/`. **(E)** = existing/refactor, **(N)** = new. Each line: responsibility · props/endpoints.

```
studio/src/
├─ App.jsx                      (E) shell + data orchestration; lifts pxPerSec/zoom + panel widths
│                                   · getState, getTimeline, onMutated
├─ api.js                       (E) add: getFilmstripUrl, getTranscript, editEvent,
│                                   deleteEvent, nudgeEdit, addSoundEffect, setMusic, addZoom,
│                                   applyStyle, listStyles, undo, redo, renderClip, exportClip
│                                   (all thin runTool wrappers + 2 new GET wrappers)
├─ styles.css                   (E) token layer (§3) + CSS-Grid timeline + density pass
├─ tokens.css                   (N) the :root token contract (imported first)
│
├─ components/
│  ├─ Topbar.jsx                (N) brand/project + transport + undo/redo + Render/Export
│  │                                · runTool(undo|redo|render_clip|export_clip)
│  ├─ ClipRail.jsx              (N, ex-App aside) clip list bin · state.clips, getFilmstripUrl(poster)
│  │
│  ├─ Preview.jsx               (E) 9:16 stage, canvas overlays, no native controls
│  │                                · timeline.media_url / mediaUrl, fps, speed
│  ├─ Transport.jsx             (N) play/step/timecode/fit/res · shared videoRef, fps
│  │
│  ├─ Timeline.jsx              (E) layout/coordinator only (geometry + state); delegates rows
│  │                                · getTimeline payload, videoRef, onMutated, selected
│  ├─ timeline/
│  │  ├─ TimelineGeometry.js    (N) pure px↔time math (pxPerSec, scroll); no JSX (Palmier pattern)
│  │  ├─ TimelineToolbar.jsx    (N) select/split/text tools, snap, marker, fit, zoom slider
│  │  ├─ Ruler.jsx              (N) adaptive ticks + markers · duration, fps, markers, pxPerSec
│  │  ├─ PlayheadOverlay.jsx    (N) red playhead as an overlay layer · playhead, pxPerSec
│  │  ├─ TrackHeader.jsx        (N) swatch/icon/label + mute/solo/lock/collapse · track meta
│  │  ├─ VideoTrack.jsx         (N) main lane: Filmstrip + trim handles + shades
│  │  ├─ Filmstrip.jsx          (N) draws /api/v2/filmstrip sprite; hover-scrub popover
│  │  ├─ Waveform.jsx           (N) canvas waveform for music/sfx/ambience · WebAudio decode of media_url
│  │  ├─ CaptionTrack.jsx       (N) word/segment chips + filler styling · getTranscript
│  │  ├─ KeyframeLane.jsx       (N) zoom strength curve · track[zoom].items value/motion
│  │  └─ TimelineItem.jsx       (N) one block per kind (range/point/span); drag-move/resize
│  │                                · edit_event/nudge_edit/delete_event, item.idx
│  │
│  ├─ Inspector.jsx             (E) generalize beyond b-roll; section/row primitives
│  ├─ inspector/
│  │  ├─ InspectorSection.jsx   (N) collapsible group header (Palmier)
│  │  ├─ InspectorRow.jsx       (N) label + control row
│  │  └─ ScrubbableNumberField.jsx (N) drag-to-scrub numeric (Palmier)
│  │
│  └─ library/
│     ├─ LibraryPanel.jsx       (E, ex-AssetPanel) tab shell + container drop handler
│     ├─ MediaTab.jsx           (N) imported+generated grid · listAssets, filter
│     ├─ GenerateTab.jsx        (N, ex-AssetPanel gen form) docked composer · getModels, generateAsset
│     ├─ AudioTab.jsx           (N) music/sfx browser · listAssets(audio), set_music/add_sound_effect
│     ├─ StylesTab.jsx          (N) style cards · listStyles/applyStyle
│     ├─ AssetGrid.jsx          (N) responsive grid + pending shimmer card
│     └─ AssetCard.jsx          (N) thumb/badge/desc + draggable + →Video/+Timeline
└─ hooks/
   ├─ useTimelineZoom.js        (N) pxPerSec state + fit-to-window
   ├─ useFilmstrip.js           (N) fetch+cache sprite per (clip, width-bucket)
   └─ useWaveform.js            (N) decodeAudioData → peaks cache
```

---

## 6. THE TIMELINE REDESIGN IN DEPTH

Turning thin lanes into a CapCut/Palmier-grade timeline. **The geometry model changes** from `%-of-duration` to **`pxPerSec`-driven absolute positioning**, so a zoom slider works and long clips scroll horizontally.

### 6.1 Geometry & zoom model (`TimelineGeometry.js` + `useTimelineZoom`)

- Lift `pxPerSec` to `App` (default ~`max(40, viewportW / duration)` = fit). `secToPx(t) = t * pxPerSec`; lane width = `duration * pxPerSec`. Items use `position:absolute; left: secToPx(start); width: secToPx(end-start)`.
- **Zoom slider** in the toolbar sets `pxPerSec` (range ~10–400). **Fit (`⊟` / Shift+Z)** sets `pxPerSec = laneViewportW / duration`. Vertical track-height tokens (`--th-*`) handle row sizing.
- The lane viewport scrolls horizontally; the **track-header gutter is sticky** (`position:sticky; left:0`). Use **CSS Grid**: `grid-template-columns: var(--track-header-w) 1fr`, one grid row per track with its `--th-*` height, so headers and lanes stay aligned at any height.

### 6.2 Main VIDEO track as a real FILMSTRIP (`VideoTrack` + `Filmstrip`)

The Phase-1 hero change. Today: one flat `block-fill` bar.

- **Render:** a `<div className="lane main video">` at `--th-video` (64px). Background = the `/api/filmstrip` sprite (a single horizontal JPG of N tiles) drawn with `background-image`, `background-size: <laneWidth>px <th>`, so the strip stretches to lane width. As `pxPerSec` rises the tiles spread; tile count is fixed (N≈80) but they scale — good enough; for very wide zoom re-fetch with larger N.
- **Hover-scrub popover:** reuse the *same sprite* — a small floating thumbnail above the cursor showing tile `floor(t/dur * N)` via `background-position`. No extra endpoint.
- **Trim handles + shades:** keep the existing head/tail handles and `trim-shade` logic from `Timeline.jsx` (it's good — live-scrub preview, commit via `set_cut`), now overlaid on the filmstrip. `cut`-mapping math is unchanged.

**THE ONE REQUIRED BACKEND TASK.** `/api/filmstrip/{clip_id}` and `/api/transcript/{clip_id}` call `_require_session()` against the **global `SESSION`** (verified: `chat/app.py:1447`, `1453` `SESSION.clip(clip_id)`), and take a bare `clip_id` with **no `?project=`**. studio2 is project-keyed via `_v2_session(project)` → `mcp_bridge._resolve(project)` (`chat/app.py:1587`). So:

- **Add project-keyed v2 wrappers** that resolve the session per-project and reuse the existing handler bodies:
  - `GET /api/v2/filmstrip?project&clip&n=80&h=54`
  - `GET /api/v2/transcript?project&clip`
- Implementation: factor the existing `filmstrip()`/`transcript()` bodies to take a `Session` arg (e.g. `_filmstrip_impl(sess, clip_id, n, h)`), call from both the legacy v1 route (`SESSION`) and the new v2 route (`_v2_session(project)`). Small, mechanical. `api.js` adds `getFilmstripUrl(project,clip,n,h)` and `getTranscript(project,clip)`.
- **Fallback if not yet added:** Phase 1 can ship against the **legacy `/api/filmstrip/{clip_id}`** *only when the global SESSION happens to hold the same project* (single-project dev). Acceptable for the Phase-1 demo, but the v2 wrapper is mandatory before multi-project use — gate behind a `getFilmstripUrl` that prefers v2 and falls back to v1.

### 6.3 Audio tracks with WAVEFORMS (`Waveform` + `useWaveform`)

Music / SFX / Ambience lanes (`--th-audio`, 44px) get real waveforms.

- **Approach (no backend):** `decodeAudioData` the clip's `media_url` (`/api/v2/media`) once via WebAudio, compute min/max **peaks** per pixel column, draw to a `<canvas>` mirrored around the centerline (amplitude scales with track height). Cache peaks per `(clip, media_url)`. For per-asset audio (a music file separate from the mp4) decode its own URL.
- **Fallback / optimization:** if long clips make client decode slow, add **optional** `GET /api/v2/peaks?project&clip&n=2000` returning a normalized min/max array. Not required — note it, don't build it in Phase 2.
- For lanes that are *generated audio* (Palmier's "label-as-prompt"), the clip block carries its prompt as label and is inspectable/re-promptable like b-roll.

### 6.4 Captions as a WORD-LEVEL track (`CaptionTrack`)

Today: one truncated 22-char chip per segment. The word timings already exist and are unused.

- **Data:** `getTranscript(project, clip)` → `{words:[{i,start,end,word,is_filler}], segments, fps}`. Render **word chips** positioned by `start/end`, with **filler words** (`is_filler`) styled muted/strikethrough (a real "remove filler" affordance later). Fall back to the segment-level `subtitles` track already in `timeline.tracks` if transcript fetch fails.
- Lane height `--th-range` (26px); chips are small pills in `--trk-subtitles`.

### 6.5 Derived effect lanes, grouping & ordering

- **Order** strictly follows backend `TRACK_ORDER` (`zoom, splitscreen, broll, overlay, brand, subtitles, fx, sfx, music, ambience, fade`) — do **not** reorder client-side (the model is positional). Video (main) renders above all.
- **Per-kind rendering** (replace the uniform 30px flat bar):
  - `range` (broll/overlay/brand/splitscreen): clip block at `--th-range` with label + `✦` for `generated`; b-roll/overlay show a poster thumb if `gen` provides one.
  - `point` (sfx/fx/markers): a small diamond/dot marker at `--th-point` with an icon + tooltip; no width.
  - `span`/`zoom`: `KeyframeLane` draws a strength **curve** (◇ keyframes connected by the `value`, `motion` glyph indicating in/out) — CapCut's "animation visible on the lane".
  - `fade`/master: a single full-width block showing fade-in/out ramps as triangles at the edges.
- **Group visually:** a faint divider + group label between the **video/visual** group (zoom→brand), **caption**, and the **audio** group (music/sfx/ambience) — mirrors DaVinci's V-stack / A-stack split.

### 6.6 Ruler, timecode, playhead

- **Ruler (`Ruler`):** adaptive ticks — seconds when zoomed out, sub-second/frames when zoomed in (driven by `pxPerSec` and `fps`). Labels in `--timecode` orange, tabular. **Markers** (`timeline.markers`, already in payload) render as pins docked in the ruler; click-add → `add_marker`, drag-remove → `remove_marker` (whitelisted).
- **Playhead (`PlayheadOverlay`):** a separate overlay layer (Palmier pattern) spanning all lanes, `--playhead` red with a draggable handle in the ruler. Synced to `video.timeupdate` (already wired). Frame-accurate scrub.

### 6.7 Snapping, selection, and the toolbar tools (`TimelineToolbar`)

- **Tools (CapCut/Palmier set):** `▤ Select` (default arrow), `✂ Split` (blade — `B`; splits the *main* clip at playhead → maps to a `set_cut`-style edit or a future `split` tool; if no split tool exists, scope Split to selecting+trimming for now and note the gap), `T Text` (adds/edit a caption via `set_subtitles`). Plus: `⤢ Snap` toggle (magnet), `◇ Marker` add, `⊟ Fit`, and the **zoom slider** on the right.
- **Snapping:** a `SnapEngine` helper — when dragging item edges/playhead, snap to nearby clip edges, markers, and the playhead within a px threshold; show a `SnapIndicatorOverlay` vertical guide (Palmier pattern). Toggle in toolbar.
- **Selection:** click an item → `onSelectEvent(track,item)` (wired) → Inspector. Selected item gets a bright outline (`--bd-strong`). Drag-move/resize editable items (`item.idx` present) → `nudge_edit`/`edit_event`; `Del` → `delete_event`.
- **Keyboard:** Space play/pause, `,`/`.` frame-step, `B` split, `S` snap, `Del` delete, `I`/`O` set trim head/tail, Shift+Z fit. (Bind on a focused timeline container.)

---

## 7. PHASED IMPLEMENTATION PLAN

Each phase is independently shippable and demoable. **Phase 1 is the smallest change that instantly reads "pro".**

### Phase 1 — Filmstrip + tokens + real track headers *(the "instant pro" pass)*
- **Goal:** the timeline stops looking like a toy. Real footage on the main track, semantic track headers, denser charcoal shell.
- **Backend (required):** add `GET /api/v2/filmstrip?project&clip&n&h` and `GET /api/v2/transcript?project&clip` by factoring the existing `filmstrip()`/`transcript()` bodies in `chat/app.py` to take a `Session` (`_v2_session(project)`).
- **Frontend:** add `tokens.css` (§3); token-ize `styles.css`. New `Filmstrip.jsx` + `VideoTrack.jsx` (sprite background + existing trim handles). New `TrackHeader.jsx` (swatch/icon/label + visibility/collapse toggles; mute/lock visual-only). Convert `.tl-grid` to CSS Grid with `--track-header-w` + `--th-*` heights. `api.js`: `getFilmstripUrl`.
- **Demo:** open `/studio2?project=demo_base` — the main track shows scannable frame thumbnails; tracks have colored headers; UI is visibly denser/darker.

### Phase 2 — Toolbar, zoom model, waveforms, captions
- **Goal:** scannable audio + caption tracks; horizontal zoom/fit; a real tool row.
- **Frontend:** `TimelineGeometry.js` + `useTimelineZoom` (switch lanes to `pxPerSec` absolute positioning + horizontal scroll). `TimelineToolbar.jsx` (select/split/text, snap, marker, fit, zoom slider). `Waveform.jsx` + `useWaveform` (WebAudio peaks for music/sfx/ambience). `CaptionTrack.jsx` (word chips + filler via `getTranscript`). `Ruler.jsx` adaptive ticks + markers (`add_marker`/`remove_marker`). `PlayheadOverlay.jsx` extracted.
- **Backend:** none required (optional `/api/v2/peaks` deferred).
- **Demo:** zoom in/out a long clip; audio lanes show waveforms; captions show per-word chips; markers on the ruler.

### Phase 3 — Library rework + generation-native flow + drag-drop
- **Goal:** Palmier-grade library with generation docked in, and drag-to-timeline.
- **Frontend:** `LibraryPanel.jsx` (rework `AssetPanel`) with `Media/Generate/Audio/Styles` tabs, `AssetGrid`/`AssetCard` (draggable + pending **shimmer card**), docked prompt composer with pinned model chip, `StylesTab` (`applyStyle`). Single container-level **drop handler** on the timeline (`add_broll`/`set_music`/`add_sound_effect` by hit-tested track+time). `Transport.jsx` under the preview.
- **Backend (optional):** `GET /api/v2/styles` (else client manifest fallback).
- **Demo:** generate an image → "Generating…" card resolves in place → drag it onto the timeline → it appears as a b-roll block; switch styles from the Styles tab.

### Phase 4 — Inspector generalization, keyframes, polish
- **Goal:** every lane editable; provenance + re-prompt everywhere; keyframe scaffolding; layout presets.
- **Frontend:** `InspectorSection`/`InspectorRow`/`ScrubbableNumberField` primitives; generalize `Inspector.jsx` to zoom/audio/caption via `edit_event`/`nudge_edit`/`delete_event`/`set_music`/`add_zoom`; `KeyframeLane.jsx` for zoom; keyframe diamonds in inspector + under preview (zoom only, our model's animatable property). Topbar layout presets (Default/Library/Inspector). Undo/Redo wired (`undo`/`redo`).
- **Demo:** select a zoom keyframe, scrub its strength; rerun a b-roll from its prompt; undo it.

### Phase 5 — Async jobs, drag-move/resize, density polish
- **Goal:** non-blocking generation/render with visible per-clip state; full drag-edit.
- **Frontend:** per-clip `ClipGeneratingOverlay` (Palmier) for in-flight gens; SSE/poll for async tool jobs (api.js notes Phase-1 is synchronous); drag-move/resize of all editable items; snap indicator overlay; persisted panel sizes.
- **Demo:** kick a generation, keep editing while a clip shows a generating shimmer, see it swap in.

---

## 8. RISKS + WHAT TO VERIFY

1. **Backend session gap (highest).** Confirmed: `/api/filmstrip` & `/api/transcript` use global `SESSION` and a bare `clip_id` (`chat/app.py:1442–1458`, `1175`). **Verify** `_v2_session(project)` (`:1587` → `mcp_bridge._resolve`) returns an object exposing `.clip(clip_id)` and `.clip(...)["current"]` (the source path the filmstrip needs) identically to global `SESSION`. If the source path lives elsewhere on the v2 session, the factored impl must read it from there.
2. **Filmstrip "not rendered" 404.** `/api/filmstrip` returns 404 when `clip["current"]` is missing/not rendered (`:1457`). The main track must **fall back** to a flat block (today's look) when the sprite 404s, and the UI should prompt "Render to preview frames" rather than show a broken image.
3. **`pxPerSec` migration risk.** Switching item geometry from `%` to absolute px touches the trim-handle math (currently `%`-based in `Timeline.jsx`). **Verify** the `set_cut` mapping (`applyTrim`) still produces correct source times after the switch — keep that math in source-time, only the *rendering* changes to px.
4. **WebAudio decode cost.** `decodeAudioData` on a long mp4 can be heavy/slow and may fail on some codecs. **Verify** on a 60s+ clip; gate behind the optional `/api/v2/peaks` if it stalls. Always render a flat lane fallback.
5. **Speed/caption time mapping.** `timeline_view.serialize` divides caption times by `speed` (captions derive from pre-speed words; `chat/timeline_view.py:30–39`). The new `CaptionTrack` must use the **already-speed-corrected** times from the timeline payload, OR apply the same `/speed` to raw `/api/v2/transcript` words — **verify** they align with the burned-in captions in the preview.
6. **DnD nesting hazard.** Per Palmier's documented lesson, a spanning timeline drop zone shadows nested child drops. **Verify** with a single container-level handler + geometry hit-test (don't register `onDrop` per lane).
7. **Mute/solo/lock are honest placeholders.** Our stage-recipe model has no per-lane mute/lock tool. **Don't ship** those toggles as if functional — visibility/collapse work client-side; label mute/lock as preview-only until a backend tool exists.
8. **Split tool may have no backend.** `✂ Split` on the main clip has no obvious dedicated tool (only `set_cut`). **Verify** the REGISTRY before promising blade-split; scope it to trim if absent.
9. **Tool result shape.** All mutations assume `{result, state, timeline}` back from `/api/v2/tool` and reconcile via `onMutated`. **Verify** the new tools (`edit_event`, `nudge_edit`, `delete_event`, `add_zoom`, `set_music`, `add_sound_effect`, `apply_style`, `add_marker`, `remove_marker`, `undo`, `redo`, `render_clip`, `export_clip`) are all whitelisted in the v2 tool registry and return that shape before wiring UI to them.

---

**Bottom line:** the editing model and data are already pro-grade — the gap is ~90% frontend. Phase 1 (real `/api/filmstrip` filmstrip on the main track + tokenized charcoal shell + semantic track headers) plus the **one** required backend task (project-keyed v2 wrappers for filmstrip/transcript) flips studio2 from "toy" to "pro" in a single ship. Everything after is depth: zoom/waveforms/captions, a generation-native library, and a context-swapping inspector — all on the existing `POST /api/v2/tool` mutation channel.

Relevant files: `/Users/boran/Desktop/shorts-mcp/studio/src/App.jsx`, `/Users/boran/Desktop/shorts-mcp/studio/src/components/Timeline.jsx`, `/Users/boran/Desktop/shorts-mcp/studio/src/components/Preview.jsx`, `/Users/boran/Desktop/shorts-mcp/studio/src/components/AssetPanel.jsx`, `/Users/boran/Desktop/shorts-mcp/studio/src/components/Inspector.jsx`, `/Users/boran/Desktop/shorts-mcp/studio/src/api.js`, `/Users/boran/Desktop/shorts-mcp/studio/src/styles.css`, `/Users/boran/Desktop/shorts-mcp/chat/timeline_view.py`, `/Users/boran/Desktop/shorts-mcp/chat/app.py` (filmstrip `:1442`, transcript `:1175`, `_v2_session` `:1587`).