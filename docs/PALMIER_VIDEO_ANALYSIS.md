# Palmier Launch Video — Deep Analysis

Source: `~/Downloads/palmier-web-video.MP4` — 39.08s, 1920×1080, 25fps. Analyzed
frame-by-frame (1 fps + key-frame zooms). This is Palmier's own launch promo. It
intercuts cinematic b-roll (a person, eyes) with **screen-recordings of the
Palmier Pro editor**, which is what we care about.

> The video's thesis (title cards): **"we built Claude … its own video editor"**
> → **"finally, a video editor built for AI"** → `palmier.io`. The pitch is an
> NLE where an embedded Claude agent does the editing through an MCP/"Palmier Pro
> integration", and generative media is a first-class timeline citizen.

---

## 1. Beat-by-beat timeline

| ~t | Shot | What it shows |
|---|---|---|
| 0–4s | Title (b-roll: person w/ laptop) | "we built Claude … **its own video editor**" |
| ~5s | Blurry monitor | The Palmier editor: multi-track timeline (purple video + green audio), asset grid, macOS dock |
| 9–13s | Chat panel | User: **"Organize my media in Palmier"** → agent: *"I'll create 4 folders and sort everything in one pass."* + tool chip **`Create_folder >`** (Palmier "P" icon) + spinner |
| 15–17s | b-roll "cool" → chat | User types **"now edit it for me"** (chat input with `+` attach) |
| ~20s | Chat (autonomous edit log) | Tool chips: **"Used Palmier Pro integration"**, *"Now muting all the audio clips and setting volume to 0."*, **"Set clip properties"**, *"Now building the text animation"* |
| ~23s | **Full editor** | Title "Palmier Launch Video — Edited". Preview shows the *we built Claude* text-animation over b-roll. Right Inspector → **PROJECT** (Name, Path `/Users/marco…`) + **FORMAT** (Resolution, Frame Rate, Aspect Ratio, Duration). Transport: `00:00:00:17 / 00:00:58:14`, ◻screenshot, **16:9 · 25 · 4K · 80%** |
| 26–27s | **Generation-in-timeline** | Left: **+ Import / + Generate** buttons; **Library › Generated (2 items)** = two **"AI"-badged** clips *"rapid came…into the scr"* (0:04) & *"Upscaled r…into the scr"* (0:03). Timeline clips labeled **"Upscaled rapid camera zoom into the scr"** show an inline **"Generating…" progress bar** in the lane. Inspector tabs **Video · Audio · AI Edit**; TRANSFORM (Position/Scale/Rotation/Opacity/Crop/Flip), **PLAYBACK Speed 4.00×**, ◆ Keyframes |
| ~29s | b-roll | extreme eye close-up, **"holy sh*t"** |
| 31–33s | **@-reference generation** | Chat: **"Generate four close up of @Marcos-Eyes left eye"** (model selector **"Sonnet 4.6 · using API key"**). Agent: *"I'll generate 4 variations of a close-up of Marcos's left eye **using his photo as reference**. Firing all four **in parallel**."* tool chips **`list_models`**, **`generate_image`×4**, then *"…shock, fear, tense stare, and horror intensity. They'll appear in your media library once ready!"* Library shows **folders** (App Footage, Brand Assets, Camera Footage, Frame Variations, Generated, Stills, **Marcos Eyes**…) + variation thumbs **Marcos Eye V1–V4**, *"zoom into left eye"*. Preview: generated eye + *"Finally, a video editor built for AI"*. Timeline now has **V5–V13 + A1–A2** with eye/text/logo clips. Inspector **AI Edit**, Speed 2.99× |
| 35–37s | Fullscreen results | generated eye variations (B&W eye, cosmic-iris) — *"finally, a video editor built for AI."* |
| ~39s | End card | **palmier.io** |

---

## 2. Feature catalog (what Palmier is selling)

### A. Agentic chat **inside** the editor (the core)
Natural-language prompts drive real edits. The chat shows **named tool-call
chips** as the agent works (`Create_folder`, `Set clip properties`,
`generate_image`, `list_models`, "Used Palmier Pro integration"). A model
selector at the input (**"Sonnet 4.6 · using API key"** → BYOK). Two modes seen:
- **Organize**: "Organize my media" → auto-creates folders & sorts assets.
- **Edit**: "now edit it for me" → autonomous multi-step edit (mute audio, set
  clip properties, trims/speeds/transforms, build text animation).

### B. **@-mention reference media** in generation  ← *the feature you flagged*
"Generate four close up of **@Marcos-Eyes** left eye" — you **@-reference an
uploaded photo** and the agent generates **using that photo as reference**. The
demo uploads an eye photo, prompts variations, and they land in the library.

### C. **Parallel variations** ("firing all four in parallel")
One prompt → **N variations**, each a different framing/mood (shock, fear, tense,
horror), generated concurrently, appearing in the library when ready.

### D. **Generation as a first-class timeline citizen**
- A dedicated **+ Generate** button + a **Library › Generated** folder, **"AI"
  badge** on generated assets.
- Generated clips sit **in the timeline** showing their **prompt as the label**
  and an inline **"Generating…" progress bar** in the lane (async jobs).
- Image→video and reference→image both feed the same library→timeline loop.

