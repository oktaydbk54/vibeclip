import { forwardRef } from 'react'
import { mediaUrl } from '../api.js'

// The preview surface: a phone-framed <video> streaming the clip's rendered
// proxy from /api/v2/media. The parent owns the ref so the timeline can scrub
// and read currentTime. Real-time client-side compositing is a later phase;
// here we play exactly what the render engine produced.
const Preview = forwardRef(function Preview({ project, clip, timeline, rendered }, ref) {
  const hasMedia = clip && rendered
  return (
    <section className="preview">
      <div className="phone">
        {hasMedia ? (
          <video
            ref={ref}
            className="video"
            src={mediaUrl(project, clip)}
            controls
            playsInline
          />
        ) : (
          <div className="video placeholder">
            {clip ? 'Clip not rendered yet' : 'Select a clip'}
          </div>
        )}
      </div>
      {timeline && (
        <div className="meta">
          <span>{timeline.duration?.toFixed(2)}s</span>
          <span>·</span>
          <span>{timeline.fps} fps</span>
          {timeline.speed && timeline.speed !== 1 && (
            <><span>·</span><span>{timeline.speed}× speed</span></>
          )}
          {timeline.locked && <><span>·</span><span className="lock">🔒 locked</span></>}
        </div>
      )}
    </section>
  )
})

export default Preview
