import { useEffect, useState } from 'react'
import { api } from '../api'

const SAMPLES = {
  'Quote request': { what: 'enquiry', text: "Hi,\n\nWe're refurbishing two lifts at our Toronto site. Could you send us a quote for the door operators on the attached spec? We'd like to decide by the end of the month.\n\nThanks,\nJohn" },
  'Invoice': { what: 'invoice', text: "Dear customer,\n\nPlease find attached invoice INV-2291 for $2,450.00 for goods delivered in September. Payment is due within 30 days.\n\nAccounts Receivable" },
  'Support problem': { what: 'support', text: "Hello, since yesterday's update the PDF export on our account fails with an error. This is blocking our month-end close. Please help." },
  'Newsletter': { what: 'enquiry', text: 'This week in product: three new features, a customer story and our webinar schedule. You are receiving this because you subscribed. Unsubscribe any time.' },
}

export default function Demo() {
  const [presets, setPresets] = useState([])
  const [what, setWhat] = useState('enquiry')
  const [text, setText] = useState(SAMPLES['Quote request'].text)
  const [res, setRes] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { api.presets().then(setPresets).catch(() => {}) }, [])

  const run = async (t = text, w = what) => {
    setBusy(true); setErr('')
    try { setRes(await api.demo(t, w)) } catch (e) { setErr(e.message); setRes(null) } finally { setBusy(false) }
  }
  const pick = (name) => { const s = SAMPLES[name]; setText(s.text); setWhat(s.what); run(s.text, s.what) }

  return (
    <div className="demo invert">
      <div>
        <span className="eyebrow">Try it</span>
        <h2 style={{ margin: '1.2rem 0 1rem' }}>Paste an email. See what it catches.</h2>
        <p className="lead">This box calls the same API your code will. Pick what to catch, paste an email, and WatchFox answers in under a second.</p>
        <p className="small muted">Demo emails are not stored.</p>
      </div>
      <div>
        <div className="chips">{Object.keys(SAMPLES).map((k) => <button key={k} className="chip" onClick={() => pick(k)}>{k}</button>)}</div>
        <textarea value={text} maxLength={1500} onChange={(e) => setText(e.target.value)} aria-label="Email text" />
        <div className="chips" role="group" aria-label="What to catch">
          {presets.map((p) => <button key={p.id} className="chip" aria-pressed={what === p.id} onClick={() => setWhat(p.id)}>{p.label}</button>)}
        </div>
        <button className="btn" disabled={busy || !text.trim()} onClick={() => run()} style={{ background: 'var(--fg)', color: 'var(--bg)' }}>{busy ? 'Reading…' : 'Check this email'}</button>
        {err && <div className="err">{err}</div>}
        {res && (
          <div className={`verdict ${res.matched ? 'yes' : ''}`} style={res.matched ? { background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' } : {}}>
            <div className="big">{res.matched ? `Caught: ${res.label}` : res.excluded ? 'Skipped: looks automated' : 'Not caught'}</div>
            <div className="small">Score {res.score.toFixed(2)} · catches from {res.threshold.toFixed(2)}</div>
            <div className="meter" aria-hidden="true"><i style={{ width: `${Math.min(100, res.score * 100)}%` }} /><u style={{ left: `${res.threshold * 100}%` }} /></div>
          </div>
        )}
      </div>
    </div>
  )
}
