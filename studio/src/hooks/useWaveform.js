import { useEffect, useState } from 'react'

// Decode an audio/video URL's first channel into ~1200 amplitude peaks via
// WebAudio, once per URL (module-level cache + in-flight dedup so the video
// track and any audio lanes that share the same media decode only once). No
// backend: the optional /api/v2/peaks endpoint stays deferred (spec §6.3).
const _cache = new Map()    // url -> Float32Array | 'pending' | 'error'
const _waiters = new Map()  // url -> Array<(peaks|null) => void>

async function decode(url) {
  const Ctx = window.AudioContext || window.webkitAudioContext
  if (!Ctx) throw new Error('no WebAudio')
  const ctx = new Ctx()
  try {
    const res = await fetch(url, { credentials: 'include' })
    if (!res.ok) throw new Error(`audio ${res.status}`)
    const audio = await ctx.decodeAudioData(await res.arrayBuffer())
    const ch = audio.getChannelData(0)
    const N = 1200
    const block = Math.max(1, Math.floor(ch.length / N))
    const peaks = new Float32Array(N)
    for (let i = 0; i < N; i++) {
      let max = 0
      const s = i * block
      for (let j = 0; j < block; j++) {
        const v = Math.abs(ch[s + j] || 0)
        if (v > max) max = v
      }
      peaks[i] = max
    }
    return peaks
  } finally {
    ctx.close?.()
  }
}

export function useWaveform(url) {
  const [peaks, setPeaks] = useState(() => {
    const c = url && _cache.get(url)
    return c instanceof Float32Array ? c : null
  })

  useEffect(() => {
    if (!url) { setPeaks(null); return }
    const cached = _cache.get(url)
    if (cached instanceof Float32Array) { setPeaks(cached); return }
    setPeaks(null)
    let alive = true
    const waiters = _waiters.get(url) || []
    waiters.push((p) => { if (alive) setPeaks(p) })
    _waiters.set(url, waiters)
    if (cached !== 'pending') {
      _cache.set(url, 'pending')
      decode(url)
        .then((p) => { _cache.set(url, p); flush(url, p) })
        .catch(() => { _cache.set(url, 'error'); flush(url, null) })
    }
    return () => { alive = false }
  }, [url])

  return peaks
}

function flush(url, value) {
  const waiters = _waiters.get(url) || []
  _waiters.delete(url)
  waiters.forEach((fn) => fn(value))
}
