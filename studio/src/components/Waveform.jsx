import { useEffect, useRef } from 'react'
import { useWaveform } from '../hooks/useWaveform.js'

// Canvas waveform drawn from decoded peaks, mirrored around the centerline.
// Sized in CSS pixels from props (so it tracks the timeline zoom width) and
// redrawn whenever the peaks, width, or height change.
export default function Waveform({
  url, width, height = 26, color = 'rgba(255,255,255,0.5)',
}) {
  const peaks = useWaveform(url)
  const ref = useRef(null)

  useEffect(() => {
    const c = ref.current
    if (!c || !width) return
    const dpr = window.devicePixelRatio || 1
    c.width = Math.floor(width * dpr)
    c.height = Math.floor(height * dpr)
    c.style.width = `${width}px`
    c.style.height = `${height}px`
    const ctx = c.getContext('2d')
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, width, height)
    if (!peaks || !peaks.length) return
    ctx.fillStyle = color
    const mid = height / 2
    for (let x = 0; x < width; x++) {
      const p = peaks[Math.floor((x / width) * peaks.length)] || 0
      const bar = Math.max(0.5, p * mid * 0.92)
      ctx.fillRect(x, mid - bar, 1, bar * 2)
    }
  }, [peaks, width, height, color])

  return <canvas ref={ref} className="waveform" />
}
