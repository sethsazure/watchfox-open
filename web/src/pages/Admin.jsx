import { Fragment, useCallback, useEffect, useState } from 'react'
import Fox from '../components/Fox'
import { useAsk } from '../ask'

// Every change carries a header that a page on another site cannot send, so the console cannot be driven from elsewhere.
async function adm(method, path, body) {
  const r = await fetch(`/v1/admin${path}`, {
    method, credentials: 'same-origin',
    headers: { 'X-WatchFox-Admin': '1', ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  let d = {}
  try { d = await r.json() } catch {}
  if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : r.statusText)
  return d
}

const day = (t) => (t ? new Date(t * 1000).toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' }) : '–')
const ago = (t) => {
  if (!t) return 'never'
  const s = Math.max(0, Date.now() / 1000 - t)
  return s < 90 ? 'just now' : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`
}
const TIER = { kit: 'Kit', tracker: 'Tracker', alpha: 'Alpha' }

function Stat({ label, value, sub }) {
  return <div className="stat"><div className="sv">{value}</div><div className="sl">{label}</div>{sub && <div className="ss">{sub}</div>}</div>
}

function Overview() {
  const [o, setO] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { adm('GET', '/overview').then(setO).catch((e) => setErr(e.message)) }, [])
  if (err) return <div className="err">{err}</div>
  if (!o) return <p className="muted">Loading…</p>
  const p = o.plans || {}
  return (
    <>
      <div className="stats">
        <Stat label="Organizations" value={o.orgs} sub={`${p.kit || 0} Kit · ${p.tracker || 0} Tracker · ${p.alpha || 0} Alpha`} />
        <Stat label="People" value={o.users} sub={`${o.new_24h} new in 24 h · ${o.new_7d} in 7 days`} />
        <Stat label="Paid subscriptions" value={o.subscriptions} />
        <Stat label="Suspended" value={o.suspended} />
        <Stat label="Real mailboxes" value={o.mailboxes} sub={`${o.sandboxes} sandboxes · ${o.needs_attention} need attention`} />
        <Stat label="Watches" value={o.watches} />
        <Stat label={`Runs in ${o.month}`} value={o.runs_this_month.toLocaleString()} />
        <Stat label="Checked in 24 h" value={o.checked_24h.toLocaleString()} sub={`${o.caught_24h.toLocaleString()} caught`} />
        <Stat label="Checking service" value={o.agent} sub={`${o.queued} waiting`} />
      </div>
      <p className="small muted">Counts only. This console never shows email content, mailbox passwords, webhook secrets or API keys.</p>
    </>
  )
}

function Typed({ what, expect, onGo, label }) {
  const [v, setV] = useState('')
  return (
    <div className="row" style={{ marginTop: '.6rem' }}>
      <input style={{ maxWidth: '22rem' }} placeholder={`Type ${what} to confirm`} value={v} onChange={(e) => setV(e.target.value)} aria-label={`Type ${what} to confirm`} />
      <button className="btn sm danger" disabled={v.trim().toLowerCase() !== expect.toLowerCase()} onClick={() => onGo(v.trim())}>{label}</button>
    </div>
  )
}

function OrgDetail({ id, onChanged, onClose }) {
  const [d, setD] = useState(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [plan, setPlan] = useState('kit')
  const [period, setPeriod] = useState('monthly')
  const [reason, setReason] = useState('')
  const [del, setDel] = useState(false)
  const [ask, sheet] = useAsk()
  const load = useCallback(() => adm('GET', `/orgs/${id}`).then((x) => { setD(x); setPlan(x.org.plan || 'kit'); setPeriod(x.org.billing || 'monthly') }).catch((e) => setErr(e.message)), [id])
  useEffect(() => { setD(null); setMsg(''); setErr(''); setDel(false); load() }, [load])
  const run = async (fn, ok) => { setErr(''); setMsg(''); try { const r = await fn(); setMsg(ok(r)); await load(); onChanged() } catch (e) { setErr(e.message) } }
  if (err && !d) return <div className="err">{err}</div>
  if (!d) return <p className="muted">Loading…</p>
  const o = d.org
  return (
    <div className="card admin-detail">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h3 style={{ margin: 0 }}>{o.name} {o.suspended ? <span className="pill">Suspended</span> : null}</h3>
        <button className="btn sm ghost" onClick={onClose}>Close</button>
      </div>
      <p className="small muted mono" style={{ margin: '.2rem 0 1rem' }}>{o.id} · created {day(o.created)}</p>
      {msg && <div className="note">{msg}</div>}
      {err && <div className="err">{err}</div>}

      <h4>Tier and billing</h4>
      <p className="small muted">{TIER[o.plan] || o.plan} ({o.billing}){o.helcim_subscription ? ` · paying through Helcim, status ${o.billing_status || 'active'}${o.billing_next ? `, next payment ${o.billing_next}` : ''}` : ' · no subscription'}. Limits: {d.limits.runs.toLocaleString()} runs, {d.limits.mailboxes} mailboxes, {d.limits.watches} watches, {d.limits.types} types, {d.limits.members} people.</p>
      <div className="row">
        <select value={plan} onChange={(e) => setPlan(e.target.value)} aria-label="Tier"><option value="kit">Kit</option><option value="tracker">Tracker</option><option value="alpha">Alpha</option></select>
        <select value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Billing"><option value="monthly">monthly</option><option value="annual">annual</option></select>
        <button className="btn sm" onClick={() => run(() => adm('POST', `/orgs/${id}/plan`, { plan, billing: period }), () => `Tier set to ${TIER[plan]}.`)}>Set tier</button>
      </div>
      <p className="small muted">Setting a tier gives it without charging. A paying customer's Helcim subscription is not changed or cancelled by this.</p>

      <h4>People ({d.members.length})</h4>
      <table><thead><tr><th>Email</th><th>Role</th><th>Verified</th><th>Joined</th></tr></thead>
        <tbody>{d.members.map((m) => <tr key={m.id}><td>{m.email}</td><td>{m.role}</td><td>{m.verified ? 'yes' : 'no'}</td><td>{day(m.created)}</td></tr>)}</tbody></table>
      {d.invites.filter((i) => !i.accepted).length > 0 && <p className="small muted">Waiting for an answer: {d.invites.filter((i) => !i.accepted).map((i) => i.email).join(', ')}</p>}

      <h4>Mailboxes ({d.mailboxes.length})</h4>
      {d.mailboxes.length === 0 ? <p className="small muted">None.</p> : (
        <table><thead><tr><th>Mailbox</th><th>Kind</th><th>Status</th></tr></thead>
          <tbody>{d.mailboxes.map((c) => <tr key={c.id}><td>{c.email}</td><td>{c.provider}{c.provider === 'm365' ? ` · ${c.delivery}` : ''}</td><td>{c.status === 'needs_attention' ? <b>needs attention: {c.status_reason}</b> : 'active'}</td></tr>)}</tbody></table>
      )}

      <h4>Watches ({d.watches.length})</h4>
      {d.watches.length === 0 ? <p className="small muted">None.</p> : (
        <table><thead><tr><th>Catches</th><th>Webhook host</th><th>Last run</th></tr></thead>
          <tbody>{d.watches.map((w) => <tr key={w.id}><td>{(() => { try { return JSON.parse(w.what).join(', ') } catch { return w.what } })()}</td><td>{w.webhook_host || 'none'}</td><td>{ago(w.last_run)}{w.last_error ? <b> · {w.last_error}</b> : ''}</td></tr>)}</tbody></table>
      )}

      <h4>Usage</h4>
      <p className="small">{d.usage.map((u) => `${u.month}: ${u.runs.toLocaleString()} runs`).join(' · ')} · {d.types} custom type(s) · {d.checked_total.toLocaleString()} emails checked in the log</p>

      <h4>API keys ({d.keys.filter((k) => !k.revoked).length} active)</h4>
      {d.keys.length === 0 ? <p className="small muted">None.</p> : (
        <table><thead><tr><th>Name</th><th>Starts with</th><th>Last used</th><th></th></tr></thead>
          <tbody>{d.keys.map((k) => <tr key={k.id}><td>{k.name}</td><td className="mono">{k.prefix}…</td><td>{ago(k.last_used)}</td><td>{k.revoked ? 'revoked' : ''}</td></tr>)}</tbody></table>
      )}

      <h4>Actions</h4>
      <div className="row">
        {o.suspended
          ? <button className="btn sm" onClick={() => run(() => adm('POST', `/orgs/${id}/suspend`, { suspended: false }), () => 'Unsuspended.')}>Unsuspend</button>
          : <>
            <input style={{ maxWidth: '16rem' }} placeholder="Reason (kept in the log)" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason" />
            <button className="btn sm ghost" onClick={async () => { if (await ask({ title: `Suspend ${o.name}?`, body: 'Its API keys and sign-ins stop working and its mailboxes are no longer checked. Nothing is deleted. You can undo this at any time.', yes: 'Suspend', no: 'Cancel', danger: true })) run(() => adm('POST', `/orgs/${id}/suspend`, { suspended: true, reason }), () => 'Suspended.') }}>Suspend</button>
          </>}
        <button className="btn sm ghost" onClick={async () => { if (await ask({ title: 'Reset this month\'s runs?', body: 'The run counter for the current month goes back to 0.', yes: 'Reset', no: 'Cancel' })) run(() => adm('POST', `/orgs/${id}/reset-runs`), () => 'Runs reset.') }}>Reset runs this month</button>
        <button className="btn sm ghost" onClick={async () => { if (await ask({ title: 'Revoke every API key?', body: 'All keys of this organization stop working at once. The customer can create new ones.', yes: 'Revoke all', no: 'Cancel', danger: true })) run(() => adm('POST', `/orgs/${id}/revoke-keys`), (r) => `${r.revoked} key(s) revoked.`) }}>Revoke all API keys</button>
        <button className="btn sm danger" onClick={() => setDel(!del)}>Delete organization…</button>
      </div>
      {del && (
        <div className="warn" style={{ marginTop: '.8rem' }}>
          <b>This permanently deletes the organization, its {d.members.length} person(s), {d.mailboxes.length} mailbox(es), watches, log and keys, and cancels its subscription.</b> It cannot be undone.
          <Typed what={`the id ${o.id}`} expect={o.id} label="Delete for good" onGo={() => run(() => adm('DELETE', `/orgs/${id}?confirm=${encodeURIComponent(o.id)}`), (r) => { onClose(); return `Deleted (${r.billing}).` })} />
        </div>
      )}
      {sheet}
    </div>
  )
}

function Orgs() {
  const [rows, setRows] = useState(null)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState('')
  const [err, setErr] = useState('')
  const [more, setMore] = useState(false)
  const load = useCallback((reset = true, search = q) => {
    adm('GET', `/orgs?q=${encodeURIComponent(search)}&limit=50&offset=${reset ? 0 : (rows || []).length}`)
      .then((r) => { setMore(r.orgs.length === 50); setRows(reset ? r.orgs : [...(rows || []), ...r.orgs]) }).catch((e) => setErr(e.message))
  }, [q, rows])             // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(() => load(true, q), 250); return () => clearTimeout(t) }, [q])       // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <input placeholder="Search by organization, person's email or id" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" style={{ maxWidth: '28rem' }} />
      {err && <div className="err">{err}</div>}
      <div className="tbl">
        <table>
          <thead><tr><th>Organization</th><th>Tier</th><th>People</th><th>Mailboxes</th><th>Watches</th><th>Runs</th><th>Last activity</th><th>Created</th></tr></thead>
          <tbody>
            {rows && rows.length === 0 && <tr><td colSpan={8} className="muted">Nothing found.</td></tr>}
            {(rows || []).map((o) => (
              <Fragment key={o.id}>
                <tr className="logrow" onClick={() => setOpen(open === o.id ? '' : o.id)} aria-expanded={open === o.id}>
                  <td><b>{o.name}</b> {o.suspended ? <span className="pill">Suspended</span> : null}<div className="small muted">{o.owner || '–'}</div></td>
                  <td>{TIER[o.plan] || o.plan}{o.subscribed ? <div className="small muted">paying{o.billing_status && o.billing_status !== 'active' ? `, ${o.billing_status}` : ''}</div> : null}</td>
                  <td>{o.members}</td><td>{o.mailboxes}</td><td>{o.watches}</td><td>{o.runs.toLocaleString()}</td><td>{ago(o.last_activity)}</td><td>{day(o.created)}</td>
                </tr>
                {open === o.id && <tr><td colSpan={8}><OrgDetail id={o.id} onChanged={() => load(true)} onClose={() => { setOpen(''); load(true) }} /></td></tr>}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {more && <button className="btn sm ghost" onClick={() => load(false)}>Load more</button>}
    </>
  )
}

function People() {
  const [rows, setRows] = useState(null)
  const [q, setQ] = useState('')
  const [err, setErr] = useState('')
  const [rm, setRm] = useState('')
  const [msg, setMsg] = useState('')
  const load = useCallback((s) => adm('GET', `/users?q=${encodeURIComponent(s)}&limit=100`).then((r) => setRows(r.users)).catch((e) => setErr(e.message)), [])
  useEffect(() => { const t = setTimeout(() => load(q), 250); return () => clearTimeout(t) }, [q, load])
  return (
    <>
      <input placeholder="Search by email or id" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" style={{ maxWidth: '28rem' }} />
      {msg && <div className="note">{msg}</div>}{err && <div className="err">{err}</div>}
      <div className="tbl">
        <table>
          <thead><tr><th>Email</th><th>Organization</th><th>Role</th><th>Signs in with</th><th>Verified</th><th>Joined</th><th></th></tr></thead>
          <tbody>
            {rows && rows.length === 0 && <tr><td colSpan={7} className="muted">Nothing found.</td></tr>}
            {(rows || []).map((u) => (
              <Fragment key={u.id}>
                <tr>
                  <td>{u.email}</td><td>{u.org || '–'}<div className="small muted">{TIER[u.plan] || ''}</div></td><td>{u.role}</td>
                  <td>{[u.github && 'GitHub', u.google && 'Google', u.microsoft && 'Microsoft'].filter(Boolean).join(', ') || 'password (test)'}</td>
                  <td>{u.verified ? 'yes' : 'no'}</td><td>{day(u.created)}</td>
                  <td>{u.role === 'member' && <button className="btn sm ghost" onClick={() => setRm(rm === u.id ? '' : u.id)}>Remove…</button>}</td>
                </tr>
                {rm === u.id && <tr><td colSpan={7}><Typed what={`their email ${u.email}`} expect={u.email} label="Remove person"
                  onGo={() => adm('DELETE', `/users/${u.id}?confirm=${encodeURIComponent(u.email)}`).then(() => { setRm(''); setMsg(`${u.email} removed.`); load(q) }).catch((e) => setErr(e.message))} /></td></tr>}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">To remove an organization's owner, delete the whole organization from the Organizations tab.</p>
    </>
  )
}

function Activity() {
  const [rows, setRows] = useState(null)
  useEffect(() => { adm('GET', '/audit').then((r) => setRows(r.audit)).catch(() => setRows([])) }, [])
  return (
    <div className="tbl">
      <table>
        <thead><tr><th>When</th><th>Admin</th><th>Action</th><th>On</th><th>Details</th></tr></thead>
        <tbody>
          {rows && rows.length === 0 && <tr><td colSpan={5} className="muted">No changes made yet.</td></tr>}
          {(rows || []).map((a, i) => <tr key={i}><td>{new Date(a.at * 1000).toLocaleString()}</td><td>{a.admin}</td><td>{a.action}</td><td className="mono">{a.target}</td><td>{a.detail}</td></tr>)}
        </tbody>
      </table>
    </div>
  )
}

export default function Admin() {
  const [ok, setOk] = useState(undefined)
  const [tab, setTab] = useState('overview')
  useEffect(() => {
    document.title = 'Admin · WatchFox'
    fetch('/web/me', { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : {})).then((m) => setOk(!!m.is_admin)).catch(() => setOk(false))
  }, [])
  if (ok === undefined) return <div className="wrap"><p className="muted">Loading…</p></div>
  if (!ok) return <div className="wrap" style={{ padding: '4rem 0' }}><h1>Not found</h1><p className="muted">There is nothing at this address.</p><a href="/">Go to WatchFox</a></div>
  const TABS = [['overview', 'Overview'], ['orgs', 'Organizations'], ['people', 'People'], ['activity', 'Activity']]
  return (
    <div className="wrap admin">
      <div className="row" style={{ justifyContent: 'space-between', padding: '1.4rem 0' }}>
        <span className="mark"><Fox size={28} /><span>WatchFox</span> <span className="pill">Admin</span></span>
        <a className="btn sm ghost" href="/#/dashboard">Back to the dashboard</a>
      </div>
      <div className="chips" role="tablist">{TABS.map(([id, l]) => <button key={id} role="tab" className="chip" aria-pressed={tab === id} onClick={() => setTab(id)}>{l}</button>)}</div>
      <div style={{ marginTop: '1.4rem' }}>
        {tab === 'overview' && <Overview />}
        {tab === 'orgs' && <Orgs />}
        {tab === 'people' && <People />}
        {tab === 'activity' && <Activity />}
      </div>
    </div>
  )
}
