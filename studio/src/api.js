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

// Run a whitelisted REGISTRY tool against the project. Returns
// { result, state, timeline } — fresh server state so the UI reconciles in one
// round-trip. (Phase 1 is synchronous; SSE-streamed jobs land in a later phase.)
export function runTool(project, name, args, clip) {
  return fetch('/api/v2/tool', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ project, name, args, clip }),
  }).then(j)
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

// Text → image/video into the library.
export function generateAsset(project, { prompt, kind, model, seed }, clip) {
  return runTool(project, 'generate_asset',
    { prompt, kind, model: model || '', seed: seed ?? -1 }, clip)
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
export function rerunBroll(project, { clip_id, idx, prompt, seed }, clip) {
  return runTool(project, 'rerun_broll',
    { clip_id, idx, prompt: prompt || '', seed: seed ?? -1 }, clip)
}
