// Friendly, human-readable labels for the agent's tool calls — ported from the
// legacy chat UI (chat/static/app.js STAGE_TR / STAGE_FRIENDLY) so studio2's
// chat chips read the same. A job_progress message is "tool|args" or a bare
// stage name; parseJobMsg splits it, friendlyTool maps the key to a line.

export const STAGE_TR = {
  cut: 'cut', jumpcut: 'silence', trim: 'trim', reframe: 'reframe',
  broll: 'b-roll', lut: 'color', zoom: 'zoom', subtitles: 'captions',
  overlay: 'overlay', brand: 'brand', fx: 'fx', music: 'music',
  ambience: 'ambience', sfx: 'sfx', fade: 'master',
}

export const STAGE_FRIENDLY = {
  cut: 'trimming to the moment',
  jumpcut: 'removing dead air',
  trim: 'trimming the edges',
  reframe: 'reframing to vertical',
  broll: 'weaving in b-roll',
  lut: 'grading the color',
  zoom: 'punching in for emphasis',
  subtitles: 'rendering word-synced captions',
  overlay: 'placing overlays',
  brand: 'stamping the brand',
  fx: 'adding effects',
  music: 'scoring the music bed',
  ambience: 'laying ambience',
  sfx: 'dropping sound effects',
  fade: 'polishing loudness',
  // tool-level lines (chat agent dispatch)
  propose_edit: 'drafting an edit & rendering a preview',
  apply_plan: 'applying the edit',
  discard_plan: 'discarding the draft',
  generate_clips: 'scanning for the best moments',
  remove_fillers: 'cleaning up the umms',
  remove_section: 'cutting that section',
  restore_section: 'restoring that section',
  set_style: 'applying the style',
  apply_style: 'applying the style',
  set_music: 'scoring the music bed',
  add_sound_effect: 'dropping a sound effect',
  set_subtitles: 'styling the captions',
  set_denoise: 'cleaning background noise',
  add_zoom: 'punching in for emphasis',
  edit_event: 'adjusting the edit',
  delete_event: 'removing that element',
  nudge_edit: 'nudging the timing',
  add_broll: 'weaving in b-roll',
  rerun_broll: 'regenerating the b-roll',
  generate_asset: 'generating media',
  generate_variations: 'generating variations',
  generate_video_from_asset: 'animating the image',
  organize_assets: 'sorting your media',
  move_asset_to_folder: 'filing it away',
  set_cut: 'setting the cut',
  set_speed: 'changing the speed',
  export_clip: 'exporting the clip',
  render_clip: 'rendering the clip',
  assemble_reel: 'assembling the reel',
}

// Split "tool|args" (tool narration) or a bare stage name → { key, args }.
export function parseJobMsg(msg) {
  if (!msg) return { key: '', args: '' }
  const i = msg.indexOf('|')
  return i < 0 ? { key: msg, args: '' }
    : { key: msg.slice(0, i), args: msg.slice(i + 1) }
}

// A short friendly line for a tool/stage key (falls back gracefully).
export function friendlyTool(key) {
  return STAGE_FRIENDLY[key] || STAGE_TR[key] || key || 'working on it'
}
