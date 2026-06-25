import { useCallback, useEffect, useRef, useState } from 'react'
import { chatTurn, listAssets, subscribeEvents } from '../api.js'
import { friendlyTool, parseJobMsg } from '../friendlyTool.js'

// Palmier's signature: an agentic chat docked INSIDE the editor. Natural language
// → real edits via the same run_turn agent the legacy UI drives (project-keyed
// here). Tool calls stream in live as friendly chips over the shared SSE hub;
// when the turn finishes, the assistant reply + fresh {state,timeline} arrive on
// the job_done event and reconcile the whole editor in one shot.
//
// Two demos from the launch video both work through this one panel:
//   "organize my media"  → the agent files assets into folders (Phase E)
//   "now edit it for me"  → autonomous multi-step edit (existing tools)
//   "generate four close-ups of @Marcos-Eyes" → reference gen (Phase B)

function ToolChip({ name, running }) {
  return (
    <span className={running ? 'tool-chip running' : 'tool-chip'}>
      <span className="tc-dot" />
      <span className="tc-name">{friendlyTool(name)}</span>
    </span>
  )
}

export default function ChatPanel({ project, onMutated }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [tier, setTier] = useState('fast')          // fast | pro
  const [activeJob, setActiveJob] = useState(null)
  const [assets, setAssets] = useState([])
  const [mention, setMention] = useState(null)      // { q, items } | null
  const activeJobRef = useRef(null)
  const scrollRef = useRef(null)

  // Load images once for @-mention reference resolution (the eye-photo flow).
  useEffect(() => {
    listAssets().then((r) => setAssets((r.assets || [])
      .filter((a) => a.kind === 'image'))).catch(() => {})
  }, [])

  // Detect a trailing "@query" the user is typing and offer matching images.
  function refreshMention(text) {
    const m = /(^|\s)@([\w-]*)$/.exec(text)
    if (!m) { setMention(null); return }
    const q = m[2].toLowerCase()
    const items = assets.filter((a) =>
      (a.description || a.name || '').toLowerCase().includes(q)
      || a.id.toLowerCase().includes(q)).slice(0, 6)
    setMention(items.length ? { q, items } : null)
  }

  // Replace the trailing @query with a literal "@desc (#id)" so the agent can
  // resolve it to generate_asset(reference=<id>).
  function pickMention(a) {
    const label = (a.description || a.name || a.id).split(/\s+/).slice(0, 3).join('-')
    setInput((t) => t.replace(/(^|\s)@([\w-]*)$/, `$1@${label}(${a.id}) `))
    setMention(null)
  }

  // Update the last still-pending assistant bubble (the one the live job feeds).
  const updateLastPending = useCallback((updater) => {
    setMessages((m) => {
      let idx = -1
      for (let i = m.length - 1; i >= 0; i -= 1) {
        if (m[i].role === 'assistant' && m[i].status === 'pending') { idx = i; break }
      }
      if (idx < 0) return m
      const next = m.slice()
      next[idx] = updater(next[idx])
      return next
    })
  }, [])

  // One subscription to the shared SSE hub; react only to OUR active job.
  useEffect(() => {
    const unsub = subscribeEvents((evt) => {
      const job = evt.job
      if (!job || job.id !== activeJobRef.current) return
      if (evt.type === 'job_progress') {
        const { key } = parseJobMsg(job.message)
        if (!key) return
        updateLastPending((x) => {
          const tools = x.tools || []
          if (tools.length && tools[tools.length - 1].name === key) return x
          return { ...x, tools: [...tools, { name: key }] }
        })
      } else if (evt.type === 'job_done') {
        const r = job.result || {}
        if (job.status === 'error') {
          updateLastPending((x) => ({
            ...x, text: job.error || 'Something went wrong.', status: 'error',
          }))
        } else {
          updateLastPending((x) => ({
            ...x, text: r.reply || '', status: 'done',
            clarify: r.clarify || null, pendingPlan: r.pending_plan || null,
          }))
          if (r.state || r.timeline) onMutated?.({ state: r.state, timeline: r.timeline })
        }
        activeJobRef.current = null
        setActiveJob(null)
      }
    })
    return unsub
  }, [onMutated, updateLastPending])

  // Keep the transcript scrolled to the latest bubble.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  const send = useCallback(async (textArg) => {
    const msg = (textArg ?? input).trim()
    if (!msg || activeJobRef.current || !project) return
    setInput('')
    setMessages((m) => [...m,
      { role: 'user', text: msg },
      { role: 'assistant', text: '', tools: [], status: 'pending' }])
    try {
      const { job_id: jobId } = await chatTurn(project, { message: msg, tier })
      activeJobRef.current = jobId
      setActiveJob(jobId)
    } catch (e) {
      updateLastPending((x) => ({
        ...x, text: String(e.message || e), status: 'error',
      }))
    }
  }, [input, project, tier, updateLastPending])

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  return (
    <section className="chat-panel">
      <div className="cp-head">
        <span className="cp-title">Assistant</span>
        <div className="cp-tier seg sm">
          {['fast', 'pro'].map((t) => (
            <button key={t} className={tier === t ? 'on' : ''}
              onClick={() => setTier(t)} title={t === 'fast'
                ? 'Fast model — quick edits' : 'Pro model — sharper planning'}>
              {t === 'fast' ? 'Fast' : 'Pro'}
            </button>
          ))}
        </div>
      </div>

      <div className="cp-scroll" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="cp-empty">
            <p>Edit by chatting. Try:</p>
            <ul>
              <li onClick={() => send('remove the filler words')}>
                “remove the filler words”</li>
              <li onClick={() => send('add a punch-in zoom at the hook')}>
                “add a punch-in zoom at the hook”</li>
              <li onClick={() => send('organize my media into folders')}>
                “organize my media into folders”</li>
            </ul>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`cp-msg ${m.role} ${m.status || ''}`}>
            {m.role === 'assistant' && m.tools?.length > 0 && (
              <div className="cp-tools">
                {m.tools.map((t, k) => (
                  <ToolChip key={k} name={t.name}
                    running={m.status === 'pending' && k === m.tools.length - 1} />
                ))}
              </div>
            )}
            {m.status === 'pending' && !m.text ? (
              <div className="cp-thinking"><span className="spin" />thinking…</div>
            ) : (
              m.text && <div className="cp-bubble">{m.text}</div>
            )}
            {m.clarify && (
              <div className="cp-clarify">
                {m.clarify.options?.length > 0 && (
                  <div className="cp-opts">
                    {m.clarify.options.map((o, k) => (
                      <button key={k} className="cp-opt" onClick={() => send(o)}>
                        {o}</button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {m.pendingPlan && (
              <div className="cp-plan">
                <span>Draft ready.</span>
                <button className="btn primary sm" onClick={() => send('apply')}>
                  Apply</button>
                <button className="btn ghost sm" onClick={() => send('discard')}>
                  Discard</button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="cp-input">
        {mention && (
          <div className="cp-mentions">
            {mention.items.map((a) => (
              <button key={a.id} className="cp-mention" onClick={() => pickMention(a)}>
                {a.thumb && <img src={a.thumb} alt="" />}
                <span>{a.description || a.name || a.id}</span>
              </button>
            ))}
          </div>
        )}
        <textarea
          rows={2}
          value={input}
          disabled={!project}
          placeholder={activeJob ? 'Working…' : 'Ask the editor… (@ to reference media)'}
          onChange={(e) => { setInput(e.target.value); refreshMention(e.target.value) }}
          onKeyDown={onKeyDown}
        />
        <button className="btn primary" disabled={!input.trim() || !!activeJob}
          onClick={() => send()}>
          {activeJob ? '…' : 'Send'}
        </button>
      </div>
    </section>
  )
}
