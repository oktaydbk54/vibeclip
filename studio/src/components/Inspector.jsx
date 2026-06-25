import { useEffect, useState } from 'react'
import { rerunBroll } from '../api.js'

// Floating inspector for a selected timeline event. For a GENERATED b-roll block
// it surfaces the generation metadata (prompt / model / seed) and offers a
// rerun — the Palmier-style "inspect the prompt and tweak from there", but in
// the browser. Other tracks show read-only details.
function cleanPrompt(item) {
  const g = item.gen
  if (g && g.prompt) return g.prompt
  let q = item.query || item.label || ''
  if (q.toLowerCase().startsWith('ai:')) q = q.slice(3).trim()
  return q
}

export default function Inspector({ project, clip, track, item, onClose, onMutated }) {
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { setPrompt(cleanPrompt(item)) }, [item])

  const isBroll = track?.key === 'broll'
  const gen = item.gen || null
  const generated = item.generated

  async function rerun(newSeed) {
    if (busy) return
    setBusy(true)
    try {
      const res = await rerunBroll(
        project, { clip_id: clip, idx: item.idx, prompt, seed: newSeed }, clip)
      const r = res?.result
      if (r?.ok === false) alert(r.error || 'Rerun failed')
      else { onMutated?.(res); onClose?.() }
    } catch (e) { alert(String(e.message || e)) }
    finally { setBusy(false) }
  }

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

      {isBroll && generated ? (
        <>
          {gen?.model && (
            <div className="insp-row">
              <span className="insp-k">Model</span>
              <span className="insp-v mono">{gen.model}</span>
            </div>
          )}
          {gen?.provider && !gen?.model && (
            <div className="insp-row">
              <span className="insp-k">Source</span>
              <span className="insp-v mono">{gen.provider}</span>
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
          {busy && <div className="insp-busy"><span className="spin" />Regenerating…</div>}
          <div className="insp-actions">
            <button className="btn" disabled={busy}
              onClick={() => rerun(-1)} title="Same prompt, new random seed">
              ↻ Rerun (new take)
            </button>
            <button className="btn primary" disabled={busy}
              onClick={() => rerun(-1)} title="Use the edited prompt">
              Regenerate
            </button>
          </div>
          <p className="insp-hint">
            Rerun keeps this slot’s window and replaces the footage — a fresh
            generation (~20–60s).
          </p>
        </>
      ) : (
        <div className="insp-row">
          <span className="insp-k">Label</span>
          <span className="insp-v">{item.label}</span>
        </div>
      )}
    </div>
  )
}
