import { useEffect, useRef, useState } from 'react'
import {
  addBrollFromAsset, applyStyle, generateAsset, generateVariations, getModels,
  imageToVideo, insertGeneratedClip, listAssets, listStyles, moveAssetToFolder,
  organizeAssets, subscribeEvents, uploadAsset,
} from '../api.js'

// The library + generation surface (Palmier's signature region): a tabbed
// browser — Media · Generate · Audio · Styles — with generation docked in,
// reference-image generation (@-mention-a-photo), parallel variations, virtual
// folders, and every asset draggable onto the timeline. Generation runs on the
// job worker (async) so the panel never blocks; pending shimmer cards resolve
// when the job_done event lands.

function onCardDragStart(a) {
  return (e) => {
    e.dataTransfer.setData('application/json', JSON.stringify(
      { id: a.id, kind: a.kind, path: a.path || '' }))
    e.dataTransfer.effectAllowed = 'copy'
  }
}

function AssetCard({
  a, genAvailable, clip, folders, onI2V, onAdd, onRef, onMove, onInsert,
}) {
  const [menu, setMenu] = useState(false)
  return (
    <div className="asset-card" draggable onDragStart={onCardDragStart(a)}
      title="Drag onto the timeline">
      <div className="thumb">
        {a.thumb ? <img src={a.thumb} alt={a.description} />
          : <span className="kind">{a.kind}</span>}
        <span className="kind-badge">{a.kind}</span>
        {a.name?.startsWith('gen_') && <span className="ai-badge">AI</span>}
        <span className="drag-hint">⠿ drag</span>
      </div>
      <div className="asset-desc" title={a.description}>
        {a.description || a.name || `asset ${a.id}`}
      </div>
      <div className="asset-actions">
        {a.kind === 'image' && genAvailable && (
          <button onClick={() => onRef(a)} title="Generate from this photo">
            ◎ Ref</button>
        )}
        {a.kind === 'image' && genAvailable && (
          <button onClick={() => onI2V(a.id)}>→ Video</button>
        )}
        {(a.kind === 'image' || a.kind === 'video' || a.kind === 'audio')
          && clip && (
          <button onClick={() => onAdd(a)} title="Overlay on the current clip">
            + TL</button>
        )}
        {a.kind === 'video' && clip && (
          <button onClick={() => onInsert(a)}
            title="Insert as a new clip after the current one (shifts later clips)">
            ⎀ Clip</button>
        )}
        <button className="af-more" onClick={() => setMenu((m) => !m)}
          title="Move to folder">⋯</button>
      </div>
      {menu && (
        <div className="af-menu" onMouseLeave={() => setMenu(false)}>
          <div className="af-menu-t">Move to folder</div>
          {folders.map((f) => (
            <button key={f} className={a.folder === f ? 'on' : ''}
              onClick={() => { onMove(a, f); setMenu(false) }}>{f}</button>
          ))}
          {a.folder && (
            <button onClick={() => { onMove(a, ''); setMenu(false) }}>
              ✕ Ungroup</button>
          )}
        </div>
      )}
    </div>
  )
}

