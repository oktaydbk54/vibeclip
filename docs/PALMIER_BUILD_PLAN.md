# studio2 → Palmier Feature Build Plan (phased, file-mapped)

Derived from `docs/PALMIER_VIDEO_ANALYSIS.md`. Ordered by leverage. Each phase is
independently shippable. Architecture rules: one mutation channel
(`/api/v2/tool` → `mcp_bridge.run_tool`); per-clip stage-recipe + derived lanes
(NOT free multi-V-track); project-keyed/disk-backed; fal key only in gitignored
`.env`; new tools must be added to `REGISTRY` + `TOOL_SPECS` + `TOOL_WHITELIST`.

## Phase A — Agentic chat panel in studio2  ✅ highest leverage
- **Backend (`chat/app.py`):** `_V2_HISTORY: dict[str,list]`; `_run_v2_chat(project, message, job, profile_prompt, tier, mode)` that resolves `mcp_bridge._resolve(project)`, runs `run_turn(sess, hist, message, on_tool=…)` under `mcp_bridge._LOCK` with snapshot/restore, returns `{reply,tools,clarify,state,timeline}`. New `POST /api/v2/chat` (sync via `?sync=1`, else submit to `MANAGER`, stream chips over existing `/api/events`, result via `/api/jobs/{jid}`). Cap history `hist[:] = hist[-40:]`.
- **Frontend:** `components/ChatPanel.jsx` (NL input, tier chip, tool-call chips via shared EventSource, clarify chips, pending_plan Apply/Discard banner). `friendlyTool.js` (port STAGE_FRIENDLY/STAGE_TR). `api.js`: `chatTurn`, `getJob`, `openEvents`. Mount in `App.jsx` with one shared EventSource.
- **Demo:** "remove filler words and add a punch-in zoom at the hook" → live chips → preview+timeline reconcile.

## Phase B — Reference-image generation + @-mention media  (the eye-photo flow)
- **`pipeline/genmedia.py`:** extend `generate_image(prompt, *, reference_path=None, negative_prompt="", strength=None)` — inline data-URI `image_url` (same as i2v), fold ref-hash+neg+strength into cache key.
- **`chat/tools.py`:** extend `generate_asset(..., reference="", negative="", strength=-1.0)` — resolve `reference` as asset id, pass path as `reference_path`; add to `TOOL_SPECS.generate_asset.properties`.
- **Frontend:** GenerateTab reference picker (drag/click asset → reference chip), optional negative + strength rows (only when reference attached). ChatPanel `@`-mention autocomplete from `listAssets()`. `api.js` `generateAsset` extended.
- **Risk:** model support varies — default to i2i-capable model (nano-banana/flux-dev) when reference set; verify cache key differs from text-only.

## Phase C — Async generation jobs + in-timeline "Generating…" block
- **`chat/app.py`:** `/api/v2/tool?async=1` → `MANAGER.submit(_run_v2_tool…)`; add `payload["clip"]=clip_id` in `_v2_timeline_payload`.
- **Frontend:** `runToolAsync`; App-level `pendingGen` map; `Timeline.jsx` `GeneratingBlock` lane overlay (shimmer+progress); AssetPanel/Inspector switch gen to async.
- **Risk:** single worker serializes; reconcile only when `result.timeline.clip===current`.

## Phase D — Parallel "N variations" (`generate_variations`)
- **`chat/tools.py`:** `generate_variations(session, prompt, kind, count=4, model, reference, negative, seeds, variation_hints)` — loop `generate_asset` impl with distinct seeds + per-variation hints; serialize catalog writes; REGISTRY+TOOL_SPECS+TOOL_WHITELIST.
- **Frontend:** GenerateTab count stepper + hints field; k pending shimmer cards.

## Phase E — Library folders + "organize my media" agent
- **`pipeline/assets.py`:** optional `folder` field; `set_folder`, `list_folders` (virtual labels, no disk move).
- **`chat/tools.py`:** `move_asset_to_folder`, `organize_assets` (LLM bucketer, validates ids); REGISTRY+TOOL_SPECS+TOOL_WHITELIST. `/api/assets` row gains `folder`.
- **Frontend:** MediaTab folder rail/chips; "Move to folder" action; ChatPanel drives `organize_assets`.
- **Risk:** catalog is global (shared across projects) — folders global too; acceptable.

## Phase F — Richer Inspector (transform/playback/keyframes + AI Edit tab)
- Reuse `edit_event`/`nudge_edit`/`delete_event`/`add_zoom`/`set_speed` (all whitelisted). Generalize `Inspector.jsx` beyond b-roll; tabs Video/Audio/AI Edit; window+zoom+opacity+speed rows; TRANSFORM rows rendered **disabled** ("renderer support pending" — honest placeholder, do NOT fake). `Timeline.jsx` zoom KeyframeLane.
- **Risk:** index-based addressing — reconcile from fresh timeline after every mutation.

## Sequencing
A → B → C is the spine. D needs B+C. E independent. F independent (best after C).
New tools added: `generate_variations` (D), `move_asset_to_folder`+`organize_assets` (E). Extended: `generate_asset` (B). No commits until asked.
