import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { api, BASE } from '../api'
import Code from '../components/Code'
import Settings, { Meter } from './Settings'
import { useAsk } from '../ask'

const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
const PROVIDER = { gmail: 'Gmail', m365: 'Microsoft 365', imap: 'IMAP', sandbox: 'Sandbox' }

function Reveal({ title, value, hint, onClose }) {
  const [done, setDone] = useState(false)
  return (
    <div className="panel dark">
      <b>{title}</b>
      <pre className="plain" style={{ borderColor: 'var(--g3)', margin: '.8rem 0', color: 'var(--bg)', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{value}</pre>
      <p className="small" style={{ color: 'var(--g3)' }}>{hint}</p>
      <div className="row">
        <button className="btn sm" style={{ background: 'var(--bg)', color: 'var(--fg)', borderColor: 'var(--bg)' }}
          onClick={async () => { try { await navigator.clipboard.writeText(value); setDone(true) } catch {} }}>{done ? 'Copied' : 'Copy'}</button>
        <button className="btn sm ghost" style={{ color: 'var(--bg)', borderColor: 'var(--bg)' }} onClick={onClose}>I have saved it</button>
      </div>
    </div>
  )
}

function NextStep({ title, text, button, onClick }) {
  return (
    <div className="next" role="status">
      <div><b>Next step: {title}</b><div className="small">{text}</div></div>
      <button className="btn sm" onClick={onClick}>{button} →</button>
    </div>
  )
}

const fmtTime = (ts) => new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
const fmtShort = (ts) => new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const LEVEL = { high: 'High', medium: 'Medium', low: 'Borderline' }
const CONF_HELP = 'Confidence shows how clearly an email fits this type, compared with the line for a match. 85% and over is high, 65 to 84% is medium, below 65% is borderline. It is a guide, not a guarantee.'

// A caught type with its confidence, e.g. "Enquiries 82%"
function Conf({ r }) {
  const pct = r.confidence ?? Math.round(Math.min(99, 100 * Math.min(1, r.score / (r.threshold + 0.3))))
  const level = r.confidence_level || (pct >= 85 ? 'high' : pct >= 65 ? 'medium' : 'low')
  return <span className="pill on" title={`${LEVEL[level]} confidence, ${pct}%. ${CONF_HELP}`}>{r.label} {pct}% · {LEVEL[level]}</span>
}

// "Use this from your code": the same data through the API
function ApiHint({ path, summary }) {
  return (
    <details className="apihint">
      <summary>{summary || 'Get this from your code'}</summary>
      <Code tabs={{
        curl: `curl "${BASE}${path}" \\\n  -H "Authorization: Bearer YOUR_API_KEY"`,
        python: `import requests\nr = requests.get("${BASE}${path}",\n                 headers={"Authorization": "Bearer YOUR_API_KEY"}).json()`,
        node: `const r = await fetch("${BASE}${path}", {\n  headers: { Authorization: "Bearer YOUR_API_KEY" },\n}).then((x) => x.json());`,
      }} />
    </details>
  )
}

/* ------------------------------------------------------------------ start */
function Start({ d, go }) {
  const steps = [
    ['Create an API key', d.keys.length > 0, 'keys'],
    ['Connect a source (a mailbox), or add a sandbox', d.connections.length > 0, 'sources'],
    ['Create a watch: pick the source and what to catch', d.watches.length > 0, 'watches'],
    ['Receive your first catch', d.matches.length > 0, 'caught'],
  ]
  return (
    <>
      <h1>Get started</h1>
      <p className="muted">Four steps from nothing to your first webhook. Every email a watch checks, caught or not, is one run.</p>
      <div className="todo" style={{ marginTop: '1.5rem' }}>
        {steps.map(([t, done, to], i) => (
          <div key={t} className={`it ${done ? 'done' : ''}`}>
            <span className="box">{done ? '✓' : i + 1}</span><span style={{ flex: 1 }}>{t}</span>
            {!done && <button className="btn sm ghost" onClick={() => go(to)}>Go</button>}
          </div>
        ))}
      </div>
      <h2>Check your key works</h2>
      <Code label="terminal">{`curl ${BASE}/v1/connections \\
  -H "Authorization: Bearer YOUR_API_KEY"`}</Code>
      <p className="muted small">Full reference in the <a href="#/docs">docs</a>. Use a sandbox to try everything without real mail. Questions about runs, billing or privacy? See the <a href="#/docs/faq">FAQ</a>.</p>
    </>
  )
}

/* ------------------------------------------------------------------ keys */
function Keys({ d, reload, go }) {
  const [ask, sheet] = useAsk()
  const [name, setName] = useState('')
  const [fresh, setFresh] = useState(null)
  const [err, setErr] = useState('')
  const make = async () => { setErr(''); try { setFresh(await api.newKey(name || 'Default')); setName(''); reload() } catch (e) { setErr(e.message) } }
  return (
    <>
      <h1>API keys</h1>
      <p className="muted">Send a key as <code>Authorization: Bearer …</code> on every API request. Keep keys on your server.</p>
      {fresh && <Reveal title="Your new API key" value={fresh.key} hint="This is the only time the full key is shown. Store it somewhere safe." onClose={() => setFresh(null)} />}
      <div className="list">
        {d.keys.length === 0 && <div className="li"><span className="s">No keys yet.</span></div>}
        {d.keys.map((k) => (
          <div className="li" key={k.id}>
            <div><div className="t">{k.name}</div><div className="s mono">{k.prefix}…  ·  created {ago(k.created)}{k.last_used ? `  ·  used ${ago(k.last_used)}` : '  ·  never used'}</div></div>
            <button className="btn sm ghost" onClick={async () => { if (await ask({ title: `Revoke "${k.name}"?`, body: 'Anything using it stops working.', yes: 'Revoke', no: 'Keep it', danger: true })) { await api.revokeKey(k.id); reload() } }}>Revoke</button>
          </div>
        ))}
      </div>
      {d.keys.length > 0 && d.connections.length === 0 && (
        <NextStep title="connect a source" text="Your key is ready. Next, connect the mailbox WatchFox should watch, or add a sandbox to try it with test mail." button="Connect a source" onClick={() => go('sources')} />
      )}
      <div className="row"><input style={{ maxWidth: '16rem' }} placeholder="Key name, e.g. production" value={name} onChange={(e) => setName(e.target.value)} /><button className="btn" onClick={make}>Create key</button></div>
      {err && <div className="err">{err}</div>}
      {sheet}
    </>
  )
}

/* ------------------------------------------------------------------ sources */
// `expect` says what the sample is meant to show, so a tester knows what a correct result looks like
const CATCH_SAMPLES = {
  'Quote request': { subject: 'Pricing for 40 licences', body: 'Hi, could you send us a quote for 40 licences with implementation? We need it by Friday.', sender: 'priya@acme.example', expect: 'caught as Enquiries' },
  'RFQ with specs': { subject: 'RFQ-2026-205: Door operators', body: 'Dear Supplier, please find attached our Request for Quotation for 40 door operators. Kindly submit your best price, delivery time and payment terms by 24 October.', sender: 'procurement@gulfbuild.example', expect: 'caught as Enquiries' },
  'Invoice': { subject: 'Invoice INV-2291', body: 'Please find attached invoice INV-2291 for $2,450.00, due in 30 days.', sender: 'billing@supplier.example', expect: 'caught as Invoices' },
  'Support problem': { subject: 'Cannot export reports since the update', body: "Hi, since yesterday's update the PDF export fails with an error. This is blocking our month-end close. Please help.", sender: 'mark@customer-co.example', expect: 'caught as Support tickets' },
  'Purchase order': { subject: 'Purchase Order PO-5521', body: 'Please accept the attached order for 12 x door operator, model GD-200. Ship to our Mississauga warehouse and confirm the delivery date.', sender: 'buyer@buildright.example', expect: 'caught as Purchase orders' },
  'Job application': { subject: 'Application: Field technician', body: 'Dear Hiring Manager, I am applying for the field technician role. I have six years of experience in elevator maintenance. My resume is attached.', sender: 'amit.k@example.com', expect: 'caught as Job applications' },
  'Claim': { subject: 'Very disappointed with delivery', body: 'This is the third time my order has arrived late and damaged. I expect a refund and an explanation.', sender: 'angry@customer.example', expect: 'caught as Claims' },
}
const IGNORE_SAMPLES = {
  'Newsletter': { subject: 'This week in logistics', body: 'Top stories this week: port congestion eases and five tips for warehouse safety. Unsubscribe any time.', sender: 'news@weekly.example', expect: 'not caught as an enquiry (newsletter)' },
  'Supplier quote': { subject: 'Quotation Q-4471 for your request', body: 'Please find attached our quotation Q-4471 for 40 door operators at $212 each, valid for 30 days.', sender: 'sales@deltacomp.example', expect: 'not caught as an enquiry (a quote sent to you)' },
  'Cold sales pitch': { subject: 'Quick idea for your team', body: 'Hi, I help SaaS companies book more demos with outbound email. Worth a 15 minute call this week?', sender: 'jordan@growthbridge.example', expect: 'not caught (sales pitch)' },
  'Renewal notice': { subject: 'Your subscription renews on 1 November', body: 'Your plan renews on 1 November for $29. Manage your subscription any time in account settings. You are receiving this email because you have an account. Unsubscribe or manage your email preferences.', sender: 'billing@cloudtools.example', expect: 'not caught (an automatic notice with an unsubscribe footer)' },
  'Reply about a quote': { subject: 'Re: Quotation Q-2026-88', body: 'Thanks for the quote. We are going ahead with option B. Please send the order acknowledgement.', sender: 'buyer@elevatorpros.example', expect: 'not caught as an enquiry (a reply about a quote)' },
  'Out of office': { subject: 'Automatic reply: out of office', body: 'I am out of the office until 20 October with limited access to email. For urgent matters contact support@supplier.example.', sender: 'ravi@supplier.example', expect: 'not caught (automatic reply)' },
}
const SAMPLES = { ...CATCH_SAMPLES, ...IGNORE_SAMPLES }
const OWN = 'Write my own'

async function toBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

function Sandbox({ c, go, hasWatch }) {
  const [picked, setPicked] = useState('Quote request')
  const [f, setF] = useState(SAMPLES['Quote request'])
  const [files, setFiles] = useState([])
  const [err, setErr] = useState('')
  const [sent, setSent] = useState(0)          // how many test emails were sent from this form
  const subject = useRef(null)

  const pick = (name) => {
    setPicked(name); setErr('')
    if (name === OWN) { setF({ subject: '', body: '', sender: 'me@example.com' }); setTimeout(() => subject.current?.focus(), 50) }
    else setF(SAMPLES[name])
  }
  const edit = (patch) => { setF({ ...f, ...patch }); setPicked(OWN) }       // once you change a sample it is your own email
  const attach = async (e) => {
    setErr('')
    const chosen = [...e.target.files]; e.target.value = ''
    const next = [...files]
    for (const file of chosen) {
      if (next.length >= 3) { setErr('Up to 3 files per test email.'); break }
      if (file.size > 3 * 1024 * 1024) { setErr(`${file.name} is over 3 MB.`); continue }
      next.push({ filename: file.name, content_type: file.type || 'application/octet-stream', content_base64: await toBase64(file), size: file.size })
    }
    setFiles(next)
  }
  const send = async () => {
    setErr('')
    if (!f.body.trim() && !f.subject.trim()) { setErr('Write a subject or some text first.'); return }
    try {
      await api.sandboxEmail(c.id, { subject: f.subject, body: f.body || f.subject, sender: f.sender || 'someone@example.com',
        attachments: files.map(({ size, ...a }) => a) })
      setSent((n) => n + 1)
    } catch (x) { setErr(x.message) }
  }
  const Chip = ({ name }) => <button className="chip" aria-pressed={picked === name} onClick={() => pick(name)}>{name}</button>
  const expect = picked !== OWN && SAMPLES[picked]?.expect

  return (
    <div style={{ padding: '0 1.1rem 1.1rem' }}>
      <label>Send a test email into this mailbox</label>
      <p className="small muted" style={{ margin: '0 0 .3rem' }}>Pick a sample, or write your own. Each watch that checks it uses 1 run.</p>
      <div className="chips"><button className="chip own" aria-pressed={picked === OWN} onClick={() => pick(OWN)}>✎ {OWN}</button></div>
      <p className="small muted" style={{ margin: '.2rem 0 0' }}>Typical emails</p>
      <div className="chips">{Object.keys(CATCH_SAMPLES).map((k) => <Chip key={k} name={k} />)}</div>
      <p className="small muted" style={{ margin: '.2rem 0 0' }}>Look-alikes that should be ignored by an Enquiries watch</p>
      <div className="chips">{Object.keys(IGNORE_SAMPLES).map((k) => <Chip key={k} name={k} />)}</div>
      {expect && <p className="small" style={{ margin: '.2rem 0 .6rem' }}><b>This sample is meant to be:</b> {expect}. It depends on which watches you have.</p>}
      <input placeholder="From" value={f.sender} onChange={(e) => edit({ sender: e.target.value })} />
      <input ref={subject} style={{ marginTop: '.5rem' }} placeholder="Subject" value={f.subject} onChange={(e) => edit({ subject: e.target.value })} />
      <textarea style={{ marginTop: '.5rem' }} rows={4} placeholder="Write the email text here" value={f.body} onChange={(e) => edit({ body: e.target.value })} />
      <div className="row" style={{ marginTop: '.5rem' }}>
        <label className="btn sm ghost filebtn">+ Attach a file<input type="file" multiple hidden onChange={attach} /></label>
        {files.map((a) => <span key={a.filename} className="pill">{a.filename} · {Math.max(1, Math.round(a.size / 1024))} KB <button className="x" aria-label={`Remove ${a.filename}`} onClick={() => setFiles(files.filter((x) => x !== a))}>✕</button></span>)}
      </div>
      {err && <div className="err">{err}</div>}
      <button className="btn sm" style={{ marginTop: '.8rem' }} onClick={send}>Send test email</button>
      {sent > 0 && (hasWatch
        ? <NextStep title={sent > 1 ? 'check what was caught' : 'see what was caught'} text="Sent. A watch checks it within a few seconds. Matches appear under Caught. Every email checked, caught or not, is in the Activity log." button="See what was caught" onClick={() => go('caught')} />
        : <NextStep title="create a watch" text="Sent, but no watch is running on this mailbox yet, so nothing will be caught." button="Create a watch" onClick={() => go('watches')} />)}
      {sent === 0 && <p className="small muted" style={{ marginTop: '.6rem' }}>Matches show up under Caught. Create a watch for this mailbox first if you have not.</p>}
    </div>
  )
}

function Sources({ d, reload, go }) {
  const [ask, sheet] = useAsk()
  const [imap, setImap] = useState(false)
  const [f, setF] = useState({ host: '', user: '', password: '' })
  const [err, setErr] = useState('')
  // arriving with a watch but nothing caught yet: open the sandbox form so the next step is right there
  const [open, setOpen] = useState(() => (d.watches.length > 0 && d.matches.length === 0 ? d.connections.find((c) => c.provider === 'sandbox')?.id || null : null))
  const prov = Object.fromEntries((d.providers?.providers || []).map((p) => [p.id, p]))
  const back = `${window.location.origin}/#/dashboard/sources`

  // Gmail and Microsoft use the hosted connect page, the same one a SaaS sends its customers to.
  const link = async (body) => {
    setErr('')
    try { const r = await api.connectLink({ redirect_url: back, app_name: 'your dashboard', ...body }); window.location.href = r.url } catch (e) { setErr(e.message) }
  }
  const addImap = async () => { setErr(''); try { await api.addConnection({ provider: 'imap', ...f }); setImap(false); setF({ host: '', user: '', password: '' }); reload() } catch (e) { setErr(e.message) } }

  return (
    <>
      <h1>Sources</h1>
      <p className="muted">The places WatchFox watches. Email is live now; folders, FTP and databases are coming. Access is always read-only.</p>
      {d.connections.length > 0 && d.watches.length === 0 && (
        <NextStep title="create a watch" text="A source is connected, but nothing is watching it yet. A watch says what to catch." button="Create a watch" onClick={() => go('watches')} />
      )}
      <div className="list">
        {d.connections.length === 0 && <div className="li"><span className="s">No sources yet.</span></div>}
        {d.connections.map((c) => (
          <div key={c.id}>
            <div className={`li ${c.provider === 'sandbox' ? 'click' : ''}`} onClick={() => c.provider === 'sandbox' && setOpen(open === c.id ? null : c.id)}>
              <div style={{ minWidth: 0 }}>
                <div className="t">{c.email}</div>
                <div className="s mono">{c.id}  ·  {PROVIDER[c.provider]}{c.provider === 'm365' ? (c.delivery === 'push' ? '  ·  push' : '  ·  checked every minute') : ''}</div>
                {c.provider === 'sandbox' && <div className="s">A pretend inbox for testing. Send it emails to try your watches.</div>}
                {c.status === 'needs_attention' && <div className="s" style={{ color: 'var(--fg)', fontWeight: 600 }}>Needs attention: {c.status_reason}</div>}
                {c.provider === 'm365' && c.delivery_note && <div className="s">{c.delivery_note}</div>}
              </div>
              <div className="row" style={{ flexShrink: 0 }}>
                {c.provider === 'sandbox' && <button className="btn sm" aria-expanded={open === c.id} onClick={(e) => { e.stopPropagation(); setOpen(open === c.id ? null : c.id) }}>Send test email {open === c.id ? '▴' : '▾'}</button>}
                {c.status === 'needs_attention' && <button className="btn sm" onClick={(e) => { e.stopPropagation(); link({ connection_id: c.id }) }}>Reconnect</button>}
                <button className="btn sm ghost" onClick={async (e) => { e.stopPropagation(); if (await ask({ title: `Disconnect ${c.email}?`, body: 'Its watches are deleted too.', yes: 'Disconnect', no: 'Keep it', danger: true })) { await api.removeConnection(c.id); reload() } }}>Disconnect</button>
              </div>
            </div>
            {open === c.id && <Sandbox c={c} go={go} hasWatch={d.watches.some((w) => w.connection_id === c.id)} />}
          </div>
        ))}
      </div>

      <h2>Connect a source</h2>
      <div className="row">
        <button className="btn ghost" onClick={() => link({ provider: 'gmail' })}>Gmail</button>
        <button className="btn ghost" disabled={prov.m365 && !prov.m365.available} onClick={() => link({ provider: 'm365' })} title={prov.m365 && !prov.m365.available ? 'Not enabled on this server yet' : ''}>Microsoft 365</button>
        <button className="btn ghost" onClick={() => setImap(!imap)}>Other (IMAP)</button>
        <button className="btn ghost" onClick={async () => { const c = await api.addConnection({ provider: 'sandbox' }); setOpen(c.id); reload() }}>+ Sandbox (for testing)</button>
      </div>
      {imap && (
        <div className="panel">
          <label>IMAP server</label><input placeholder="imap.example.com" value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })} />
          <label>Email address</label><input value={f.user} onChange={(e) => setF({ ...f, user: e.target.value })} />
          <label>Password or app password</label><input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
          <p className="small muted">The mailbox is opened read-only.</p>
          <button className="btn" onClick={addImap}>Connect</button>
        </div>
      )}
      {err && <div className="err">{err}</div>}
      {sheet}
    </>
  )
}