export default function AssetPanel({
  project, clip, videoRef, onMutated, onRendering,
}) {
  const [tab, setTab] = useState('media')
  const [assets, setAssets] = useState([])
  const [models, setModels] = useState(null)
  const [styles, setStyles] = useState([])
  const [busy, setBusy] = useState('')
  const [pending, setPending] = useState(0)       // # of shimmer cards in flight
  const [filter, setFilter] = useState('all')     // media kind filter
  const [folder, setFolder] = useState('all')     // folder filter
  const fileRef = useRef(null)
  const jobsRef = useRef(new Set())               // our in-flight gen job ids

  // Generate form
  const [kind, setKind] = useState('image')
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState('')
  const [seed, setSeed] = useState('')
  const [count, setCount] = useState(1)
  const [hints, setHints] = useState('')
  const [reference, setReference] = useState(null)  // { id, thumb, desc }
  const [negative, setNegative] = useState('')
  const [strength, setStrength] = useState('')

  // image-to-video target
  const [i2v, setI2v] = useState(null)
  const [i2vPrompt, setI2vPrompt] = useState('')

  async function refreshAssets() {
    try { setAssets((await listAssets()).assets || []) } catch { /* ignore */ }
  }

  useEffect(() => {
    getModels().then(setModels).catch(() => setModels({ available: false }))
    listStyles().then((r) => setStyles(r.styles || [])).catch(() => setStyles([]))
    refreshAssets()
  }, [])

  // Resolve async generation jobs: when one of OUR jobs finishes, refresh the
  // library and clear its shimmer card(s).
  useEffect(() => {
    const unsub = subscribeEvents((evt) => {
      const job = evt.job
      if (!job || evt.type !== 'job_done' || !jobsRef.current.has(job.id)) return
      jobsRef.current.delete(job.id)
      setPending(0)
      setBusy('')
      refreshAssets()
      const r = job.result?.result
      if (r && r.ok === false) alert(r.error || 'Generation failed')
    })
    return unsub
  }, [])

  // Default the model dropdown to the catalog default for the current kind.
  useEffect(() => {
    if (!models) return
    const list = models[kind] || []
    const def = list.find((m) => m.default) || list[0]
    setModel(def ? def.id : '')
  }, [models, kind])

  const genAvailable = models?.available
  const modelList = (models && models[kind]) || []
  const i2vModels = (models && models.i2v) || []
  const folders = [...new Set(assets.map((a) => a.folder).filter(Boolean))].sort()

  const visible = assets.filter((a) => {
    if (tab === 'audio') return a.kind === 'audio'
    if (a.kind === 'audio') return false
    if (filter !== 'all' && a.kind !== filter) return false
    if (folder === 'all') return true
    if (folder === '_none') return !a.folder
    return a.folder === folder
  })

  async function onGenerate() {
    if (!prompt.trim() || !project || busy) return
    const n = Math.max(1, Math.min(8, +count || 1))
    setBusy(n > 1 ? `Generating ${n} variations…` : `Generating ${kind}…`)
    setPending(n)
    setTab('media')
    try {
      const common = {
        prompt: prompt.trim(), kind, model,
        reference: reference?.id || '', negative: negative.trim(),
      }
      const res = n > 1
        ? await generateVariations(project,
          { ...common, count: n, hints: hints.trim() }, clip)
        : await generateAsset(project,
          { ...common, seed: seed ? +seed : -1,
            strength: strength ? +strength : -1 }, clip, true)
      if (res?.job_id) jobsRef.current.add(res.job_id)
      else { setPending(0); setBusy(''); await refreshAssets() }
      setPrompt('')
    } catch (e) { setPending(0); setBusy(''); alert(String(e.message || e)) }
  }

  async function onUpload(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy('Uploading…')
    try { await uploadAsset(file); await refreshAssets() }
    catch (err) { alert(String(err.message || err)) }
    finally { setBusy(''); if (fileRef.current) fileRef.current.value = '' }
  }

  async function onImageToVideo(assetId) {
    if (busy) return
    const modelId = (i2vModels.find((m) => m.default) || i2vModels[0])?.id || ''
    setBusy('Animating image → video…')
    setPending(1)
    try {
      const res = await imageToVideo(
        project, { asset_id: assetId, prompt: i2vPrompt.trim(), model: modelId },
        clip)
      const r = res?.result
      if (r?.ok === false) alert(r.error || 'Image-to-video failed')
      else { setI2v(null); setI2vPrompt(''); await refreshAssets() }
    } catch (e) { alert(String(e.message || e)) }
    finally { setBusy(''); setPending(0) }
  }

  async function onAddToTimeline(asset) {
    if (!clip || busy) return
    const t = Math.max(3.05, videoRef?.current?.currentTime || 0)
    setBusy('Placing on timeline…')
    onRendering?.(true)
    try {
      const res = await addBrollFromAsset(
        project, { clip_id: clip, file: asset.path || '', start: +t.toFixed(2),
                   end: +(t + 4).toFixed(2) })
      const r = res?.result
      if (r?.ok === false) alert(r.error || 'Could not place on timeline')
      else onMutated?.(res)
    } catch (e) { alert(String(e.message || e)) }
    finally { setBusy(''); onRendering?.(false) }
  }

  // Insert a library video into the clip SEQUENCE as a new clip right after the
  // current one — the time-inserting generative insert (later clips shift).
  async function onInsertClip(asset) {
    if (!clip || busy) return
    setBusy('Inserting as a clip…')
    onRendering?.(true)
    try {
      const res = await insertGeneratedClip(
        project, { after: clip, assetId: asset.id }, clip)
      const r = res?.result
      if (r?.ok === false) alert(r.error || 'Could not insert clip')
      else onMutated?.(res)
    } catch (e) { alert(String(e.message || e)) }
    finally { setBusy(''); onRendering?.(false) }
  }

  async function onApplyStyle(name) {
    if (!clip || busy) return
    setBusy(`Applying ${name}…`)
    onRendering?.(true)
    try {
      const res = await applyStyle(project, { clip_id: clip, style: name })
      const r = res?.result
      if (r?.ok === false) alert(r.error || 'Could not apply style')
      else onMutated?.(res)
    } catch (e) { alert(String(e.message || e)) }
    finally { setBusy(''); onRendering?.(false) }
  }

  async function onMove(asset, dest) {
    try {
      await moveAssetToFolder(project, { asset_id: asset.id, folder: dest })
      await refreshAssets()
    } catch (e) { alert(String(e.message || e)) }
  }

  async function onOrganize() {
    if (busy) return
    setBusy('Sorting your media…')
    try {
      const res = await organizeAssets(project)
      if (res?.job_id) jobsRef.current.add(res.job_id)
      else await refreshAssets()
    } catch (e) { alert(String(e.message || e)) }
    // busy/refresh clear on job_done
  }

  function useAsReference(a) {
    setReference({ id: a.id, thumb: a.thumb, desc: a.description })
    setKind('image')
    setTab('generate')
  }

  const TABS = ['media', 'generate', 'audio', 'styles']

  return (
    <aside className="asset-panel">
      <div className="ap-tabs">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? 'on' : ''}
            onClick={() => setTab(t)}>
            {t === 'media' ? `Media · ${assets.filter((a) => a.kind !== 'audio').length}`
              : t === 'audio' ? 'Audio'
              : t === 'styles' ? 'Styles'
              : 'Generate'}
          </button>
        ))}
      </div>

      {busy && <div className="ap-busy"><span className="spin" />{busy}</div>}

      {(tab === 'media' || tab === 'audio') && (
        <div className="ap-body">
          <div className="ap-toprow">
            <button className="btn block"
              onClick={() => fileRef.current?.click()}>
              ⬆ Upload {tab === 'audio' ? 'audio' : 'image / video / audio'}
            </button>
          </div>
          <input ref={fileRef} type="file" hidden
            accept="image/*,video/*,audio/*" onChange={onUpload} />
          {tab === 'media' && (
            <>
              <div className="folder-rail">
                {[['all', 'All'], ['_none', 'Ungrouped'],
                  ...folders.map((f) => [f, f])].map(([id, label]) => (
                  <button key={id} className={folder === id ? 'on' : ''}
                    onClick={() => setFolder(id)}>{label}</button>
                ))}
                <button className="fr-organize" disabled={!!busy}
                  onClick={onOrganize} title="Let the agent sort your media">
                  ✦ Organize</button>
              </div>
              <div className="seg sm">
                {['all', 'image', 'video'].map((f) => (
                  <button key={f} className={filter === f ? 'on' : ''}
                    onClick={() => setFilter(f)}>
                    {f[0].toUpperCase() + f.slice(1)}
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="asset-grid">
            {tab === 'media' && pending > 0 && Array.from({ length: pending })
              .map((_, k) => (
                <div key={`p${k}`} className="asset-card pending">
                  <div className="thumb"><span className="shimmer" /></div>
                  <div className="asset-desc">Generating…</div>
                </div>
              ))}
            {visible.length === 0 && !pending && (
              <div className="ap-empty">
                No {tab === 'audio' ? 'audio' : 'assets'} here. Upload
                {tab === 'audio' ? '' : ' or generate'} one.
              </div>
            )}
            {visible.map((a) => (
              <div key={a.id} className="card-wrap">
                <AssetCard a={a} genAvailable={genAvailable} clip={clip}
                  folders={folders} onI2V={(id) => setI2v({ id })}
                  onAdd={onAddToTimeline} onRef={useAsReference} onMove={onMove}
                  onInsert={onInsertClip} />
                {i2v?.id === a.id && (
                  <div className="i2v-pop">
                    <input className="ap-input" value={i2vPrompt}
                      placeholder="motion: slow push in, parallax"
                      onChange={(e) => setI2vPrompt(e.target.value)} />
                    <div className="i2v-actions">
                      <button className="btn ghost" onClick={() => setI2v(null)}>
                        Cancel</button>
                      <button className="btn primary" disabled={!!busy}
                        onClick={() => onImageToVideo(a.id)}>Animate</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
          {tab === 'media' && (
            <p className="ap-hint">Drag any card onto the timeline · ◎ Ref to
              generate from a photo · ⋯ to file it.</p>
          )}
        </div>
      )}

      {tab === 'generate' && (
        <div className="ap-body">
          {!genAvailable && (
            <div className="ap-warn">
              Generation off — set <code>GENMEDIA_API_KEY</code> in .env.
            </div>
          )}
          <div className="seg">
            <button className={kind === 'image' ? 'on' : ''}
              onClick={() => setKind('image')}>Image</button>
            <button className={kind === 'video' ? 'on' : ''}
              onClick={() => { setKind('video'); setReference(null) }}>Video</button>
          </div>

          {reference && (
            <div className="ref-chip">
              {reference.thumb && <img src={reference.thumb} alt="" />}
              <span>Reference: {reference.desc || reference.id}</span>
              <button onClick={() => setReference(null)}>✕</button>
            </div>
          )}

          <label className="ap-label">Prompt</label>
          <textarea className="ap-input" rows={3} value={prompt}
            placeholder={reference
              ? 'extreme close-up, horror intensity, cinematic lighting'
              : kind === 'video'
                ? 'cinematic neon rain on a city window, slow push in'
                : 'a glowing 3D logo on black, studio lighting'}
            onChange={(e) => setPrompt(e.target.value)} />

          <div className="composer-row">
            <select className="ap-input model-chip" value={model}
              onChange={(e) => setModel(e.target.value)}>
              {modelList.map((m) => (
                <option key={m.id} value={m.id}>{m.label}</option>
              ))}
            </select>
            <input className="ap-input seed-in" value={seed} inputMode="numeric"
              placeholder="seed" onChange={(e) => setSeed(e.target.value)}
              disabled={count > 1} />
          </div>

          {kind === 'image' && (
            <div className="composer-row">
              <label className="vary-lbl">Variations
                <input className="ap-input count-in" type="number" min="1" max="8"
                  value={count}
                  onChange={(e) => setCount(e.target.value)} />
              </label>
              {count > 1 && (
                <input className="ap-input" value={hints}
                  placeholder="hints: shock, fear, tense, horror"
                  onChange={(e) => setHints(e.target.value)} />
              )}
            </div>
          )}

          {reference && (
            <div className="composer-row">
              <input className="ap-input" value={negative}
                placeholder="negative: blurry, text, watermark"
                onChange={(e) => setNegative(e.target.value)} />
              {count <= 1 && (
                <input className="ap-input seed-in" value={strength}
                  inputMode="decimal" placeholder="0–1"
                  onChange={(e) => setStrength(e.target.value)}
                  title="strength: how far from the reference" />
              )}
            </div>
          )}

          <button className="btn primary block" disabled={!genAvailable || !!busy}
            onClick={onGenerate}>
            {count > 1 ? `Generate ${count} variations` : `Generate ${kind}`}
          </button>
          <p className="ap-hint">
            {reference
              ? 'Generating from your reference photo (image-to-image).'
              : 'Tip: hit ◎ Ref on any image in Media to generate from it.'}
          </p>
        </div>
      )}

      {tab === 'styles' && (
        <div className="ap-body">
          {!clip && <div className="ap-warn">Select a clip to apply a style.</div>}
          <div className="style-grid">
            {styles.length === 0 && (
              <div className="ap-empty">No styles found.</div>
            )}
            {styles.map((s) => (
              <button key={s.name} className="style-card" disabled={!clip || !!busy}
                onClick={() => onApplyStyle(s.name)} title={`Apply ${s.name}`}>
                <span className="style-aa">Aa</span>
                <span className="style-name">{s.label || s.name}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </aside>
  )
}
