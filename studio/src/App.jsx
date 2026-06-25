import { useCallback, useEffect, useRef, useState } from 'react'
import { getState, getTimeline } from './api.js'
import AssetPanel from './components/AssetPanel.jsx'
import Inspector from './components/Inspector.jsx'
import Preview from './components/Preview.jsx'
import Timeline from './components/Timeline.jsx'

function useQueryParam(name) {
  const params = new URLSearchParams(window.location.search)
  return params.get(name)
}

export default function App() {
  const project = useQueryParam('project')
  const [state, setState] = useState(null)
  const [clipId, setClipId] = useState(null)
  const [timeline, setTimeline] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [rendering, setRendering] = useState(false)  // any server render/gen
  const [selected, setSelected] = useState(null)  // { track, item } | null
  const videoRef = useRef(null)

  // Load the project state (clip list + source meta) on mount.
  const reloadState = useCallback(async () => {
    if (!project) { setError('No ?project= in the URL.'); return }
    try {
      const s = await getState(project)
      setState(s)
      setClipId((cur) => cur ?? s.active_clip ?? s.clips?.[0]?.id ?? null)
    } catch (e) { setError(String(e.message || e)) }
  }, [project])

  useEffect(() => { reloadState() }, [reloadState])

  // Load the active clip's multi-track timeline whenever the clip changes.
  const reloadTimeline = useCallback(async () => {
    if (!project || !clipId) return
    try {
      const t = await getTimeline(project, clipId)
      setTimeline(t)
    } catch (e) { setError(String(e.message || e)) }
  }, [project, clipId])

  useEffect(() => { reloadTimeline() }, [reloadTimeline])

  // After a mutation the tool endpoint already returns fresh state+timeline.
  const onMutated = useCallback((res) => {
    if (res?.state) setState(res.state)
    if (res?.timeline) setTimeline(res.timeline)
  }, [])

  // Drop the selection when the clip changes.
  useEffect(() => { setSelected(null) }, [clipId])

  if (error) {
    return (
      <div className="shell">
        <div className="error-card">
          <h2>Couldn’t load the studio</h2>
          <p>{error}</p>
          <p className="hint">
            Open with a project, e.g.{' '}
            <code>/studio2?project=demo_base</code>
          </p>
        </div>
      </div>
    )
  }

  if (!state) return <div className="shell loading">Loading…</div>

  const clips = state.clips || []
  const activeClip = clips.find((c) => c.id === clipId)

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">VibeClip <span className="badge">studio2</span></div>
        <div className="project-name">{state.display_name || state.project}</div>
        <a className="legacy-link" href={`/studio?project=${encodeURIComponent(project)}`}>
          legacy studio →
        </a>
      </header>

      <div className="body">
        <aside className="rail">
          <div className="rail-title">Clips · {clips.length}</div>
          <ul className="clip-list">
            {clips.map((c) => (
              <li
                key={c.id}
                className={c.id === clipId ? 'clip active' : 'clip'}
                onClick={() => { setClipId(c.id); setTimeline(null) }}
              >
                <span className="clip-id">#{c.id}</span>
                <span className="clip-title">{c.title || `clip ${c.id}`}</span>
                <span className={c.rendered ? 'dot ok' : 'dot'} />
              </li>
            ))}
          </ul>
        </aside>

        <main className="stage">
          <Preview
            ref={videoRef}
            project={project}
            clip={clipId}
            timeline={timeline}
            rendered={activeClip?.rendered}
            rendering={rendering}
          />
          {selected && (
            <Inspector
              project={project}
              clip={clipId}
              track={selected.track}
              item={selected.item}
              onClose={() => setSelected(null)}
              onMutated={onMutated}
              onRendering={setRendering}
            />
          )}
          <Timeline
            project={project}
            clip={clipId}
            timeline={timeline}
            videoRef={videoRef}
            busy={busy}
            setBusy={setBusy}
            onMutated={onMutated}
            onReload={reloadTimeline}
            selected={selected}
            onSelectEvent={(track, item) => setSelected({ track, item })}
            onRendering={setRendering}
          />
        </main>

        <AssetPanel
          project={project}
          clip={clipId}
          videoRef={videoRef}
          onMutated={onMutated}
          onRendering={setRendering}
        />
      </div>
    </div>
  )
}
