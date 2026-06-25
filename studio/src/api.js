// Thin client for the project-keyed studio2 backend (chat/app.py /api/v2/*).
// Every call carries the project id; the server loads it fresh from disk
// (mirroring chat/mcp_bridge.py), so studio2 is multi-project and never touches
// the legacy global SESSION. Mutations go through one channel: POST /api/v2/tool.

async function j(res) {
  if (!res.ok) {
    let detail
    try { detail = (await res.json()).error } catch { detail = res.statusText }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export function getState(project) {
  return fetch(`/api/v2/state?project=${encodeURIComponent(project)}`, {
    credentials: 'include',
  }).then(j)
}

export function getTimeline(project, clip) {
  return fetch(
    `/api/v2/timeline?project=${encodeURIComponent(project)}&clip=${clip}`,
    { credentials: 'include' },
  ).then(j)
}

export function mediaUrl(project, clip) {
  return `/api/v2/media?project=${encodeURIComponent(project)}&clip=${clip}`
}

// Horizontal thumbnail sprite for the main video track (project-keyed). `v` is
// an artifact version (from timeline.media_url) so the immutable-cached sprite
// busts after a re-render instead of showing stale frames.
export function filmstripUrl(project, clip, { n = 80, h = 54, v = '' } = {}) {
  const q = new URLSearchParams({ project, clip: String(clip), n: String(n), h: String(h) })
  if (v) q.set('v', v)
  return `/api/v2/filmstrip?${q.toString()}`
}

// Project-keyed word timings for the caption track (Phase 2).
export function getTranscript(project, clip) {
  return fetch(
    `/api/v2/transcript?project=${encodeURIComponent(project)}&clip=${clip}`,
    { credentials: 'include' },
  ).then(j)
}

// Run a whitelisted REGISTRY tool against the project. Returns
// { result, state, timeline } — fresh server state so the UI reconciles in one
// round-trip.
export function runTool(project, name, args, clip) {
  return fetch('/api/v2/tool', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ project, name, args, clip }),
  }).then(j)
}

// Same as runTool but non-blocking: the server runs the tool on its job worker
// and returns { job_id }. Stream progress via subscribeEvents and read the
// { result, state, timeline } off the job_done event. Used for long generations
// so the UI can show a pending "Generating…" block instead of hanging.
export function runToolAsync(project, name, args, clip) {
  return fetch('/api/v2/tool?async=1', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ project, name, args, clip }),
  }).then(j)
}

// ---- agentic chat + live job stream (Phase A / C) ------------------------

// One agent turn in studio2: NL → real edits via the same run_turn agent the
// legacy UI drives, but project-keyed. Returns { job_id }; the reply + fresh
// { state, timeline } arrive on the job_done event (job.result).
export function chatTurn(project, { message, mode = 'pro', tier = 'fast' }) {
  return fetch('/api/v2/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ project, message, mode, tier }),
  }).then(j)
}

export function getJob(jid) {
  return fetch(`/api/jobs/${jid}`, { credentials: 'include' }).then(j)
}

// Shared SSE hub: ONE EventSource for the whole app (job_queued / job_progress /
// job_done). Components subscribe with a callback and get an unsubscribe fn back;
// the connection opens lazily on the first subscriber and closes when the last
// one leaves. The job_done event carries job.result (with_result=True server-
// side), so subscribers reconcile state without an extra round-trip.
let _es = null
const _subs = new Set()
export function subscribeEvents(fn) {
  _subs.add(fn)
  if (!_es) {
    _es = new EventSource('/api/events')
    _es.onmessage = (e) => {
      let evt
      try { evt = JSON.parse(e.data) } catch { return }
      _subs.forEach((f) => { try { f(evt) } catch { /* ignore */ } })
    }
    _es.onerror = () => {}   // EventSource auto-reconnects
  }
  return () => {
    _subs.delete(fn)
    if (_subs.size === 0 && _es) { _es.close(); _es = null }
  }
}

// ---- generation + asset library ------------------------------------------

// Selectable models per kind ({ available, video[], image[], i2v[] }).
export function getModels() {
  return fetch('/api/v2/genmedia/models', { credentials: 'include' }).then(j)
}

// The asset library is project-independent (a shared catalog), so it reuses the
// existing /api/assets endpoints rather than the project-keyed v2 surface.
export function listAssets() {
  return fetch('/api/assets', { credentials: 'include' }).then(j)
}

