import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { PlanCards } from './Pricing'
import { useAsk } from '../ask'

const when = (ts) => new Date(ts * 1000).toLocaleDateString([], { month: 'long', day: 'numeric' })

export function Meter({ plan, compact }) {
  if (!plan) return null
  const pct = Math.min(100, Math.round((100 * plan.runs_used) / plan.runs_limit))
  return (
    <div className={`usage ${pct >= 100 ? 'full' : pct >= 80 ? 'warn' : ''}`}>
      <div className="mt"><span>{plan.plan_name}{compact ? '' : ' plan'}</span><span>{plan.runs_used.toLocaleString()} / {plan.runs_limit.toLocaleString()} runs</span></div>
      <div className="ubar"><i style={{ width: `${pct}%` }} /></div>
    </div>
  )
}

function Section({ title, children, sub }) {
  return <section className="setsec"><h2>{title}</h2>{sub && <p className="muted">{sub}</p>}{children}</section>
}

const loadHelcim = () => new Promise((resolve, reject) => {
  if (window.appendHelcimPayIframe) return resolve()
  const el = document.createElement('script')
  el.src = 'https://secure.helcim.app/helcim-pay/services/start.js'
  el.onload = () => resolve()
  el.onerror = () => reject(new Error('Could not load the secure card form. Check your connection or ad blocker and try again.'))
  document.head.appendChild(el)
})

/* Opens Helcim's secure card form and resolves with the server's answer once the card is saved. The card number never touches WatchFox. */
async function runCheckout(body, ask) {
  const co = await api.billingCheckout(body)
  // A new card goes straight to the secure form, which shows the amount itself. A saved card is charged with no form, so that one asks first.
  if (co.mode === 'saved_card' && ask && !(await ask(co))) return null
  if (co.mode === 'saved_card') return api.billingConfirm({ checkoutToken: co.checkoutToken })     // a card is already saved: no form, one step
  await loadHelcim()
  return new Promise((resolve, reject) => {
    const key = `helcim-pay-js-${co.checkoutToken}`
    const done = (fn, v) => { window.removeEventListener('message', on); try { window.removeHelcimPayIframe() } catch {} fn(v) }
    async function on(e) {
      if (!e.data || e.data.eventName !== key) return
      if (e.data.eventStatus === 'SUCCESS') {
        window.removeEventListener('message', on)
        try { const r = await api.billingConfirm({ checkoutToken: co.checkoutToken, eventMessage: e.data.eventMessage }); done(resolve, r) } catch (x) { done(reject, x) }
      } else if (e.data.eventStatus === 'ABORTED') {
        const why = typeof e.data.eventMessage === 'string' ? e.data.eventMessage.replace(/\s+/g, ' ').trim().slice(0, 160) : ''
        done(reject, new Error(`The card was not accepted${why ? ` (${why})` : ''}. Nothing was charged. Check the card number, expiry date and security code, or try another card.`))
      }
      else if (e.data.eventStatus === 'HIDE') done(resolve, null)
    }
    window.addEventListener('message', on)
    window.appendHelcimPayIframe(co.checkoutToken)
  })
}

