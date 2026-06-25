import { useEffect, useRef, useState } from 'react'
import {
  addBrollFromAsset, generateAsset, getModels, imageToVideo, listAssets,
  uploadAsset,
} from '../api.js'

// Right rail: the asset library + the AI generation surface — the two things a
// generative timeline editor needs that a plain NLE doesn't. Everything routes
// through existing backend (/api/assets, /api/v2/tool → generate_asset /
// generate_video_from_asset / add_broll).
export default function AssetPanel({
  project, clip, videoRef, onMutated, onRendering,
}) {
  const [tab, setTab] = useState('generate')
  const [assets, setAssets] = useState([])
  const [models, setModels] = useState(null)
  const [busy, setBusy] = useState('')        // status text while generating
  const fileRef = useRef(null)

  // Generate form
  const [kind, setKind] = useState('image')
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState('')
  const [seed, setSeed] = useState('')

  // image-to-video target (an asset id) + its motion prompt
  const [i2v, setI2v] = useState(null)         // { id } | null
  const [i2vPrompt, setI2vPrompt] = useState('')

  async function refreshAssets() {
    try { setAssets((await listAssets()).assets || []) } catch { /* ignore */ }
  }

  useEffect(() => {
    getModels().then(setModels).catch(() => setModels({ available: false }))
    refreshAssets()
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

  async function onGenerate() {
    if (!prompt.trim() || !project || busy) return
    setBusy(`Generating ${kind}…`)
    try {
      const res = await generateAsset(
        project, { prompt: prompt.trim(), kind, model, seed: seed ? +seed : -1 },
        clip)
      const r = res?.result
      if (r?.ok === false) { alert(r.error || 'Generation failed') }
      else { setPrompt(''); await refreshAssets(); setTab('assets') }
    } catch (e) { alert(String(e.message || e)) }
    finally { setBusy('') }
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
    try {
      const res = await imageToVideo(
        project, { asset_id: assetId, prompt: i2vPrompt.trim(), model: modelId },
        clip)
      const r = res?.result
      if (r?.ok === false) alert(r.error || 'Image-to-video failed')
      else { setI2v(null); setI2vPrompt(''); await refreshAssets() }
    } catch (e) { alert(String(e.message || e)) }
    finally { setBusy('') }
  }

  async function onAddToTimeline(asset) {
    if (!clip || busy) return
    const t = videoRef?.current?.currentTime || 0
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

  return (
    <aside className="asset-panel">
      <div className="ap-tabs">
        <button className={tab === 'generate' ? 'on' : ''}
          onClick={() => setTab('generate')}>Generate</button>
        <button className={tab === 'assets' ? 'on' : ''}
          onClick={() => setTab('assets')}>Assets · {assets.length}</button>
      </div>

      {busy && <div className="ap-busy"><span className="spin" />{busy}</div>}

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
              onClick={() => setKind('video')}>Video</button>
          </div>
          <label className="ap-label">Prompt</label>
          <textarea className="ap-input" rows={3} value={prompt}
            placeholder={kind === 'video'
              ? 'cinematic neon rain on a city window, slow push in'
              : 'a glowing 3D logo on black, studio lighting'}
            onChange={(e) => setPrompt(e.target.value)} />
          <label className="ap-label">Model</label>
          <select className="ap-input" value={model}
            onChange={(e) => setModel(e.target.value)}>
            {modelList.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
          <label className="ap-label">Seed (optional)</label>
          <input className="ap-input" value={seed} inputMode="numeric"
            placeholder="random" onChange={(e) => setSeed(e.target.value)} />
          <button className="btn primary block" disabled={!genAvailable || !!busy}
            onClick={onGenerate}>Generate {kind}</button>
          <p className="ap-hint">
            Tip: generate an <b>image</b>, then hit <b>→ Video</b> on it in Assets
            to animate it (image-to-video).
          </p>
        </div>
      )}

      {tab === 'assets' && (
        <div className="ap-body">
          <button className="btn block" onClick={() => fileRef.current?.click()}>
            ⬆ Upload image / video / audio
          </button>
          <input ref={fileRef} type="file" hidden
            accept="image/*,video/*,audio/*" onChange={onUpload} />
          <div className="asset-grid">
            {assets.length === 0 && (
              <div className="ap-empty">No assets yet. Upload or generate one.</div>
            )}
            {assets.map((a) => (
              <div className="asset-card" key={a.id}>
                <div className="thumb">
                  {a.thumb ? <img src={a.thumb} alt={a.description} />
                    : <span className="kind">{a.kind}</span>}
                  <span className="kind-badge">{a.kind}</span>
                </div>
                <div className="asset-desc" title={a.description}>
                  {a.description || a.name || `asset ${a.id}`}
                </div>
                <div className="asset-actions">
                  {a.kind === 'image' && genAvailable && (
                    <button onClick={() => setI2v({ id: a.id })}>→ Video</button>
                  )}
                  {(a.kind === 'image' || a.kind === 'video') && clip && (
                    <button onClick={() => onAddToTimeline(a)}>+ Timeline</button>
                  )}
                </div>
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
        </div>
      )}
    </aside>
  )
}
