import { useEffect, useRef, useState } from 'react'
import { runTool } from '../api.js'

// Color per track key (top→bottom mirrors chat/timeline_view.TRACK_ORDER).
const LANE_COLOR = {
  zoom: '#f6c453', splitscreen: '#8b9cff', broll: '#5ad1c4',
  overlay: '#c08bff', brand: '#ff9f6e', subtitles: '#7c5cff',
  fx: '#ff6ea9', sfx: '#ffce5a', music: '#69d27a', ambience: '#5aa9ff',
  fade: '#9aa3b2',
}

function pct(x, dur) {
  if (!dur) return 0
  return Math.max(0, Math.min(100, (x / dur) * 100))
}

export default function Timeline({
  project, clip, timeline, videoRef, busy, setBusy, onMutated,
  selected, onSelectEvent, onRendering,
}) {
  const dur = timeline?.duration || 0
  const laneWrapRef = useRef(null)
  const [playhead, setPlayhead] = useState(0)
  // Trim handles live in player seconds; null = at the clip's natural edges.
  const [trim, setTrim] = useState(null)   // { head, tail } | null
  const dragRef = useRef(null)              // 'head' | 'tail' | null

  // Reset trim + playhead when the clip or its timeline reloads.
  useEffect(() => { setTrim(null); setPlayhead(0) }, [clip, timeline?.media_url])

  // Keep the playhead synced to the <video>.
  useEffect(() => {
    const v = videoRef?.current
    if (!v) return
    const onTime = () => setPlayhead(v.currentTime || 0)
    v.addEventListener('timeupdate', onTime)
    return () => v.removeEventListener('timeupdate', onTime)
  }, [videoRef, timeline?.media_url])

  // Window-level drag listeners for the trim handles. MUST run unconditionally
  // (before any early return) so the hook order stays stable across renders.
  useEffect(() => {
    function onMove(e) {
      if (!dragRef.current) return
      const wrap = laneWrapRef.current
      if (!wrap) return
      const r = wrap.getBoundingClientRect()
      const t = Math.max(0, Math.min(dur, ((e.clientX - r.left) / r.width) * dur))
      // Live preview: scrub the <video> to the handle's frame as you drag, so a
      // trim shows instantly with NO server render — the commit happens only on
      // "Apply trim". This is the fast-preview path for the most common edit.
      const v = videoRef?.current
      if (v && v.readyState >= 1) {
        try { v.pause(); v.currentTime = t } catch { /* mid-load */ }
      }
      setTrim((cur) => {
        const base = cur || { head: 0, tail: dur }
        if (dragRef.current === 'head') {
          return { ...base, head: Math.min(t, base.tail - 0.1) }
        }
        return { ...base, tail: Math.max(t, base.head + 0.1) }
      })
    }
    function onUp() { dragRef.current = null }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [dur])

  // While a trim is staged, keep playback inside [head, tail] so pressing Play
  // previews the trimmed result live — again with no render until commit.
  useEffect(() => {
    const v = videoRef?.current
    if (!v || !trim) return
    const onTime = () => {
      if (v.currentTime > trim.tail + 0.05 || v.currentTime < trim.head - 0.05) {
        v.currentTime = trim.head
      }
    }
    v.addEventListener('timeupdate', onTime)
    return () => v.removeEventListener('timeupdate', onTime)
  }, [trim, videoRef])

  if (!timeline) {
    return <section className="timeline empty">Loading timeline…</section>
  }

  const head = trim?.head ?? 0
  const tail = trim?.tail ?? dur
  const trimmed = trim && (head > 0.001 || tail < dur - 0.001)

  function timeFromClientX(clientX) {
    const wrap = laneWrapRef.current
    if (!wrap) return 0
    const r = wrap.getBoundingClientRect()
    const f = (clientX - r.left) / r.width
    return Math.max(0, Math.min(dur, f * dur))
  }

  function onRulerClick(e) {
    const t = timeFromClientX(e.clientX)
    const v = videoRef?.current
    if (v) v.currentTime = t
    setPlayhead(t)
  }

  function startDrag(which) {
    return (e) => {
      e.preventDefault()
      e.stopPropagation()
      dragRef.current = which
      if (!trim) setTrim({ head: 0, tail: dur })
    }
  }

  // Map the player-time trim onto a SOURCE-time cut and commit via set_cut.
  // cut maps source [cs, ce] → player [0, dur] (plain-cut approximation; the
  // tool re-cuts cleanly from source, so the new range is exact even though
  // jumpcuts make the player↔source ratio slightly nonlinear).
  async function applyTrim() {
    const cut = timeline.cut
    if (!cut || !trimmed || busy) return
    const span = cut.end - cut.start
    const ns = cut.start + (head / dur) * span
    const ne = cut.start + (tail / dur) * span
    setBusy(true)
    onRendering?.(true)
    try {
      const res = await runTool(
        project, 'set_cut',
        { clip_id: clip, start: +ns.toFixed(3), end: +ne.toFixed(3) },
        clip,
      )
      if (res?.result?.ok === false) {
        alert(res.result.error || 'Trim failed')
      } else {
        onMutated(res)
        setTrim(null)
      }
    } catch (e) {
      alert(String(e.message || e))
    } finally {
      setBusy(false)
      onRendering?.(false)
    }
  }

  const tracks = timeline.tracks || []

  return (
    <section className="timeline">
      <div className="tl-toolbar">
        <span className="tl-tc">
          {playhead.toFixed(2)} / {dur.toFixed(2)}s
        </span>
        <div className="tl-actions">
          {trimmed && (
            <>
              <span className="trim-readout">
                trim → {head.toFixed(2)}–{tail.toFixed(2)}s
              </span>
              <button className="btn ghost" disabled={busy}
                onClick={() => setTrim(null)}>Reset</button>
              <button className="btn primary" disabled={busy}
                onClick={applyTrim}>
                {busy ? 'Cutting…' : 'Apply trim'}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="tl-grid">
        <div className="tl-labels">
          <div className="lane-label main">Video</div>
          {tracks.map((t) => (
            <div className="lane-label" key={t.key}>{t.label}</div>
          ))}
        </div>

        <div className="tl-lanes" ref={laneWrapRef}>
          {/* ruler / scrub strip */}
          <div className="ruler" onClick={onRulerClick}>
            {Array.from({ length: Math.max(1, Math.ceil(dur)) + 1 }).map((_, i) => (
              <span className="tick" key={i} style={{ left: `${pct(i, dur)}%` }}>
                <i />{i % 5 === 0 ? <b>{i}s</b> : null}
              </span>
            ))}
          </div>

          {/* main video track with trim handles + dimmed trimmed-away regions */}
          <div className="lane main">
            <div className="clip-block" style={{ left: 0, width: '100%' }}>
              <div className="block-fill" style={{ background: LANE_COLOR.subtitles }} />
            </div>
            {trim && (
              <>
                <div className="trim-shade"
                  style={{ left: 0, width: `${pct(head, dur)}%` }} />
                <div className="trim-shade"
                  style={{ left: `${pct(tail, dur)}%`, right: 0 }} />
              </>
            )}
            <div className="handle head" onPointerDown={startDrag('head')}
              style={{ left: `${pct(head, dur)}%` }} title="Trim head" />
            <div className="handle tail" onPointerDown={startDrag('tail')}
              style={{ left: `${pct(tail, dur)}%` }} title="Trim tail" />
          </div>

          {/* one lane per derived track */}
          {tracks.map((t) => (
            <div className="lane" key={t.key}>
              {t.items.map((it, i) => {
                const start = it.start ?? 0
                const end = it.end ?? (t.kind === 'point' ? start : dur)
                const left = pct(start, dur)
                const width = t.kind === 'point'
                  ? null : Math.max(0.6, pct(end, dur) - left)
                const isSel = selected?.track?.key === t.key &&
                  selected?.item?.idx === it.idx && it.idx != null
                return (
                  <div
                    key={i}
                    className={`item ${t.kind}${isSel ? ' sel' : ''}` +
                      (it.generated ? ' gen' : '')}
                    style={{
                      left: `${left}%`,
                      width: width != null ? `${width}%` : undefined,
                      background: LANE_COLOR[t.key] || '#7c5cff',
                    }}
                    title={`${it.label} (${start.toFixed(2)}${
                      t.kind === 'point' ? '' : `–${end.toFixed(2)}`}s)` +
                      (it.generated ? ' · click to inspect / rerun' : '')}
                    onClick={(e) => {
                      e.stopPropagation()
                      onSelectEvent?.(t, it)
                    }}
                  >
                    {it.generated && <span className="gen-dot">✦</span>}
                    <span className="item-label">{it.label}</span>
                  </div>
                )
              })}
            </div>
          ))}

          {/* playhead spans all lanes */}
          <div className="playhead" style={{ left: `${pct(playhead, dur)}%` }} />
        </div>
      </div>
    </section>
  )
}
