import { useEffect, useState } from 'react'
import { getTranscript } from '../api.js'

// Word-level caption lane: positions each word from /api/v2/transcript, styling
// filler words muted/struck-through (a real "remove filler" affordance later).
// Falls back to the timeline's segment-level subtitle chips if the transcript
// fetch fails or is empty.
function pct(x, dur) {
  return dur ? Math.max(0, Math.min(100, (x / dur) * 100)) : 0
}

export default function CaptionTrack({ project, clip, dur, fallback }) {
  const [words, setWords] = useState(null)

  useEffect(() => {
    let alive = true
    setWords(null)
    if (!project || clip == null) return
    getTranscript(project, clip)
      .then((t) => { if (alive) setWords(t.words || []) })
      .catch(() => { if (alive) setWords([]) })
    return () => { alive = false }
  }, [project, clip])

  if (!words || !words.length) {
    return (
      <>
        {(fallback?.items || []).map((it, i) => {
          const left = pct(it.start ?? 0, dur)
          const w = Math.max(0.6, pct(it.end ?? it.start, dur) - left)
          return (
            <div key={i} className="cap-seg" title={it.label}
              style={{ left: `${left}%`, width: `${w}%` }}>
              <span className="item-label">{it.label}</span>
            </div>
          )
        })}
      </>
    )
  }

  return (
    <>
      {words.map((w) => {
        const left = pct(w.start, dur)
        const width = Math.max(0.4, pct(w.end, dur) - left)
        return (
          <div key={w.i}
            className={`cap-word${w.is_filler ? ' filler' : ''}`}
            style={{ left: `${left}%`, width: `${width}%` }}
            title={w.word + (w.is_filler ? ' · filler' : '')}>
            <span className="item-label">{w.word}</span>
          </div>
        )
      })}
    </>
  )
}
