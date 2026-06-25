import { useEffect, useState } from 'react'
import { deleteEvent, editEvent, rerunBroll, runTool } from '../api.js'

// Floating inspector for a selected timeline event, organized into Palmier's
// three tabs — Video · Audio · AI Edit:
//   • Video — the event window, a retune slider for tunable visual lanes (zoom
//     strength + motion, overlay opacity), a clip-level PLAYBACK Speed control,
//     and an honest DISABLED Transform block (our renderer is a stage-recipe,
//     not a free compositor, so we don't fake position/scale/rotation/crop).
//   • Audio — clip-level note + a Volume slider for the sfx lane.
//   • AI Edit — generated b-roll's prompt / seed / Regenerate (async).
// Every editable event keeps a Delete. The active tab defaults to the one that
// fits the selection (audio lane → Audio, generated b-roll → AI Edit, else
// Video) but the user can switch freely.

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
const AUDIO_KEYS = new Set(['sfx', 'music', 'ambience'])

export default function Inspector({
  project, clip, track, item, speed, onClose, onMutated, onRendering,
  onPendingGen,
}) {
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [val, setVal] = useState(null)
  const [motion, setMotion] = useState('center')
  const [spd, setSpd] = useState(1)
  const [tab, setTab] = useState('video')

  const key = track?.key
  const editable = track?.editable
  const isBroll = key === 'broll'
  const generated = item.generated
  const gen = item.gen || null
  const tune = TUNE[key]
  const isAudio = AUDIO_KEYS.has(key)

  // Reset the form and pick the tab that best fits the new selection.
  useEffect(() => {
    setPrompt(cleanPrompt(item))
    setVal(item.value ?? null)
    setMotion(item.motion || 'center')
    setTab(isBroll && generated ? 'ai' : isAudio ? 'audio' : 'video')
  }, [item, isBroll, generated, isAudio])

  // Clip-level speed comes from the timeline payload; keep the slider in sync.
  useEffect(() => { setSpd(speed ?? 1) }, [speed])

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

  // Clip-level constant speed (set_speed re-renders the clip + keeps captions
  // in sync). This is a property of the whole clip, not the selected event.
  function applySpeed(f) {
    return call(() => runTool(
      project, 'set_speed', { clip_id: clip, factor: f }, clip))
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
  const TABS = [['video', 'Video'], ['audio', 'Audio'], ['ai', 'AI Edit']]

  return (
    <div className="inspector">
      <div className="insp-head">
        <span className="insp-title">{track?.label || 'Event'}</span>
        <button className="insp-x" onClick={onClose}>×</button>
      </div>

      <div className="insp-tabs seg sm">
        {TABS.map(([id, label]) => (
          <button key={id} className={tab === id ? 'on' : ''}
            onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      <div className="insp-row">
        <span className="insp-k">Window</span>
        <span className="insp-v">
          {(item.start ?? 0).toFixed(2)}
          {item.end != null ? `–${item.end.toFixed(2)}` : ''}s
        </span>
      </div>

      {tab === 'video' && (
        <>
          {/* retune slider for tunable visual lanes (zoom / overlay) */}
          {tune && editable && !isAudio && (
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
                    <button key={m} className={motion === m ? 'on' : ''}
                      disabled={busy}
                      onClick={() => { setMotion(m); retune(null, m) }}>{m}</button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* clip-level PLAYBACK speed (captions kept in sync) */}
          <div className="insp-tune">
            <div className="insp-aihdr">Playback</div>
            <div className="insp-row">
              <span className="insp-k">Speed</span>
              <span className="insp-v mono">{(spd ?? 1).toFixed(2)}×</span>
            </div>
            <input type="range" min={0.25} max={4} step={0.05}
              value={spd ?? 1} disabled={busy}
              onChange={(e) => setSpd(+e.target.value)}
              onMouseUp={(e) => applySpeed(+e.target.value)} />
            <p className="insp-hint">Whole-clip speed · captions rescale with it.</p>
          </div>

          {/* honest placeholder: transforms our stage-recipe can't do yet */}
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
        </>
      )}

      {tab === 'audio' && (
        <>
          {tune && editable && isAudio ? (
            <div className="insp-tune">
              <div className="insp-row">
                <span className="insp-k">{tune.label}</span>
                <span className="insp-v mono">{tune.fmt(val ?? tune.min)}</span>
              </div>
              <input type="range" min={tune.min} max={tune.max} step={tune.step}
                value={val ?? tune.min} disabled={busy}
                onChange={(e) => setVal(+e.target.value)}
                onMouseUp={(e) => retune(+e.target.value, null)} />
            </div>
          ) : (
            <p className="insp-hint">No per-event audio controls for this lane.
              Drop music/SFX from the library, or use the chat to mix.</p>
          )}
        </>
      )}

      {tab === 'ai' && (
        isBroll && generated ? (
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
        ) : (
          <p className="insp-hint">AI edits are available on generated b-roll.
            Generate footage from the Library, then select it here to re-prompt
            or reroll it.</p>
        )
      )}

      {canDelete && (
        <button className="btn danger block" disabled={busy} onClick={remove}>
          Delete event
        </button>
      )}
    </div>
  )
}
