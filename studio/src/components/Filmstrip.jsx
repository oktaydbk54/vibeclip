import { useEffect, useState } from 'react'
import { filmstripUrl } from '../api.js'

// The main video track's "footage" — a single horizontal sprite of N frame
// thumbnails from /api/v2/filmstrip, stretched across the lane so the timeline
// reads like real footage instead of a flat bar. Preloads the sprite and only
// paints it once decoded; if the clip isn't rendered yet (404) it stays a flat
// dark strip rather than showing a broken image.
export default function Filmstrip({ project, clip, version = '', n = 80, h = 54 }) {
  const url = (project && clip != null)
    ? filmstripUrl(project, clip, { n, h, v: version }) : ''
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setReady(false)
    if (!url) return
    const img = new Image()
    img.onload = () => setReady(true)
    img.onerror = () => setReady(false)
    img.src = url
    return () => { img.onload = null; img.onerror = null }
  }, [url])

  return (
    <div
      className={`filmstrip${ready ? ' ready' : ''}`}
      style={ready ? { backgroundImage: `url("${url}")` } : undefined}
    />
  )
}
