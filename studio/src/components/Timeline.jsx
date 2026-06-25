import { useEffect, useMemo, useRef, useState } from 'react'
import {
  addBrollFromAsset, addSoundEffect, runTool, setMusic,
} from '../api.js'
import Filmstrip from './Filmstrip.jsx'
import TrackHeader from './TrackHeader.jsx'
import Waveform from './Waveform.jsx'
import CaptionTrack from './CaptionTrack.jsx'

const HEADER_W = 128                 // mirrors --track-header-w
const AUDIO_KEYS = new Set(['music', 'ambience'])
// Canvas needs literal colors (CSS vars don't resolve in 2d context).
const WAVE_COLOR = {
  video: 'rgba(90,209,196,0.55)', music: 'rgba(105,210,122,0.6)',
  ambience: 'rgba(90,169,255,0.6)',
}

// Color per track key — references the semantic --trk-* tokens (tokens.css).
function laneColor(key) {
  return `var(--trk-${key}, var(--accent))`
}

// Pull the artifact version (&v=) out of the media URL so the immutable-cached
// filmstrip sprite busts after a re-render.
function mediaVersion(timeline) {
  const m = (timeline?.media_url || '').match(/[?&]v=([^&]+)/)
  return m ? m[1] : ''
}

function pct(x, dur) {
  if (!dur) return 0
  return Math.max(0, Math.min(100, (x / dur) * 100))
}

// Pick a "nice" label interval (seconds) so labels stay ~64px apart at any zoom.
function niceStep(pxPerSec) {
  const raw = 64 / Math.max(1, pxPerSec)
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]
  return steps.find((s) => s >= raw) || 300
}

function fmtTick(t, step) {
  if (step < 1) return `${t.toFixed(step < 0.5 ? 2 : 1)}s`
  if (t >= 60) {
    const m = Math.floor(t / 60)
    return `${m}:${String(Math.round(t % 60)).padStart(2, '0')}`
  }
  return `${Math.round(t)}s`
}