function PaymentsSection() {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.billingPayments().then((r) => setRows(r.payments)).catch((e) => setErr(e.message)) }, [])
  const day = (v) => (v ? new Date(v + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '')
  return (
    <Section title="Payments and receipts" sub="Your payments, read from our payment service when you open this page. Receipts are made when you ask for them.">
      {err && <div className="err">{err}</div>}
      {rows && rows.length === 0 && <p className="muted small">No payments yet.</p>}
      {rows && rows.length > 0 && (
        <div className="tbl"><table>
          <thead><tr><th>Date</th><th>Amount</th><th>Card</th><th></th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{day(r.date)}</td>
                <td>{r.kind === 'refund' ? '−' : ''}US${r.amount.toFixed(2)} {r.kind === 'refund' && <span className="muted small">refund</span>}</td>
                <td>{r.card ? `${r.brand} ${r.card}` : ''}</td>
                <td><a className="btn sm ghost" href={`/v1/billing/payments/${r.id}/receipt`} download>Download PDF</a></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </Section>
  )
}

function PlanSection({ plan, owner, reload }) {
  const [ask, sheet] = useAsk()
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [bill, setBill] = useState(null)
  const [done, setDone] = useState(null)
  const loadBill = () => api.billing().then(setBill).catch(() => {})
  useEffect(() => { loadBill() }, [plan?.plan])
  const top = useRef(null)
  const run = async (fn) => {
    setBusy(true); setMsg(''); setErr(''); setDone(null)
    try { await fn() } catch (e) { setErr(e.message) }
    setBusy(false); loadBill(); reload()
    setTimeout(() => top.current && top.current.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50)
  }
  const pick = (id, period) => run(async () => {
    if (bill && bill.configured) {
      const name = id === 'tracker' ? 'Tracker' : 'Alpha'
      const r = await runCheckout({ kind: 'subscribe', plan: id, billing: period },
        (co) => ask({ title: `Subscribe to ${name}`, big: `US$${Number(co.amount).toLocaleString()} / ${period === 'annual' ? 'year' : 'month'}`,
          body: `You will be charged now on your ${co.card}. It renews automatically until you cancel, and you can cancel any time in Settings.`, yes: 'Pay and subscribe' }))
      if (r) setDone({ plan: name, amount: r.charged, period: r.billing, next: r.next_billing, currency: r.currency })
    } else {
      setMsg((await api.upgrade({ plan: id, billing: period })).message)
    }
  })
  const updateCard = () => run(async () => { const r = await runCheckout({ kind: 'card' }); if (r) setMsg(r.message) })
  const cancel = () => run(async () => {
    if (!(await ask({ title: 'Cancel your subscription?', body: 'You keep your plan until the end of the period you paid for, then move to the free Kit plan. Nothing more is charged.',
      yes: 'Cancel subscription', no: 'Keep it', danger: true }))) return
    await api.billingCancel()                          // the note above says it, so no second message
  })
  const when = (v) => (v ? new Date(typeof v === 'number' ? v * 1000 : v).toLocaleDateString([], { month: 'long', day: 'numeric', year: 'numeric' }) : '')
  const paid = bill && bill.subscribed
  return (
    <Section title="Plan and usage" sub="One run is one email checked by one watch, caught or not. Your count starts again on the 1st of each month (UTC).">
      <Meter plan={plan} />
      {plan && <p className="small muted">{plan.runs_remaining.toLocaleString()} runs left this month, resets {when(plan.resets_at)}.</p>}
      {bill && bill.past_due && <div className="warn"><b>Your last payment failed.</b> Your plan stays active for a few days while you fix it. <button className="btn sm" disabled={busy} onClick={updateCard}>Update card</button></div>}
      {paid && bill.status === 'cancelling' && <div className="note">Your subscription is cancelled. You keep the {plan?.plan_name} plan until {when(bill.cancel_at)}, then move to Kit.</div>}
      {paid && bill.status !== 'cancelling' && owner && (
        <div className="note">
          Billed {bill.billing === 'annual' ? 'yearly' : 'monthly'}{bill.next_billing ? ` · next payment ${when(bill.next_billing)}` : ''}.
          <div className="row" style={{ marginTop: '.5rem' }}>
            <button className="btn sm ghost" disabled={busy} onClick={updateCard}>Update card</button>
            <button className="btn sm ghost" disabled={busy} onClick={cancel}>Cancel subscription</button>
          </div>
        </div>
      )}
      <div ref={top}>
        {busy && <div className="note">One moment…</div>}
        {done && (
          <div className="receipt">
            <div className="rk">Payment received</div>
            <div className="ra">US${Number(done.amount).toFixed(2)} <span>{done.currency}</span></div>
            <div>You are now on the <b>{done.plan}</b> plan, billed {done.period === 'annual' ? 'yearly' : 'monthly'}.</div>
            {done.next && <div className="small muted">Next payment on {when(done.next)}. Cancel any time below.</div>}
          </div>
        )}
        {msg && <div className="note">{msg}</div>}
        {err && <div className="err">{err}</div>}
      </div>
      {owner ? <PlanCards current={plan?.plan} currentBilling={paid ? bill.billing : undefined} onPick={pick} busy={busy} /> : <p className="muted">Only the organization owner can change the plan.</p>}
      {owner && bill && !bill.configured && <p className="small muted">Online payment is not switched on yet. Choosing a plan sends us a request and we will activate it.</p>}
      {sheet}
      {owner && bill && bill.configured && <p className="small muted">Payments are processed by Helcim on its own secure form. WatchFox never sees or stores your card number. Prices are in US dollars.</p>}
    </Section>
  )
}

function TeamSection({ me }) {
  const [ask, sheet] = useAsk()
  const [t, setT] = useState(null)
  const [email, setEmail] = useState('')
  const [err, setErr] = useState('')
  const [fresh, setFresh] = useState(null)
  const load = () => api.team().then(setT).catch(() => {})
  useEffect(() => { load() }, [])
  const invite = async (e) => {
    e.preventDefault(); setErr(''); setFresh(null)
    try { const r = await api.invite(email); setEmail(''); setFresh(r); load() } catch (x) { setErr(x.message) }
  }
  if (!t) return null
  const count = t.members.length + t.invites.length
  return (
    <Section title="Team" sub={`Everyone here is a developer in the same organization: they share the mailboxes, watches, types and API keys, and can use every feature. ${count} of ${t.limit} seats used.`}>
      <div className="list">
        {t.members.map((m) => (
          <div className="li" key={m.id}>
            <div><div className="t">{m.email}{m.you ? ' (you)' : ''}</div><div className="s">{m.role === 'owner' ? 'Owner' : 'Developer'}{m.verified ? '' : ' · email not confirmed'}</div></div>
            {t.can_manage && m.role !== 'owner' && <button className="btn sm ghost" onClick={async () => { if (await ask({ title: `Remove ${m.email}?`, body: 'Their access and the API keys they made stop working.', yes: 'Remove', no: 'Keep', danger: true })) { await api.removeMember(m.id); load() } }}>Remove</button>}
          </div>
        ))}
        {t.invites.map((i) => (
          <div className="li" key={i.id}>
            <div><div className="t">{i.email}</div><div className="s">Invitation pending · waiting for them to sign in · link valid until {when(i.expires)}</div></div>
            {t.can_manage && (
              <div className="row" style={{ flexShrink: 0 }}>
                <button className="btn sm ghost" onClick={async () => { const r = await api.inviteLink(i.id); setFresh({ email: i.email, link: r.link, emailed: false, again: true }) }}>Get the link</button>
                <button className="btn sm ghost" onClick={async () => { await api.cancelInvite(i.id); load() }}>Cancel</button>
              </div>
            )}
          </div>
        ))}
      </div>
      {t.can_manage ? (
        <form onSubmit={invite} className="row">
          <input style={{ maxWidth: '20rem' }} type="email" placeholder="colleague@yourcompany.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="btn">Invite a developer</button>
        </form>
      ) : <p className="muted">Only the owner can invite or remove people.</p>}
      {err && <div className="err">{err}</div>}
      {fresh && (
        <div className="note">
          <b>Invitation for {fresh.email}.</b>{fresh.emailed ? ' We emailed them the link too, and here it is if you want to send it yourself:' : ' Nothing was emailed. Send them this link (it works for 7 days):'}
          <div className="row" style={{ marginTop: '.5rem' }}>
            <input readOnly value={fresh.link} onFocus={(e) => e.target.select()} style={{ maxWidth: '34rem' }} />
            <button className="btn sm" type="button" onClick={() => { try { navigator.clipboard.writeText(fresh.link) } catch {} }}>Copy link</button>
            <a className="btn sm ghost" href={`mailto:${fresh.email}?subject=${encodeURIComponent('Join our team on WatchFox')}&body=${encodeURIComponent(`I invited you to our team on WatchFox. Open this link and sign in with GitHub, Google or Microsoft:\n\n${fresh.link}\n\nThe link works for 7 days.`)}`}>Email it from my mail app</a>
          </div>
        </div>
      )}
      <p className="small muted">They sign in with GitHub, Google or Microsoft and join this team. A person can belong to only one organization, so invite someone who does not already use WatchFox.</p>
    {sheet}
    </Section>
  )
}

function AccountSection({ me, reload }) {
  const [pwOn, setPwOn] = useState(false)
  useEffect(() => { api.signInOptions().then((r) => setPwOn(!!r.password)).catch(() => {}) }, [])
  const [f, setF] = useState({ current: '', password: '' })
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const change = async (e) => {
    e.preventDefault(); setErr(''); setMsg('')
    try { await api.changePassword(f); setMsg('Password changed.'); setF({ current: '', password: '' }) } catch (x) { setErr(x.message) }
  }
  return (
    <Section title="Your account">
      <p>{me.email}{me.role === 'owner' ? ' · owner' : ' · developer'}</p>
      <p className="small muted">You sign in with GitHub, Google or Microsoft, so there is no password to manage here.</p>
      {pwOn && (
        <form onSubmit={change} style={{ maxWidth: '24rem' }}>
          <label>Current password</label>
          <input type="password" autoComplete="current-password" required value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} />
          <label>New password (at least 10 characters)</label>
          <input type="password" autoComplete="new-password" required minLength={10} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
          {err && <div className="err">{err}</div>}
          {msg && <div className="note">{msg}</div>}
          <button className="btn sm">Change password</button>
        </form>
      )}
    </Section>
  )
}

export default function Settings({ d, user, reload }) {
  const me = d.me || user
  const owner = me.role === 'owner'
  const [name, setName] = useState('')
  const [saved, setSaved] = useState('')
  useEffect(() => { api.org().then((o) => setName(o.name || '')).catch(() => {}) }, [])
  return (
    <>
      <h1>Settings</h1>
      <Section title="Organization" sub="Your account has one organization. Mailboxes, watches, types, keys and logs belong to it, and so does the plan.">
        <div className="row">
          <input style={{ maxWidth: '20rem' }} value={name} disabled={!owner} onChange={(e) => setName(e.target.value)} aria-label="Organization name" />
          {owner && <button className="btn sm" onClick={async () => { await api.renameOrg(name); setSaved('Saved'); setTimeout(() => setSaved(''), 2000) }}>Save name</button>}
          {saved && <span className="small">{saved}</span>}
        </div>
      </Section>
      <PlanSection plan={d.plan} owner={owner} reload={reload} />
      {owner && <PaymentsSection />}
      <TeamSection me={me} />
      <AccountSection me={me} reload={reload} />
    </>
  )
}