/* ------------------------------------------------------------------ watches */
function Watches({ d, reload, go }) {
  const [ask, sheet] = useAsk()
  const [conn, setConn] = useState('')
  const [what, setWhat] = useState(() => { try { const p = sessionStorage.getItem('wf-preselect'); if (p) { sessionStorage.removeItem('wf-preselect'); return [p] } } catch {} return ['enquiry'] })
  const [editId, setEditId] = useState('')
  const [hook, setHook] = useState('')
  const [err, setErr] = useState('')
  const [fresh, setFresh] = useState(null)
  const [note, setNote] = useState({})
  const [open, setOpen] = useState(null)
  const [dl, setDl] = useState([])
  const [saving, setSaving] = useState(false)
  const [made, setMade] = useState(null)

  const toggle = (id) => setWhat((w) => (w.includes(id) ? w.filter((x) => x !== id) : [...w, id]))
  const create = async () => {
    if (saving) return
    setErr(''); setSaving(true)
    try {
      const cid = conn || d.connections[0]?.id
      const w = editId ? await api.editWatch(editId, { what, webhook_url: hook }) : await api.addWatch({ connection_id: cid, what, webhook_url: hook })
      if (w.webhook_secret) setFresh(w)
      if (!editId) {
        setMade({ labels: what.map((id) => d.presets.find((p) => p.id === id)?.label || id), sandbox: d.connections.find((c) => c.id === cid)?.provider === 'sandbox', hook: !!hook })
        setTimeout(() => document.getElementById('watch-made')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 80)
      }
      setHook(''); setEditId(''); reload()
    } catch (e) { setErr(e.message) }
    setSaving(false)
  }
  const startEdit = (w) => {
    setEditId(w.id); setWhat(w.what); setHook(w.webhook_url || ''); setErr('')
    setTimeout(() => document.getElementById('watch-form')?.scrollIntoView({ behavior: 'smooth' }), 50)
  }
  const stopEdit = () => { setEditId(''); setHook(''); setWhat(['enquiry']); setErr('') }
  const test = async (w) => {
    try { const r = await api.testWatch(w.id); setNote({ ...note, [w.id]: r.delivered ? `Delivered (HTTP ${r.status_code})` : `Not delivered${r.status_code ? ` (HTTP ${r.status_code})` : ': could not reach your server'}` }) }
    catch (e) { setNote({ ...note, [w.id]: e.message }) }
  }
  const showDl = async (w) => { if (open === w.id) return setOpen(null); setDl(await api.deliveries(w.id)); setOpen(w.id) }
  const mail = (id) => d.connections.find((c) => c.id === id)?.email || id

  return (
    <>
      <h1>Watches</h1>
      <p className="muted">A watch says which source to read and what to catch. Matches go to your webhook.</p>
      {!made && d.watches.length > 0 && d.matches.length === 0 && d.watches.some((w) => d.connections.find((c) => c.id === w.connection_id)?.provider === 'sandbox') && (
        <NextStep title="send a test email" text="Your watch is running. Send an email into the sandbox mailbox and it should appear under Caught." button="Send a test email" onClick={() => go('sources')} />
      )}
      {fresh && <Reveal title="Webhook signing secret" value={fresh.webhook_secret} hint="Use this to verify the X-WatchFox-Signature header on deliveries. Shown once." onClose={() => setFresh(null)} />}
      <div className="list">
        {d.watches.length === 0 && <div className="li"><span className="s">No watches yet.</span></div>}
        {d.watches.map((w) => (
          <div key={w.id}>
            <div className="li" style={{ alignItems: 'flex-start' }}>
              <div style={{ minWidth: 0 }}>
                <div className="t">{w.labels.join(' · ')}</div>
                <div className="s">{mail(w.connection_id)} · {w.last_checked ? `checked ${ago(w.last_checked)}` : 'starting'}{w.webhook_url ? ` · ${w.webhook_url}` : ' · no webhook'}</div>
                {w.last_error && <div className="s" style={{ color: 'var(--fg)', fontWeight: 600 }}>Error: {w.last_error}</div>}
                {note[w.id] && <div className="s"><b>{note[w.id]}</b></div>}
              </div>
              <div className="row" style={{ flexShrink: 0 }}>
                {w.webhook_url && <><button className="btn sm ghost" onClick={() => test(w)}>Test webhook</button><button className="btn sm ghost" onClick={() => showDl(w)}>Deliveries</button></>}
                <button className="btn sm ghost" onClick={() => startEdit(w)}>Edit</button>
                <button className="btn sm ghost" onClick={async () => { if (await ask({ title: 'Delete this watch?', body: 'Its records are deleted too.', yes: 'Delete', no: 'Keep it', danger: true })) { await api.removeWatch(w.id); reload() } }}>Delete</button>
              </div>
            </div>
            {open === w.id && (
              <div style={{ padding: '0 1.1rem 1rem' }}>
                {dl.length === 0 ? <p className="small muted">No deliveries yet.</p> : (
                  <table><thead><tr><th>When</th><th>Status</th><th>Code</th><th>Tries</th></tr></thead>
                    <tbody>{dl.map((x) => <tr key={x.id}><td>{ago(x.created)}</td><td>{x.status}</td><td>{x.code || '–'}</td><td>{x.attempts}</td></tr>)}</tbody></table>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <h2 id="watch-form">{editId ? 'Edit watch' : 'New watch'}</h2>
      {made ? (
        <div className="card" id="watch-made">
          <h3>Now watching for {made.labels.join(' · ')}</h3>
          <p className="muted">{made.sandbox ? 'Send a test email into your sandbox and it should appear under Caught within a minute.' : 'New mail that matches will appear under Caught' + (made.hook ? ' and go to your webhook.' : ' and through the API.')}</p>
          <div className="row">
            {made.sandbox ? <button className="btn" onClick={() => go('sources')}>Send a test email →</button> : <button className="btn" onClick={() => go('caught')}>Go to Caught →</button>}
            <button className="btn ghost" onClick={() => setMade(null)}>Add another watch</button>
          </div>
        </div>
      ) : d.connections.length === 0 ? <p className="muted">Connect a source first (a sandbox is fine).</p> : (
        <div className="panel">
          {!editId && <><label>Which source?</label>
          <div className="chips">{d.connections.map((c) => <button key={c.id} className="chip" aria-pressed={(conn || d.connections[0].id) === c.id} onClick={() => setConn(c.id)}>{c.email}</button>)}</div></>}
          {editId && <p className="small muted">The source cannot change. To watch a different one, create a new watch. Emails already checked are not checked again.</p>}
          <label>What should it catch?</label>
          <p className="small muted" style={{ margin: '0 0 .4rem' }}>Pick one or several. Each is judged on its own, so choosing several never changes the result for any of them. Use separate watches only if each needs its own webhook address.</p>
          <div className="chips">{d.presets.map((p) => <button key={p.id} className="chip" aria-pressed={what.includes(p.id)} onClick={() => toggle(p.id)} title={p.description}>{p.label}</button>)}
            {!editId && <button className="chip" onClick={() => go('types')} title="Describe your own type and tune it, then come back here to use it">+ Create your own type</button>}</div>
          <label>Webhook URL (optional)</label>
          <input placeholder="https://yourapp.com/hooks/watchfox" value={hook} onChange={(e) => setHook(e.target.value)} />
          <p className="small muted">Without one, matches still appear under Caught and through the API.</p>
          {err && <div className="err">{err}</div>}
          <div className="row"><button className="btn" disabled={what.length === 0 || saving} onClick={create}>{saving ? 'Saving…' : editId ? 'Save changes' : 'Start watching'}</button>{editId && <button className="btn ghost" onClick={stopEdit}>Cancel</button>}</div>
        </div>
      )}
      {sheet}
    </>
  )
}

/* ------------------------------------------------------------------ caught */
function Caught({ d, go }) {
  const [open, setOpen] = useState(null)
  const shown = d.matches.slice(0, 10)
  return (
    <>
      <h1>Caught</h1>
      <p className="muted">The latest emails your watches matched, newest first.</p>
      <ApiHint path="/v1/matches?limit=10" summary="Get these from your code (GET /v1/matches)" />
      <div className="list">
        {shown.length === 0 && <div className="li"><span className="s">Nothing caught yet. Send a test email to a sandbox mailbox, or wait for real mail.</span></div>}
        {shown.map((m, i) => (
          <div key={m.watch_id + m.email.id + i}>
            <div className="li click" onClick={() => setOpen(open === i ? null : i)}>
              <div style={{ minWidth: 0 }}><div className="t">{m.email.subject || '(no subject)'}</div><div className="s">{m.email.from} · {ago(m.checked)}</div></div>
              <div className="row">{Object.values(m.results).filter((r) => r.matched).map((r) => <Conf key={r.label} r={r} />)}</div>
            </div>
            {open === i && <div style={{ padding: '0 1.1rem 1rem' }}><p className="small muted" style={{ margin: '0 0 .6rem' }}>{m.email.snippet}</p><Flag members={[m]} go={go} /></div>}
          </div>
        ))}
      </div>
      {shown.length > 0 && <p className="small muted">{CONF_HELP}</p>}
      {shown.length > 0 && <p><button className="btn ghost" onClick={() => go('log')}>View the full log →</button></p>}
    </>
  )
}

/* ------------------------------------------------------------------ activity log */
const DELIVERY = {
  delivered: ['Delivered', 'on'], failed: ['Failed', 'bad'], retrying: ['Retrying', 'dim'], pending: ['Waiting', 'dim'],
}

function MiniDelivery({ x }) {
  if (!x) return <span className="muted">–</span>
  const [label, cls] = DELIVERY[x.status] || [x.status, 'dim']
  return <span className={`pill ${cls}`} title={`${label}${x.http_status ? ` (HTTP ${x.http_status})` : ''}${x.attempts > 1 ? `, ${x.attempts} tries` : ''} · ${x.webhook_url || ''}`}>{label}</span>
}

function DeliveryPill({ x }) {
  if (!x) return <span className="muted">no webhook</span>
  const [label, cls] = DELIVERY[x.status] || [x.status, 'dim']
  return <span className={`pill ${cls}`} title={x.webhook_url}>{label}{x.http_status ? ` ${x.http_status}` : ''}{x.attempts > 1 ? ` · ${x.attempts} tries` : ''}</span>
}

function LogDetail({ e, go, d }) {
  const x = e.delivery
  const rows = [
    ['Received', fmtTime(e.received_at)], ['Checked', fmtTime(e.checked_at)], ['Email id', e.email.id], ['Watch', e.watch_id], ['Source', e.connection_id],
    ...(x ? [['Webhook', x.webhook_url], ['Sent', x.sent_at ? fmtTime(x.sent_at) : '–'], ['Last attempt', x.last_attempt_at ? fmtTime(x.last_attempt_at) : '–'],
             ['Delivered', x.delivered_at ? fmtTime(x.delivered_at) : 'no'], ['Attempts', x.attempts ?? '–'], ['HTTP status', x.http_status ?? '–'],
             ['Error', x.error || 'none'], ['Event id', x.event_id || '–']] : []),
    ['Sent with the webhook', e.include.length ? e.include.join(', ') : 'summary only'],
  ]
  const ignored = e.ignored.map((i) => `${i.label}: ${i.reason}`).join('; ')
  return (
    <div className="logdetail">
      {e.email.snippet && <p className="small muted" style={{ margin: '0 0 .6rem' }}>“{e.email.snippet}”</p>}
      {ignored && <p className="small"><b>Skipped:</b> {ignored}</p>}
      {e.members.length > 1 && (
        <div className="note" style={{ margin: '0 0 .8rem' }}>
          <b>{e.members.length} watches checked this email ({e.members.length} runs).</b> Each watch is a separate run. To use one run per email, put the types in a single watch.
          <ul style={{ margin: '.4rem 0 0', paddingLeft: '1.2rem' }}>
            {e.members.map((m) => <li key={m.watch_id}>{(d.watches.find((w) => w.id === m.watch_id)?.labels || [m.watch_id]).join(', ')}: {m.matched ? m.matches.map((t) => `caught as ${t.label} ${t.confidence}%`).join(', ') : 'not caught'}</li>)}
          </ul>
        </div>
      )}
      <dl>{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd className="mono">{String(v)}</dd></div>)}</dl>
      <Flag members={e.members} go={go} />
      {e.links && <p className="small muted">Original email: <code>GET {e.links.raw.replace(BASE, '')}</code> (read from the mailbox when you ask; WatchFox keeps no copy).</p>}
    </div>
  )
}

function Log({ d, go }) {
  const [view, setView] = useState('all')
  const [watch, setWatch] = useState('')
  const [text, setText] = useState('')
  const [range, setRange] = useState(0)
  const [items, setItems] = useState([])
  const [next, setNext] = useState(null)
  const [open, setOpen] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const params = (before) => {
    const p = { limit: 100 }
    if (view === 'matched') p.matched = 'true'
    if (view === 'unmatched') p.matched = 'false'
    if (view === 'failed') p.delivery = 'failed'
    if (watch) p.watch_id = watch
    if (text.trim()) p.q = text.trim()
    if (range) p.since = Math.floor(Date.now() / 1000) - range
    if (before) p.before = before
    return p
  }
  const first = useCallback(async () => {
    try { const r = await api.log(params()); setItems(r.items); setNext(r.next_before); setLoaded(true) } catch {}
  }, [view, watch, text, range])            // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(first, text ? 300 : 0); return () => clearTimeout(t) }, [first, text])
  useEffect(() => { if (open === null && items.length <= 100) { const t = setInterval(first, 5000); return () => clearInterval(t) } }, [first, open, items.length])
  const more = async () => { setBusy(true); try { const r = await api.log(params(next)); setItems((c) => [...c, ...r.items]); setNext(r.next_before) } finally { setBusy(false) } }

  // one row per email: each watch records its own result, so an email checked by two watches would show twice
  const groups = (() => {
    const by = new Map()
    items.forEach((x) => { const k = `${x.connection_id}|${x.email.id}`; by.has(k) ? by.get(k).push(x) : by.set(k, [x]) })
    return [...by.entries()].map(([k, ms]) => ({ ...ms[0], id: k, members: ms, matched: ms.some((m) => m.matched),
      matches: ms.flatMap((m) => m.matches), ignored: ms.flatMap((m) => m.ignored), delivery: (ms.find((m) => m.delivery) || {}).delivery || null }))
  })()
  const exportPath = '/v1/log/export?' + (() => { const { limit, before, ...rest } = params(); return new URLSearchParams(rest).toString() })()
  const apiPath = '/v1/log?' + new URLSearchParams({ ...params(), limit: 100 }).toString()
  const mailbox = (id) => d.connections.find((c) => c.id === id)?.email || id
  const VIEWS = [['all', 'All checked'], ['matched', 'Caught'], ['unmatched', 'Not caught'], ['failed', 'Webhook failed']]

  return (
    <div className="logpage">
      <h1>Activity log</h1>
      <p className="muted">Every email a watch checked, caught or not: what it decided and what happened to your webhook. We keep only the subject, sender and a short snippet, never the email itself.</p>
      <div className="retention"><b>Logs are kept for 90 days</b>, then deleted automatically. To keep them longer, download them first: use Export CSV below, or <code>GET /v1/log</code> from your code.</div>
      <ApiHint path={apiPath} summary="Get this log from your code (GET /v1/log). The link follows your filters." />
      <div className="toolbar">
        <div className="chips">{VIEWS.map(([id, label]) => <button key={id} className="chip" aria-pressed={view === id} onClick={() => setView(id)}>{label}</button>)}</div>
        <div className="row">
          <select value={range} onChange={(e) => setRange(Number(e.target.value))} aria-label="Time">
            <option value={0}>Any time</option><option value={3600}>Last hour</option><option value={86400}>Last 24 hours</option><option value={604800}>Last 7 days</option>
          </select>
          <select value={watch} onChange={(e) => setWatch(e.target.value)} aria-label="Watch">
            <option value="">All watches</option>
            {d.watches.map((w) => <option key={w.id} value={w.id}>{w.labels.join(', ')} · {mailbox(w.connection_id)}</option>)}
          </select>
          <input style={{ width: '13rem' }} placeholder="Search emails" value={text} onChange={(e) => setText(e.target.value)} />
          <a className="btn sm ghost" href={exportPath} download>Export CSV</a>
        </div>
      </div>
      <div className="logwrap" onScroll={(ev) => { const t = ev.currentTarget; if (next && !busy && t.scrollTop + t.clientHeight >= t.scrollHeight - 160) more() }}>
        <table className="logt">
          <colgroup><col style={{ width: '8.5rem' }} /><col /><col style={{ width: '9rem' }} /><col style={{ width: '11.5rem' }} /><col style={{ width: '6rem' }} /></colgroup>
          <thead><tr><th>Checked</th><th>Email</th><th>Watch</th><th>Result</th><th>Webhook</th></tr></thead>
          <tbody>
            {loaded && items.length === 0 && <tr><td colSpan={5} className="muted">Nothing here yet. Send a test email, or change the filters.</td></tr>}
            {groups.map((e) => {
              const labels = [...new Set(e.members.flatMap((m) => d.watches.find((w) => w.id === m.watch_id)?.labels || [m.watch_id]))]
              const first = e.matches[0]
              return (
                <Fragment key={e.id}>
                  <tr className="logrow" onClick={() => setOpen(open === e.id ? null : e.id)} aria-expanded={open === e.id}>
                    <td className="cut" title={fmtTime(e.checked_at)}>{fmtShort(e.checked_at)}</td>
                    <td className="cut" title={`${e.email.subject || '(no subject)'} · ${e.email.from}`}><b>{e.email.subject || '(no subject)'}</b> <span className="s">{e.email.from}</span></td>
                    <td className="cut" title={`${labels.join(', ')} · ${mailbox(e.connection_id)}`}>{e.members.length > 1 ? <>{e.members.length} watches <span className="s">({e.members.length} runs)</span></> : labels[0]}</td>
                    <td className="cut">{e.matched ? <>
                      <span className="pill on" title={e.matches.map((m) => `${m.label} ${m.confidence}%`).join(', ')}>{first.label} {first.confidence}%</span>{e.matches.length > 1 && <span className="s"> +{e.matches.length - 1}</span>}</>
                      : e.ignored.length ? <span className="s" title={e.ignored[0].reason}>Skipped: {e.ignored[0].reason}</span> : <span className="muted">Not caught</span>}</td>
                    <td className="cut"><MiniDelivery x={e.delivery} /></td>
                  </tr>
                  {open === e.id && <tr className="logopen"><td colSpan={5}><LogDetail e={e} go={go} d={d} /></td></tr>}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="small muted">{groups.length} email{groups.length === 1 ? '' : 's'} shown (one row per email; each watch that checked it is one run){next ? ', more load as you scroll' : ''}. Click a row for details. Not sure why a count went up? See the <a href="#/docs/faq">FAQ</a>. For large volumes use <code>GET /v1/log</code> with filters.</p>
      {next && <p><button className="btn ghost" disabled={busy} onClick={more}>{busy ? 'Loading…' : 'Load more'}</button></p>}
      <p className="small muted">{CONF_HELP}</p>
    </div>
  )
}

/* ------------------------------------------------------------------ your own types */
const splitExamples = (text) => text.split(/\n\s*---+\s*\n/).map((t) => t.trim()).filter(Boolean).map((t) => {
  const m = t.match(/^subject:\s*(.*)\n?([\s\S]*)$/i)
  return m ? { subject: m[1].trim(), body: m[2].trim() || m[1].trim() } : { subject: '', body: t }
})
const joinExamples = (xs) => xs.map((e) => `Subject: ${e.subject}\n${e.body}`).join('\n---\n')
const append = (old, xs) => [old.trim(), joinExamples(xs)].filter(Boolean).join('\n---\n')
const count = (text) => splitExamples(text).length

function Result({ draft, onSave, onDiscard, onBack, saveLabel = 'Save this type' }) {
  const r = draft.report
  const gen = r.generated || { positives: 0, negatives: 0 }
  const own = r.own_look_alikes || 0
  return (
    <div className="card">
      <h3>Check the result</h3>
      <p>It caught <b>{r.caught} of {r.samples.should_catch}</b> emails that should match and raised <b>{r.false_alarms_after}</b> false alarm{r.false_alarms_after === 1 ? '' : 's'} among {r.samples.should_ignore} that should not.</p>
      {own === 0 && gen.negatives === 0 && <div className="warn"><b>Not tested against your own look-alikes.</b> You gave no “should not be caught” emails, so this was only checked against generic samples. It may catch things you did not expect. Add a few look-alikes and tune again.</div>}
      {own === 0 && gen.negatives > 0 && <div className="warn"><b>Tested against look-alikes the assistant invented, not yours.</b> Add real ones when you have them (the “Wrong catch” button on a caught email does that for you).</div>}
      {r.corrections && <p>Of the {r.corrections.wrong + r.corrections.missed} emails you flagged, <b>{r.corrections.still_wrong}</b> {r.corrections.still_wrong === 1 ? 'is' : 'are'} still wrong.</p>}
      <div className="s">What WatchFox will look for</div>
      <div className="mono">{draft.question}</div>
      <div className="s" style={{ marginTop: '.6rem' }}>Skips it when: {draft.excludes.length ? draft.excludes.map((e) => e.question).join(' · ') : 'nothing extra needed'}</div>
      {r.mistakes.length > 0 && <>
        <div className="s" style={{ marginTop: '.6rem' }}>Mistakes it still makes</div>
        {r.mistakes.map((m, i) => <div className="s" key={i}>{m.kind === 'missed' ? 'Missed' : 'False alarm'}: {m.subject}</div>)}
      </>}
      {(r.left_out_as_similar || []).length > 0 && <div className="s" style={{ marginTop: '.6rem' }}>Not used as “should ignore” because they look like your type: {r.left_out_as_similar.join(', ')}.</div>}
      {(gen.positives + gen.negatives) > 0 && <div className="s">Used {gen.positives} invented matching and {gen.negatives} invented look-alike emails to tune it.</div>}
      {(r.notes || []).map((n, i) => <div className="s" key={i}>{n}</div>)}
      <p className="muted">{r.note} More real examples make it better: tune again after adding them.</p>
      <div className="row"><button className="btn" onClick={onSave}>{saveLabel}</button>{onBack && <button className="btn ghost" onClick={onBack}>Change the examples and tune again</button>}<button className="btn ghost" onClick={onDiscard}>Discard</button></div>
    </div>
  )
}

function Corrections({ d, reload }) {
  const [ask, sheet] = useAsk()
  const [rows, setRows] = useState([])
  const [busy, setBusy] = useState('')
  const [draft, setDraft] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => api.corrections().then(setRows).catch(() => {}), [])
  useEffect(() => { load() }, [load])
  if (rows.length === 0 && !draft) return null
  const tune = async (what) => { setErr(''); setBusy(what); try { setDraft({ what, ...(await api.retune(what)) }) } catch (e) { setErr(e.message) } setBusy('') }
  const save = async () => {
    try {
      await api.updateType(draft.what, { label: draft.label, description: draft.description, question: draft.question, threshold: draft.threshold, excludes: draft.excludes, ignore_subject: draft.ignore_subject || [], report: draft.report, inputs: draft.inputs })
      setDraft(null); load(); reload()
    } catch (e) { setErr(e.message) }
  }
  return (
    <>
      <h2>Corrections</h2>
      <p className="muted">Emails you flagged as wrong catches or misses. Tuning again adds them to the examples. A copy of each flagged email's subject and text is kept for this only; clear them any time.</p>
      <div className="list">
        {rows.map((c) => (
          <div className="li" key={c.what}>
            <div><div className="t">{c.label}{c.tuned_for_you ? ' · tuned for you' : ''}</div><div className="s">{c.wrong} wrong catch{c.wrong === 1 ? '' : 'es'} · {c.missed} missed</div></div>
            <div className="row">
              <button className="btn sm" disabled={!!busy} onClick={() => tune(c.what)}>{busy === c.what ? 'Tuning…' : 'Tune again with these'}</button>
              <button className="btn sm ghost" onClick={async () => { if (await ask({ title: 'Forget these flagged emails?', body: 'The type stops learning from them.', yes: 'Forget them', no: 'Keep them', danger: true })) { await api.clearCorrections(c.what); load() } }}>Clear</button>
              {c.tuned_for_you && !c.what.startsWith('type_') && <button className="btn sm ghost" onClick={async () => { if (await ask({ title: 'Go back to the built-in settings?', body: 'The tuning you made for this type is removed.', yes: 'Reset', no: 'Keep my tuning', danger: true })) { await api.deleteType(c.what); load(); reload() } }}>Reset</button>}
            </div>
          </div>
        ))}
      </div>
      {err && <div className="err">{err}</div>}
      {draft && <Result draft={draft} onSave={save} onDiscard={() => setDraft(null)} saveLabel="Use these settings" />}
      {sheet}
    </>
  )
}

function TryIt({ what }) {
  const [text, setText] = useState('')
  const [res, setRes] = useState(null)
  const [err, setErr] = useState('')
  const run = async () => {
    setErr(''); setRes(null)
    try { const r = await api.classify({ body: text, what }); setRes(Object.values(r.results)[0]) } catch (e) { setErr(e.message) }
  }
  return (
    <div className="card">
      <h3>Try it on an email</h3>
      <p className="small muted" style={{ margin: 0 }}>Each check uses 1 run.</p>
      <textarea rows={4} placeholder="Paste the text of an email" value={text} onChange={(e) => setText(e.target.value)} />
      <div className="row"><button className="btn sm" disabled={text.trim().length < 5} onClick={run}>Check it</button></div>
      {res && <p>{res.matched ? <><b>Caught</b> · <Conf r={res} /></> : <b>Not caught</b>}{res.excluded && res.reason ? ` (${res.reason})` : ''}</p>}
      {err && <div className="err">{err}</div>}
    </div>
  )
}

function NextSteps({ made, go, onEdit, onAnother, onClose }) {
  const [tryOpen, setTryOpen] = useState(false)
  const toWatch = () => { try { sessionStorage.setItem('wf-preselect', made.id) } catch {} go('watches') }
  return (
    <div className="card">
      <h3>“{made.label}” is saved. What next?</h3>
      <div className="row">
        <button className="btn" onClick={toWatch}>Add it to a watch</button>
        <button className="btn ghost" onClick={() => setTryOpen(!tryOpen)}>Try it on an email</button>
        <button className="btn ghost" onClick={onEdit}>Edit and tune again</button>
        <button className="btn ghost" onClick={onAnother}>Create another type</button>
        <button className="btn ghost" onClick={onClose}>Done</button>
      </div>
      <p className="small muted">A type does nothing until a watch uses it. Once it is in a watch, flag any mistakes with “Wrong catch” on Caught or the Activity log, and tune it again from here.</p>
      {tryOpen && <TryIt what={made.id} />}
    </div>
  )
}

/* Long waits (the assistant, tuning) say what they are doing, so a button never looks stuck. Moves to the next message every few seconds. */
function useProgress(active, steps, every = 3500) {
  const [i, setI] = useState(0)
  useEffect(() => {
    if (!active) { setI(0); return }
    const t = setInterval(() => setI((n) => Math.min(n + 1, steps.length - 1)), every)
    return () => clearInterval(t)
  }, [active])           // eslint-disable-line react-hooks/exhaustive-deps
  return active ? steps[i] : ''
}

const Spin = () => <span className="spin" aria-hidden="true" />

function Types({ d, reload, go }) {
  const [ask, sheet] = useAsk()
  const empty = { label: '', description: '', pos: '', neg: '', kinds: [], assistant: true, idea: '' }
  const [f, setF] = useState(empty)
  const [info, setInfo] = useState({ assistant: false, kinds: [] })
  const [busy, setBusy] = useState('')
  const [draft, setDraft] = useState(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState('')
  const [made, setMade] = useState(null)
  const [drafted, setDrafted] = useState(false)
  const suggestMsg = useProgress(busy === 'suggest', ['Reading your idea…', 'Writing a name and a description…', 'Making example emails that should be caught…', 'Writing look-alikes that should not…', 'Nearly done…'])
  const tuneMsg = useProgress(busy === 'tune', ['Trying different ways of asking…', 'Scoring them on your examples…', 'Picking the one that separates them best…', 'Setting how sure it must be…', 'Checking what it catches and misses…', 'Nearly done, thank you for waiting…'], 6000)
  const moreMsg = useProgress(busy === 'pos' || busy === 'neg', ['Writing examples…', 'Making them different from each other…', 'Nearly done…'])
  useEffect(() => { api.typeKinds().then(setInfo).catch(() => {}) }, [])
  const formRef = useRef(null)
  useEffect(() => {          // when the form opens, bring it into view and put the cursor in its first box
    if (!open || draft) return
    const t = setTimeout(() => {
      formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      formRef.current?.querySelector('input, textarea')?.focus({ preventScroll: true })
    }, 80)
    return () => clearTimeout(t)
  }, [open, editing, info.assistant])
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }))
  const mine = d.presets.filter((p) => p.maturity === 'custom')
  const ready_made = d.presets.filter((p) => p.maturity !== 'custom')
  const ready = f.label.trim() && f.description.trim().length >= 10

  const suggest = async () => {
    setErr(''); setBusy('suggest')
    try {
      const s = await api.suggestType(f.idea)
      setF((x) => ({ ...x, label: s.label, description: s.description, pos: joinExamples(s.positives), neg: joinExamples(s.negatives) }))
      setDrafted(true)
      setTimeout(() => document.getElementById('type-step1')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 120)
    } catch (e) { setErr(e.message) }
    setBusy('')
  }
  const more = async (which) => {
    setErr(''); setBusy(which)
    try {
      const r = await api.moreExamples({ label: f.label, description: f.description, positives: which === 'pos' ? 5 : 0, negatives: which === 'neg' ? 5 : 0 })
      if (which === 'pos') set('pos', append(f.pos, r.positives)); else set('neg', append(f.neg, r.negatives))
    } catch (e) { setErr(e.message) }
    setBusy('')
  }
  const tune = async () => {
    setErr(''); setBusy('tune'); setDraft(null)
    try {
      setDraft(await api.tuneType({ label: f.label, description: f.description, positives: splitExamples(f.pos), negatives: splitExamples(f.neg), treat_as_yes: f.kinds, use_assistant: f.assistant }))
    } catch (e) { setErr(e.message) }
    setBusy('')
  }
  const save = async () => {
    const b = { label: draft.label, description: draft.description, question: draft.question, threshold: draft.threshold, excludes: draft.excludes, report: draft.report, inputs: draft.inputs }
    try {
      let id = editing
      if (editing) await api.updateType(editing, b); else id = (await api.saveType(b)).id
      setMade({ id, label: draft.label }); setDraft(null); setOpen(false); setEditing(''); setF(empty); reload()
    } catch (e) { setErr(e.message) }
  }
  const edit = async (id) => {
    setErr(''); setMade(null)
    try {
      const t = await api.getType(id); const i = t.inputs || {}
      setF({ ...empty, label: t.label, description: t.description, pos: joinExamples(i.positives || []), neg: joinExamples(i.negatives || []), kinds: i.treat_as_yes || [] })
      setEditing(id); setDraft(null); setOpen(true)
      setTimeout(() => window.scrollTo({ top: 0, behavior: 'smooth' }), 50)
    } catch (e) { setErr(e.message) }
  }
  const cancel = () => { setOpen(false); setDraft(null); setEditing(''); setF(empty); setDrafted(false) }
  const nPos = count(f.pos)
  const nNeg = count(f.neg)
  const FULL = 20
  return (
    <>
      <h1>Your types</h1>
      <p className="muted">A type is one thing you want caught. The defaults are ready to use. Or describe your own: WatchFox tunes it from your examples by trying several wordings and keeping the best.</p>
      {!open && !made && <div className="row" style={{ margin: '1rem 0' }}><button className="btn" onClick={() => setOpen(true)}>Create a new type</button></div>}
      <h2>Ready to use</h2>
      <p className="muted small">Already tuned. Pick one and go straight to a watch.</p>
      <div className="list">
        {ready_made.map((t) => (
          <div className="li" key={t.id}>
            <div><div className="t">{t.label}</div><div className="s">{t.description}</div></div>
            <button className="btn sm ghost" style={{ flexShrink: 0 }} onClick={() => { try { sessionStorage.setItem('wf-preselect', t.id) } catch {} go('watches') }}>Use in a watch</button>
          </div>
        ))}
      </div>
      <h2>Your own types</h2>
      <div className="list">
        {mine.length === 0 && <div className="li"><span className="s">No types of your own yet. Create one above and tune it with your own examples.</span></div>}
        {mine.map((t) => (
          <div className="li" key={t.id}>
            <div><div className="t">{t.label}</div><div className="s">{t.description}{t.caught != null ? `  ·  caught ${t.caught} of ${t.samples.should_catch} test examples` : ''}  ·  <span className="mono">{t.id}</span></div></div>
            <div className="row" style={{ flexShrink: 0 }}>
              <button className="btn sm ghost" onClick={() => edit(t.id)}>Edit</button>
              <button className="btn sm ghost" onClick={() => { try { sessionStorage.setItem('wf-preselect', t.id) } catch {} go('watches') }}>Use in a watch</button>
              <button className="btn sm ghost" onClick={async () => { if (await ask({ title: `Delete "${t.label}"?`, body: 'Watches using it stop catching it.', yes: 'Delete', no: 'Keep it', danger: true })) { try { await api.deleteType(t.id); reload() } catch (e) { await ask({ title: 'Could not delete', body: e.message, yes: 'OK', no: null }) } } }}>Delete</button>
            </div>
          </div>
        ))}
      </div>
      {made && !open && <NextSteps made={made} go={go} onEdit={() => edit(made.id)} onAnother={() => { setMade(null); setOpen(true) }} onClose={() => setMade(null)} />}
      {open && !draft && (
        <div className="card" ref={formRef} style={{ scrollMarginTop: '6rem' }}>
          {editing && <h3>Editing “{f.label}”</h3>}
          {info.assistant && !editing && <>
            <div className="assist">
              <span className="assist-badge">✦ AI assistant</span>
              <h3>Don't want to type it all? Let the AI assistant draft it.</h3>
              <p className="muted">Type one line about what you want to catch. The assistant drafts the name, description and examples. Edit anything before you save. It never sees your real email.</p>
              <label htmlFor="idea">What do you want to catch?</label>
              <div className="row"><input id="idea" placeholder="e.g. suppliers telling us a delivery will be late" value={f.idea} onChange={(e) => set('idea', e.target.value)} />
                <button className="btn" disabled={busy !== '' || f.idea.trim().length < 8} onClick={suggest}>{busy === 'suggest' ? <><Spin /> {suggestMsg}</> : 'Draft with AI assistant'}</button></div>
              {busy === 'suggest' && <div role="status" aria-live="polite"><div className="progress"><i /></div><p className="small muted" style={{ margin: '.4rem 0 0' }}>The assistant is working on your draft. This usually takes under 20 seconds.</p></div>}
              <div className="chips" style={{ marginTop: '.6rem' }}>
                <span className="small muted" style={{ alignSelf: 'center' }}>Try:</span>
                {['Suppliers telling us a delivery will be late', 'Customers asking to cancel their subscription', 'Partners asking to book a demo'].map((t) => <button key={t} type="button" className="chip" onClick={() => set('idea', t)}>{t}</button>)}
              </div>
              {drafted && <div className="note" style={{ marginTop: '.8rem' }}><b>Drafted.</b> Check the name, description and examples below, change anything you like, then press <b>Tune it</b>.</div>}
            </div>
            <p className="assist-or">or fill in the form yourself</p>
          </>}
          <h3 id="type-step1" style={{ scrollMarginTop: '6rem' }}>1. Describe it</h3>
          <label>Name</label>
          <input placeholder="e.g. Shipping delays" value={f.label} onChange={(e) => set('label', e.target.value)} />
          <label>What counts? One or two sentences, as you would explain it to a new colleague.</label>
          <textarea rows={3} placeholder="e.g. A supplier telling us that an order will arrive later than promised." value={f.description} onChange={(e) => set('description', e.target.value)} />
          <h3>2. Show it what to catch</h3>
          <label>Emails that SHOULD be caught ({nPos} so far; at least 3, and 10 to 20 is plenty). Separate emails with <code>---</code> on its own line. Start an email with <code>Subject: …</code> to set its subject.</label>
          <textarea rows={7} className="mono" value={f.pos} onChange={(e) => set('pos', e.target.value)} placeholder={'Subject: Delay on PO 4411\nSorry, the shipment is delayed by two weeks.\n---\nSubject: Revised delivery date\nYour order will now ship on the 28th.'} />
          {info.assistant && <div className="row"><button className="btn sm ghost" disabled={busy !== '' || !ready || nPos >= FULL} onClick={() => more('pos')}>{busy === 'pos' ? <><Spin /> {moreMsg}</> : nPos >= FULL ? `Enough examples (${nPos})` : '+ Add 5 examples with the AI assistant'}</button></div>}
          <h3>3. Show it what to avoid</h3>
          <div className={nNeg === 0 ? 'warn' : 'note'}>
            <b>Look-alikes that should NOT be caught</b> ({nNeg} so far). They stop false alarms: emails on the same subject that you don't want, like reminders, confirmations, replies and adverts. Aim for 5 to 20.
          </div>
          <textarea rows={7} className="mono" value={f.neg} onChange={(e) => set('neg', e.target.value)} placeholder={'Subject: Your order has shipped\nOn time, tracking number below.\n---\nSubject: Order delivered\nYour package was delivered today.'} />
          {info.assistant && <div className="row"><button className="btn sm ghost" disabled={busy !== '' || !ready || nNeg >= FULL} onClick={() => more('neg')}>{busy === 'neg' ? <><Spin /> {moreMsg}</> : nNeg >= FULL ? `Enough look-alikes (${nNeg})` : '+ Add 5 look-alikes with the AI assistant'}</button></div>}
          {info.kinds.length > 0 && <>
            <label>Does your type overlap with any everyday kind of email? Tick those that count as yes. Similar kinds are left out of the look-alikes automatically.</label>
            <div className="chips">{info.kinds.map((k) => <button key={k.id} type="button" className="chip" aria-pressed={f.kinds.includes(k.id)} title={k.description}
              onClick={() => set('kinds', f.kinds.includes(k.id) ? f.kinds.filter((x) => x !== k.id) : [...f.kinds, k.id])}>{k.label}</button>)}</div>
          </>}
          {info.assistant && <label className="check"><input type="checkbox" checked={f.assistant} onChange={(e) => set('assistant', e.target.checked)} /> Let the assistant top up with extra wordings and invented examples while tuning. It sees only your name and description, never real email.</label>}
          <div className="row"><button className="btn" disabled={busy !== '' || !ready} onClick={tune}>{busy === 'tune' ? <><Spin /> {tuneMsg}</> : 'Tune it'}</button><button className="btn ghost" onClick={cancel}>Cancel</button></div>
          {busy === 'tune' && <div role="status" aria-live="polite"><div className="progress"><i /></div><p className="small muted" style={{ margin: '.4rem 0 0' }}>Tuning tries several wordings on your examples and picks the best. It takes up to a minute. You can keep this page open.</p></div>}
          {err && <div className="err">{err}</div>}
        </div>
      )}
      {draft && open && <Result draft={draft} onSave={save} onBack={() => setDraft(null)} onDiscard={cancel} saveLabel={editing ? 'Save changes' : 'Save this type'} />}
      {err && !open && <div className="err">{err}</div>}
      <Corrections d={d} reload={reload} />
      {sheet}
      {!open && !made && <div className="row" style={{ margin: '1rem 0' }}><button className="btn" onClick={() => setOpen(true)}>Create a new type</button></div>}
      <p className="muted">Once saved, pick a type in a watch like any other, or use its id (<code>type_…</code>) as <code>what</code> in the API.</p>
    </>
  )
}

/* "Wrong catch" / "Should have been caught": teaches the type with a real example */
function Flag({ members, go }) {
  const [msg, setMsg] = useState('')
  const [pick, setPick] = useState('')
  const all = {}
  members.forEach((m) => Object.entries(m.results || {}).forEach(([id, r]) => { if (!all[id] || r.matched) all[id] = { r, watchId: m.watch_id, emailId: m.email.id } }))
  const entries = Object.entries(all)
  const missed = entries.filter(([, v]) => !v.r.matched && !v.r.excluded)
  const send = async (what, kind) => {
    setMsg('Saving…')
    const v = all[what]
    try { await api.feedback({ what, watch_id: v.watchId, email_id: v.emailId, kind }); setMsg('saved') } catch (e) { setMsg(e.message) }
  }
  if (msg === 'saved') return <p className="small">Thanks, saved. <a href="#/dashboard/types" onClick={(e) => { e.preventDefault(); go('types') }}>Tune it again on the Types page</a> when you have flagged a few.</p>
  return (
    <div className="flag" onClick={(e) => e.stopPropagation()}>
      <div className="row">
        {entries.filter(([, v]) => v.r.matched).map(([id, v]) => <button key={id} className="btn sm ghost" onClick={() => send(id, 'wrong')}>Wrong catch: not {v.r.label}</button>)}
        {missed.length > 0 && <>
          <select value={pick || missed[0][0]} onChange={(e) => setPick(e.target.value)} aria-label="Should have been caught as">{missed.map(([id, v]) => <option key={id} value={id}>{v.r.label}</option>)}</select>
          <button className="btn sm ghost" onClick={() => send(pick || missed[0][0], 'missed')}>Should have been caught</button>
        </>}
      </div>
      <p className="small muted" style={{ margin: '.3rem 0 0' }}>This reads the email from the mailbox now and keeps a copy of its subject and text so the type can learn from it. You can delete it on the Types page.</p>
      {msg && msg !== 'Saving…' && <div className="err">{msg}</div>}
    </div>
  )
}

/* verification, plan and usage notices shown above every tab */
function Banners({ d, user, go, tab, reload }) {
  const me = d.me || user
  const [msg, setMsg] = useState('')
  const [link, setLink] = useState(() => { try { return sessionStorage.getItem('wf-dev-link') || '' } catch { return '' } })
  const justVerified = window.location.hash.includes('notice=verified')
  const plan = d.plan
  const pct = plan ? Math.round((100 * plan.runs_used) / plan.runs_limit) : 0
  return (
    <>
      {justVerified && me.verified && <div className="note">Email confirmed. You are all set.</div>}
      {!me.verified && (
        <div className="warn">
          <b>Confirm your email to start using the API.</b> We sent a link to {me.email}. Until then you can look around, but creating keys, sources and watches is switched off.
          <div className="row" style={{ marginTop: '.5rem' }}>
            <button className="btn sm ghost" onClick={async () => { try { const r = await api.resendVerify(); if (r.dev_link) setLink(r.dev_link); setMsg('We sent a new link.') } catch (e) { setMsg(e.message) } }}>Send it again</button>
            {link && <a className="btn sm" href={link} onClick={() => { try { sessionStorage.removeItem('wf-dev-link') } catch {} }}>Confirm now (no mail server on this install)</a>}
            {msg && <span className="small">{msg}</span>}
          </div>
        </div>
      )}
      {plan && pct >= 100 && <div className="warn"><b>You have used all {plan.runs_limit.toLocaleString()} runs on the {plan.plan_name} plan.</b> New mail is held back, not lost, and checking resumes when you upgrade or on the 1st. <button className="btn sm" onClick={() => go('settings')}>See plans</button></div>}
      {plan && pct >= 80 && pct < 100 && <div className="note">You have used {pct}% of this month's runs ({plan.runs_used.toLocaleString()} of {plan.runs_limit.toLocaleString()}). <button className="btn sm ghost" onClick={() => go('settings')}>See plans</button></div>}
      {plan && plan.plan === 'kit' && tab === 'start' && pct < 80 && (
        <div className="note"><b>You are on the free Kit plan: {plan.runs_limit} runs a month.</b> A run is one email checked, caught or not. Need more mailboxes, volume or teammates? <button className="btn sm ghost" onClick={() => go('settings')}>See plans</button></div>
      )}
    </>
  )
}

/* ------------------------------------------------------------------ shell */
const TABS = [['start', 'Get started'], ['sources', 'Sources'], ['types', 'Types'], ['watches', 'Watches'], ['caught', 'Caught'], ['log', 'Activity log'], ['keys', 'API keys'], ['settings', 'Settings']]

export default function Dashboard({ user, onOut }) {
  const [tab, setTab] = useState(((window.location.hash.split('/')[2] || 'start').split('?')[0]).replace('mailboxes', 'sources'))
  const [d, setD] = useState({ keys: [], connections: [], watches: [], matches: [], presets: [], providers: null, plan: null, me: null })

  const reload = useCallback(async () => {
    try {
      const [keys, connections, watches, matches, presets, providers, types, plan, me] = await Promise.all([
        api.keys(), api.connections(), api.watches(), api.matches(50), api.presets(), api.providers(), api.types(), api.plan(), api.me()])
      setD({ keys, connections, watches, matches, presets: [...presets, ...types], providers, plan, me })
    } catch (e) { if (e.status === 401) onOut() }
  }, [onOut])
  useEffect(() => { reload(); const t = setInterval(reload, 4000); return () => clearInterval(t) }, [reload])

  useEffect(() => {          // follow the address bar when it changes (links, back and forward)
    const on = () => { const t = ((window.location.hash.split('/')[2] || 'start').split('?')[0]).replace('mailboxes', 'sources'); if (TABS.some(([id]) => id === t)) setTab(t) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const go = (t) => { setTab(t); window.history.replaceState(null, '', `#/dashboard/${t}`) }
  const View = { start: Start, sources: Sources, types: Types, watches: Watches, caught: Caught, log: Log, keys: Keys, settings: Settings }[tab] || Start

  return (
    <div className="wrap dash">
      <aside className="side">
        {TABS.map(([id, label]) => <button key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => go(id)}>{label}</button>)}
        {d.plan && <button className="sidemeter" onClick={() => go('settings')} title="Plan and usage"><Meter plan={d.plan} compact /></button>}
        <div className="who">{user.email}</div>
        {d.me?.is_admin && <a className="adminlink" href="/admin" style={{ display: 'block', padding: '.5rem 0', fontSize: '.9rem' }}>Admin console</a>}
        <button onClick={onOut}>Sign out</button>
      </aside>
      <div><Banners d={d} user={user} go={go} tab={tab} reload={reload} /><View d={d} user={user} reload={reload} go={go} /></div>
    </div>
  )
}