export function uploadAsset(file) {
  const fd = new FormData()
  fd.append('file', file)
  return fetch('/api/assets/upload', {
    method: 'POST', credentials: 'include', body: fd,
  }).then(j)
}

// Text → image/video into the library. `reference` is an optional library image
// asset id to generate FROM (the @-mention-a-photo flow); `negative`/`strength`
// tune it. `async_` runs it on the job worker (non-blocking) → { job_id }.
export function generateAsset(
  project,
  { prompt, kind, model, seed, reference = '', negative = '', strength = -1 },
  clip, async_ = false,
) {
  const args = {
    prompt, kind, model: model || '', seed: seed ?? -1,
    reference, negative, strength,
  }
  return (async_ ? runToolAsync : runTool)(project, 'generate_asset', args, clip)
}

// One prompt → COUNT variations into the library (the "fire all four" flow).
export function generateVariations(
  project,
  { prompt, kind = 'image', count = 4, model = '', reference = '', negative = '', hints = '' },
  clip, async_ = true,
) {
  const args = { prompt, kind, count, model, reference, negative, hints }
  return (async_ ? runToolAsync : runTool)(project, 'generate_variations', args, clip)
}

// ---- asset folders (Phase E) ---------------------------------------------

export function moveAssetToFolder(project, { asset_id, folder }, clip) {
  return runTool(project, 'move_asset_to_folder',
    { asset_id, folder: folder || '' }, clip)
}

export function organizeAssets(project, folders, clip, async_ = true) {
  const args = folders ? { folders } : {}
  return (async_ ? runToolAsync : runTool)(project, 'organize_assets', args, clip)
}

// Image → video (animate a library still) into the library.
export function imageToVideo(project, { asset_id, prompt, model, seed }, clip) {
  return runTool(project, 'generate_video_from_asset',
    { asset_id, prompt: prompt || '', model: model || '', seed: seed ?? -1 },
    clip)
}

// Drop a library asset onto the active clip's timeline as a b-roll overlay.
export function addBrollFromAsset(project, { clip_id, file, start, end }) {
  return runTool(project, 'add_broll',
    { clip_id, auto: false, file, start, end }, clip_id)
}

// Regenerate one generated b-roll event in place (timeline inspector "rerun").
// async_=true runs it on the job worker so the lane can show a "Generating…"
// block instead of blocking; the fresh { result, state, timeline } arrives on
// the job_done event.
export function rerunBroll(project, { clip_id, idx, prompt, seed }, clip, async_ = false) {
  const args = { clip_id, idx, prompt: prompt || '', seed: seed ?? -1 }
  return (async_ ? runToolAsync : runTool)(project, 'rerun_broll', args, clip)
}

// ---- per-event editing (Inspector, Phase F) ------------------------------

// Move/resize/retune one timeline event. stage is the track key (zoom, overlay,
// broll, sfx, fx…); value retunes (zoom strength / overlay opacity / sfx vol).
export function editEvent(
  project, { clip_id, stage, index, start, end, value, motion }, clip,
) {
  const args = { clip_id, stage, index }
  if (start != null) args.start = start
  if (end != null) args.end = end
  if (value != null) args.value = value
  if (motion != null) args.motion = motion
  return runTool(project, 'edit_event', args, clip)
}

export function deleteEvent(project, { clip_id, stage, index }, clip) {
  return runTool(project, 'delete_event', { clip_id, stage, index }, clip)
}

// ---- styles + audio (drag-to-timeline targets) ---------------------------

// Global style catalog ({ styles: [{ name, label }] }) — project-independent,
// like /api/assets, so it reuses the legacy endpoint.
export function listStyles() {
  return fetch('/api/styles', { credentials: 'include' }).then(j)
}

export function applyStyle(project, { clip_id, style }) {
  return runTool(project, 'apply_style', { clip_id, style }, clip_id)
}

// Drop an audio asset as the background music bed.
export function setMusic(project, { clip_id, file }) {
  return runTool(project, 'set_music', { clip_id, file: file || '' }, clip_id)
}

// Drop an audio asset as a one-shot sound effect at a point in time.
export function addSoundEffect(project, { clip_id, time, file }) {
  return runTool(project, 'add_sound_effect',
    { clip_id, time: +(+time).toFixed(2), file: file || '' }, clip_id)
}
