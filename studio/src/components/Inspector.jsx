import { useEffect, useState } from 'react'
import { deleteEvent, editEvent, rerunBroll } from '../api.js'

// Floating inspector for a selected timeline event. Generalized across editable
// lanes (Phase F): every editable event shows its window + a Delete; tunable
// lanes (zoom strength, overlay opacity, sfx volume) get a retune slider;
// generated b-roll keeps the Palmier-style prompt/seed + Regenerate. The full
// TRANSFORM block (position/scale/rotation/crop/flip) is shown but DISABLED —
// our renderer is a stage-recipe, not a free compositor, so we don't fake
// controls we can't honor.

function cleanPrompt(item) {
  const g = item.gen
  if (g && g.prompt) return g.prompt
  let q = item.query || item.label || ''
  if (q.toLowerCase().startsWith('ai:')) q = q.slice(3).trim()
  return q
}

// Per-lane retune metadata: which value the slider edits, and its range/label.
const TUNE = {
  zoom: { label: 'Strength', min: 1, max: 2.5, step: 0.05, fmt: (v) => `${v.toFixed(2)}×` },
  overlay: { label: 'Opacity', min: 0, max: 1, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%` },
  sfx: { label: 'Volume', min: 0, max: 2, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%` },
}
const MOTIONS = ['center', 'in', 'out', 'left', 'right', 'up', 'down']

const TRANSFORM_ROWS = ['Position', 'Scale', 'Rotation', 'Crop', 'Flip']

export default function Inspector({
  project, clip, track, item, onClose, onMutated, onRendering, onPendingGen,
}) {
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [val, setVal] = useState(null)
  const [motion, setMotion] = useState('center')

  useEffect(() => {
    setPrompt(cleanPrompt(item))
    setVal(item.value ?? null)
    setMotion(item.motion || 'center')
  }, [item])

  const key = track?.key
  const editable = track?.editable
  const isBroll = key === 'broll'
  const gen = item.gen || null
  const generated = item.generated
  const tune = TUNE[key]

  async function call(fn, optimistic) {
    if (busy) return
    setBusy(true)
    onRendering?.(true)
    try {
      const res = await fn()
      const r = res?.result
      if (r?.ok === false) alert(r.error || 'Edit failed')
      else { onMutated?.(res); if (optimistic) optimistic() }
    } catch (e) { alert(String(e.message || e)) }
    finally { setBusy(false); onRendering?.(false) }
  }

  function retune(nextVal, nextMotion) {
    return call(() => editEvent(project, {
      clip_id: clip, stage: key, index: item.idx,
      value: nextVal != null ? nextVal : undefined,
      motion: nextMotion != null ? nextMotion : undefined,
    }, clip))
  }

  function remove() {
    return call(() => deleteEvent(
      project, { clip_id: clip, stage: key, index: item.idx }, clip), onClose)
  }

  // Regenerate a b-roll slot non-blocking (in-lane "Generating…" via App).
  async function rerun(newSeed) {
    if (busy) return
    setBusy(true)
    try {
      const res = await rerunBroll(
        project, { clip_id: clip, idx: item.idx, prompt, seed: newSeed }, clip,
        true)
      if (res?.job_id && onPendingGen) {
        onPendingGen({
          jobId: res.job_id, clip, lane: 'broll',
          start: item.start ?? 0, end: item.end ?? (item.start ?? 0) + 4,
          label: prompt || item.label,
        })
        onClose?.()
      } else {
        const r = res?.result
        if (r?.ok === false) alert(r.error || 'Rerun failed')
        else { onMutated?.(res); onClose?.() }
      }
    } catch (e) { alert(String(e.message || e)) }
    finally { setBusy(false) }
  }

  const canDelete = editable && item.idx != null

  return (
    <div className="inspector">
      <div className="insp-head">
        <span className="insp-title">{track?.label || 'Event'}</span>
        <button className="insp-x" onClick={onClose}>×</button>
      </div>

      <div className="insp-row">
        <span className="insp-k">Window</span>
        <span className="insp-v">
          {(item.start ?? 0).toFixed(2)}
          {item.end != null ? `–${item.end.toFixed(2)}` : ''}s
        </span>
      </div>

      {/* retune slider for tunable lanes (zoom / overlay / sfx) */}
      {tune && editable && (
        <div className="insp-tune">
          <div className="insp-row">
            <span className="insp-k">{tune.label}</span>
            <span className="insp-v mono">{tune.fmt(val ?? tune.min)}</span>
          </div>
          <input type="range" min={tune.min} max={tune.max} step={tune.step}
            value={val ?? tune.min} disabled={busy}
            onChange={(e) => setVal(+e.target.value)}
            onMouseUp={(e) => retune(+e.target.value, null)} />
          {key === 'zoom' && (
            <div className="seg sm">
              {MOTIONS.map((m) => (
                <button key={m} className={motion === m ? 'on' : ''} disabled={busy}
                  onClick={() => { setMotion(m); retune(null, m) }}>{m}</button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* generated b-roll: prompt + seed + Regenerate (the AI Edit surface) */}
      {isBroll && generated && (
        <div className="insp-ai">
          <div className="insp-aihdr">AI Edit</div>
          {gen?.model && (
            <div className="insp-row">
              <span className="insp-k">Model</span>
              <span className="insp-v mono">{gen.model}</span>
            </div>
          )}
          <div className="insp-row">
            <span className="insp-k">Seed</span>
            <span className="insp-v mono">
              {gen?.seed != null ? gen.seed : 'random'}
            </span>
          </div>
          <label className="insp-k" style={{ marginTop: 10 }}>Prompt</label>
          <textarea className="ap-input" rows={3} value={prompt}
            onChange={(e) => setPrompt(e.target.value)} />
          <div className="insp-actions">
            <button className="btn" disabled={busy}
              onClick={() => rerun(-1)} title="Same prompt, new random seed">
              ↻ New take
            </button>
            <button className="btn primary" disabled={busy}
              onClick={() => rerun(-1)} title="Use the edited prompt">
              Regenerate
            </button>
          </div>
        </div>
      )}

      {!editable && !generated && (
        <div className="insp-row">
          <span className="insp-k">Label</span>
          <span className="insp-v">{item.label}</span>
        </div>
      )}

      {/* honest placeholder: transforms our stage-recipe renderer can't do yet */}
      <div className="insp-transform">
        <div className="insp-aihdr">Transform</div>
        {TRANSFORM_ROWS.map((r) => (
          <div className="insp-row dim" key={r}>
            <span className="insp-k">{r}</span>
            <span className="insp-v mono">—</span>
          </div>
        ))}
        <p className="insp-hint">Free transforms aren’t supported in the
          stage-recipe model yet.</p>
      </div>

      {canDelete && (
        <button className="btn danger block" disabled={busy} onClick={remove}>
          Delete event
        </button>
      )}
    </div>
  )
}