export default function Timeline({
  project, clip, timeline, videoRef, busy, setBusy, onMutated,
  selected, onSelectEvent, onRendering, pendingGen,
}) {
  const dur = timeline?.duration || 0
  const gridRef = useRef(null)
  const laneWrapRef = useRef(null)
  const [playhead, setPlayhead] = useState(0)
  // Trim handles live in player seconds; null = at the clip's natural edges.
  const [trim, setTrim] = useState(null)   // { head, tail } | null
  const dragRef = useRef(null)              // 'head' | 'tail' | null
  // Per-lane visibility (NLE "hide track") — client-side only.
  const [hidden, setHidden] = useState(() => new Set())
  // Horizontal zoom: pxPerSec = fit * zoom, so zoom=1 fills the viewport and
  // higher values scroll. Snap toggles edge-snapping while trimming.
  const [zoom, setZoom] = useState(1)
  const [snap, setSnap] = useState(true)
  const [viewportW, setViewportW] = useState(0)
  const [dropX, setDropX] = useState(null)   // px x of a drag hovering the lanes
  const ver = mediaVersion(timeline)

  // Track the lane viewport width so "fit" zoom can fill it (and recompute on
  // resize). Re-runs when the grid mounts (timeline arrives).
  useEffect(() => {
    const el = gridRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setViewportW(el.clientWidth))
    ro.observe(el)
    setViewportW(el.clientWidth)
    return () => ro.disconnect()
  }, [timeline])

  const laneViewport = Math.max(200, viewportW - HEADER_W)
  const fitPps = dur > 0 ? laneViewport / dur : 60
  const pxPerSec = fitPps * zoom
  const lanesWidth = Math.max(laneViewport, dur * pxPerSec)

  // Reset transient view state when the clip or its timeline reloads.
  useEffect(() => {
    setTrim(null); setPlayhead(0); setZoom(1); setHidden(new Set())
  }, [clip, timeline?.media_url])

  // Keep the playhead synced to the <video>.
  useEffect(() => {
    const v = videoRef?.current
    if (!v) return
    const onTime = () => setPlayhead(v.currentTime || 0)
    v.addEventListener('timeupdate', onTime)
    return () => v.removeEventListener('timeupdate', onTime)
  }, [videoRef, timeline?.media_url])

  // Snap candidates (seconds): clip edges, every item edge, markers, integer
  // seconds. Kept in a ref so the drag listener reads fresh values without
  // re-subscribing on every zoom/selection change.
  const snapTargets = useMemo(() => {
    const out = new Set([0, dur])
    for (const t of timeline?.tracks || []) {
      for (const it of t.items || []) {
        if (it.start != null) out.add(+it.start)
        if (it.end != null) out.add(+it.end)
      }
    }
    for (const m of timeline?.markers || []) out.add(+m.t)
    for (let s = 0; s <= dur; s++) out.add(s)
    return [...out]
  }, [timeline, dur])
  const snapRef = useRef(snap); snapRef.current = snap
  const ppsRef = useRef(pxPerSec); ppsRef.current = pxPerSec
  const targetsRef = useRef(snapTargets); targetsRef.current = snapTargets

  function maybeSnap(t) {
    if (!snapRef.current) return t
    const tol = 7 / Math.max(1, ppsRef.current)   // ~7px
    let best = t, bestD = tol
    for (const c of targetsRef.current) {
      const d = Math.abs(c - t)
      if (d < bestD) { bestD = d; best = c }
    }
    return best
  }

  // Window-level drag listeners for the trim handles. MUST run unconditionally
  // (before any early return) so the hook order stays stable across renders.
  useEffect(() => {
    function onMove(e) {
      if (!dragRef.current) return
      const wrap = laneWrapRef.current
      if (!wrap) return
      const r = wrap.getBoundingClientRect()
      let t = Math.max(0, Math.min(dur, ((e.clientX - r.left) / r.width) * dur))
      t = maybeSnap(t)
      // Live preview: scrub the <video> to the handle's frame as you drag, so a
      // trim shows instantly with NO server render — commit happens on "Apply".
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
  const step = niceStep(pxPerSec)
  const tickCount = Math.floor(dur / step) + 1

  function timeFromClientX(clientX) {
    const wrap = laneWrapRef.current
    if (!wrap) return 0
    const r = wrap.getBoundingClientRect()
    return Math.max(0, Math.min(dur, ((clientX - r.left) / r.width) * dur))
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

  async function addMarker() {
    if (busy) return
    try {
      const res = await runTool(
        project, 'add_marker',
        { clip_id: clip, t: +playhead.toFixed(3), label: '' }, clip)
      if (res?.result?.ok !== false) onMutated(res)
    } catch (e) { alert(String(e.message || e)) }
  }

  async function removeMarker(id) {
    try {
      const res = await runTool(
        project, 'remove_marker', { clip_id: clip, marker_id: id }, clip)
      if (res?.result?.ok !== false) onMutated(res)
    } catch (e) { alert(String(e.message || e)) }
  }

  // ---- drag-to-timeline (drop a library asset) ---------------------------
  function onLanesDragOver(e) {
    if (!Array.from(e.dataTransfer.types).includes('application/json')) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    const wrap = laneWrapRef.current
    if (wrap) setDropX(e.clientX - wrap.getBoundingClientRect().left)
  }

  async function onLanesDrop(e) {
    e.preventDefault()
    setDropX(null)
    let data
    try { data = JSON.parse(e.dataTransfer.getData('application/json')) }
    catch { return }
    if (!data || !clip) return
    const t = timeFromClientX(e.clientX)
    const trackKey = e.target.closest?.('.lane')?.dataset?.track || ''
    onRendering?.(true)
    try {
      let res
      if (data.kind === 'audio') {
        res = trackKey === 'sfx'
          ? await addSoundEffect(project, { clip_id: clip, time: t, file: data.path })
          : await setMusic(project, { clip_id: clip, file: data.path })
      } else {
        // B-roll can't cover the hook (first 3s) — clamp the drop start.
        const bs = Math.max(3.0, t)
        res = await addBrollFromAsset(project, {
          clip_id: clip, file: data.path,
          start: +bs.toFixed(2), end: +Math.min(dur, bs + 4).toFixed(2),
        })
      }
      if (res?.result?.ok === false) alert(res.result.error || 'Drop failed')
      else onMutated(res)
    } catch (err) { alert(String(err.message || err)) }
    finally { onRendering?.(false) }
  }

  // Map the player-time trim onto a SOURCE-time cut and commit via set_cut.
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
        { clip_id: clip, start: +ns.toFixed(3), end: +ne.toFixed(3) }, clip)
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
  const markers = timeline.markers || []

  return (
    <section className="timeline">
      <div className="tl-toolbar">
        <div className="tl-tools">
          <button className="tool on" title="Select">▤</button>
          <button className="tool" title="Add marker at playhead"
            onClick={addMarker}>◇</button>
          <span className="tl-tc">
            {playhead.toFixed(2)} / {dur.toFixed(2)}s
          </span>
        </div>
        <div className="tl-actions">
          {trimmed && (
            <>
              <span className="trim-readout">
                trim → {head.toFixed(2)}–{tail.toFixed(2)}s
              </span>
              <button className="btn ghost" disabled={busy}
                onClick={() => setTrim(null)}>Reset</button>
              <button className="btn primary" disabled={busy}
                onClick={applyTrim}>{busy ? 'Cutting…' : 'Apply trim'}</button>
            </>
          )}
          <button className={`tool${snap ? ' on' : ''}`} title="Snap to edges"
            onClick={() => setSnap((s) => !s)}>⤢</button>
          <button className="tool" title="Fit to window"
            onClick={() => setZoom(1)}>⊟</button>
          <input className="zoom-slider" type="range" min="1" max="10" step="0.1"
            value={zoom} title="Zoom"
            onChange={(e) => setZoom(+e.target.value)} />
        </div>
      </div>

      <div className="tl-grid" ref={gridRef}>
        <div className="tl-labels">
          <TrackHeader main label="Video" color="var(--accent-2)" />
          {tracks.map((t) => (
            <TrackHeader
              key={t.key}
              trackKey={t.key}
              label={t.label}
              color={laneColor(t.key)}
              visible={!hidden.has(t.key)}
              onToggleVisible={() => setHidden((cur) => {
                const next = new Set(cur)
                if (next.has(t.key)) next.delete(t.key); else next.add(t.key)
                return next
              })}
            />
          ))}
        </div>

        <div className="tl-lanes" ref={laneWrapRef} style={{ width: lanesWidth }}
          onDragOver={onLanesDragOver}
          onDragLeave={() => setDropX(null)}
          onDrop={onLanesDrop}>
          {/* ruler / scrub strip with adaptive ticks + markers */}
          <div className="ruler" onClick={onRulerClick}>
            {Array.from({ length: tickCount }).map((_, i) => {
              const t = i * step
              return (
                <span className="tick" key={i} style={{ left: `${pct(t, dur)}%` }}>
                  <i /><b>{fmtTick(t, step)}</b>
                </span>
              )
            })}
            {markers.map((m) => (
              <span className="marker" key={m.id}
                style={{ left: `${pct(m.t, dur)}%` }}
                title={`${m.label || 'marker'} · click to remove`}
                onClick={(e) => { e.stopPropagation(); removeMarker(m.id) }} />
            ))}
          </div>

          {/* main video track: filmstrip + audio waveform behind trim handles */}
          <div className="lane main video">
            <Filmstrip project={project} clip={clip} version={ver} />
            <div className="video-wave">
              <Waveform url={timeline.media_url} width={lanesWidth} height={15}
                color={WAVE_COLOR.video} />
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
          {tracks.map((t) => {
            const off = hidden.has(t.key)
            const isAudio = AUDIO_KEYS.has(t.key)
            // Async generation in flight on this lane → a pending "Generating…"
            // block sits where the result will land (Palmier's in-timeline gen).
            const genHere = pendingGen && pendingGen.clip === clip
              && pendingGen.lane === t.key
            const gLeft = genHere ? pct(pendingGen.start ?? 0, dur) : 0
            const gWidth = genHere
              ? Math.max(2, pct(pendingGen.end ?? dur, dur) - gLeft) : 0
            return (
              <div className={`lane${off ? ' off' : ''}${isAudio ? ' audio' : ''}`}
                key={t.key} data-track={t.key}>
                {genHere && (
                  <div className="gen-block"
                    style={{ left: `${gLeft}%`, width: `${gWidth}%` }}>
                    <span className="gen-shimmer" />
                    <span className="gen-block-label">✦ Generating…</span>
                  </div>
                )}
                {t.key === 'subtitles' ? (
                  <CaptionTrack project={project} clip={clip} dur={dur}
                    fallback={t} />
                ) : isAudio ? (
                  <Waveform url={timeline.media_url} width={lanesWidth} height={28}
                    color={WAVE_COLOR[t.key]} />
                ) : t.items.map((it, i) => {
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
                        background: laneColor(t.key),
                      }}
                      title={`${it.label} (${start.toFixed(2)}${
                        t.kind === 'point' ? '' : `–${end.toFixed(2)}`}s)` +
                        (it.generated ? ' · click to inspect / rerun' : '')}
                      onClick={(e) => { e.stopPropagation(); onSelectEvent?.(t, it) }}
                    >
                      {it.generated && <span className="gen-dot">✦</span>}
                      <span className="item-label">{it.label}</span>
                      {/* zoom is an animated property: mark its ramp-in/out
                          keyframes with diamonds (Palmier's ◆ Keyframes). */}
                      {t.key === 'zoom' && (
                        <>
                          <span className="kf kf-in" title="keyframe (ramp in)" />
                          <span className="kf kf-out" title="keyframe (ramp out)" />
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
            )
          })}

          {/* drop indicator while dragging an asset over the lanes */}
          {dropX != null && (
            <div className="drop-line" style={{ left: `${dropX}px` }} />
          )}

          {/* playhead spans all lanes */}
          <div className="playhead" style={{ left: `${pct(playhead, dur)}%` }} />
        </div>
      </div>
    </section>
  )
}
