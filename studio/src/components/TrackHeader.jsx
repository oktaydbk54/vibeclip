// One track-header row in the timeline's left gutter: color swatch · kind icon ·
// label · visibility toggle. Heights mirror the lanes exactly (CSS --th-video /
// --th-row) so headers and lanes stay aligned. Visibility is functional (hides
// the lane's items client-side); mute/lock are intentionally omitted until the
// stage-recipe backend grows tools for them (see UI redesign spec, §4.5).
const ICONS = {
  zoom: '⤢', splitscreen: '▦', broll: '🎬', overlay: '▣', brand: '◈',
  subtitles: 'T', fx: '✦', sfx: '♪', music: '♪', ambience: '∿', fade: '▭',
}

export default function TrackHeader({
  trackKey, label, color, main = false, visible = true, onToggleVisible,
}) {
  return (
    <div className={`trk-head${main ? ' main' : ''}`}>
      <span className="trk-swatch" style={{ background: color }} />
      <span className="trk-icon">{main ? '▣' : (ICONS[trackKey] || '•')}</span>
      <span className="trk-label">{label}</span>
      {!main && (
        <button
          className={`trk-eye${visible ? '' : ' off'}`}
          title={visible ? 'Hide lane' : 'Show lane'}
          onClick={onToggleVisible}
        >
          {visible ? '◉' : '○'}
        </button>
      )}
    </div>
  )
}