### E. Full NLE chrome
Multi-track timeline (**V5–V13, A1–A2**), per-clip **Inspector** with
**Video / Audio / AI Edit** tabs → TRANSFORM (Position, Scale, Rotation,
Opacity, Crop, Flip) + PLAYBACK (Speed) + **◆ Keyframes**. Transport with
**4K / 16:9 / 25fps / zoom%**. **+ Import / + Generate**, **Export**, project
**Format** inspector, a **Library with folders**, search + sort.

---

## 3. Map to OUR codebase (EXISTS / PARTIAL / MISSING)

Grounded in the Explore inventory of `/Users/boran/Desktop/shorts-mcp`.

| Palmier feature | Status for us | Evidence / gap |
|---|---|---|
| **Agent loop: NL → edits via tools** | **EXISTS (engine), MISSING (in studio2)** | `chat/app.py` `/api/chat` + `run_turn` + `chat/mcp_bridge.py` + `REGISTRY` tools all work in the **legacy** UI. studio2 has **no chat panel**. |
| Tool-call chips with friendly names | PARTIAL | legacy narrates via `on_tool`/`pg.note`; studio2 doesn't surface it |
| Model selector + BYOK | EXISTS | `auth.user_llm_override` + `config.set_llm_override`; just needs UI |
| "Organize my media" → folders | **MISSING** | asset catalog (`pipeline/assets.py`) is **flat** — no folders/collections, no organize tool |
| **@-reference media in a prompt** | **MISSING** | no @-mention; no chat in studio2 |
| **Reference-image-guided generation** (eye photo → eye images) | **MISSING** | `pipeline/genmedia.py` does text→image only; **no init/reference image for image gen**, no inpaint/mask/negative-prompt/strength |
| Image→video (i2v) | **EXISTS** | `genmedia.generate_video_from_image` + `generate_video_from_asset` tool |
| **Parallel "N variations"** | PARTIAL | `generate_asset` is single-shot; no batch/variations tool (could loop) |
| Generated asset → library | EXISTS | `generate_asset` catalogs; studio2 Media grid + "Generating…" shimmer card |
| **Generation as in-timeline citizen w/ async "Generating…" block** | PARTIAL | generated b-roll carries `gen{}` + filmstrip; but `/api/v2/tool` is **synchronous** (no SSE job), no in-lane progress block |
| Insert generated/asset into timeline | EXISTS | `add_broll` (file/generate/source_ref) + studio2 drag-drop/+Timeline (Phase 3) |
| Regenerate in place (prompt/seed) | EXISTS | `rerun_broll` + Inspector "Rerun/Regenerate" |
| Inspector: transform/playback/keyframes | **MISSING (our Phase 4)** | studio2 Inspector only does b-roll rerun; no position/scale/rotation/opacity/crop/flip/speed/keyframes |
| **AI Edit** inspector tab | MISSING | new concept |
| Multi V-track free compositing (V5–V13) | **ARCH DIFFERENCE** | our model = per-clip stage-recipe + **derived** lanes, not a free NLE document. Generated overlays = `broll`/`overlay` lanes, not arbitrary V-tracks |
| Transport 4K/aspect/fps + Format inspector | PARTIAL | `export_clip` exists; no format/transport UI |
| Library folders + search/sort | MISSING | flat catalog; studio2 filters by kind only |

---

## 4. The headline gaps (what would make us "Palmier-like")

Ranked by how central they are to the video's pitch and to your stated ask
(upload a photo → prompt → image-to-video → insert):

1. **Agentic chat panel in studio2** — we already own the whole engine
   (`run_turn` + `REGISTRY` + `mcp_bridge`); the gap is a chat UI in studio2 that
   streams tool-call chips and reconciles `{state,timeline}` after each tool.
   *Highest leverage: ~all backend exists.*
2. **Reference-image generation + @-mention media** — the eye-photo flow. Needs
   (a) `genmedia` support for a **reference/init image** on image generation (and
   ideally negative prompt/strength), (b) an **@-reference** affordance that
   resolves a library asset id into the generation call. i2v already exists; this
   adds *image→image* with a reference.
3. **Async generation jobs + in-timeline "Generating…" block** — make
   `/api/v2/tool` (or a new `/api/v2/job`) asynchronous with SSE progress, and
   render a pending block in the lane (we already have the shimmer-card pattern).
4. **Parallel "N variations"** — a batch generate that fans out k variations into
   the library (trivial server loop; nice UX win).
5. **Library folders + an "organize my media" agent** — folders in the catalog +
   a tool that buckets assets.
6. **Richer Inspector (transform/playback/keyframes)** — already our **Phase 4**;
   the video confirms it's table-stakes.

**Architectural note:** Palmier is a free-form multi-V-track NLE; we are a
per-clip **stage-recipe** with derived lanes. We should NOT copy free
compositing — we should express these features as generative **broll/overlay**
events + the agent loop, which is our actual moat (lazy recipe + cache-aware
replay). Everything above fits that model.

---

## 5. Suggested next step

A `@Plan`-agent pass that turns §4 into a phased build plan mapped to our files
(genmedia reference-image API, an `/api/v2/chat` + studio2 chat panel, async job
endpoint + in-lane progress, a `generate_variations` tool, asset folders), each
phase independently shippable and demoable — in the same spirit as the studio2
Phases 1–3 already shipped.
