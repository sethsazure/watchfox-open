import { useEffect, useState } from 'react'
import { api } from '../api'

const FEATURES = (p) => [
  `${p.runs.toLocaleString()} runs a month`,
  `${p.mailboxes} mailboxes`,
  `${p.watches} watches`,
  `${p.types} custom types`,
  `${p.members} team members`,
  'Signed webhooks, full .eml and attachments',
  '90 days of activity logs, CSV export',
]

const money = (n) => (n === 0 ? 'Free' : `$${Number.isInteger(n) ? n.toLocaleString() : n.toFixed(2)}`)

/* The three plans. `current` highlights the plan an account is on; `onPick` makes the buttons act. */
export function PlanCards({ current, currentBilling, onPick, busy }) {
  const [plans, setPlans] = useState([])
  const [annual, setAnnual] = useState(false)
  useEffect(() => { api.plans().then((r) => setPlans(r.plans)).catch(() => {}) }, [])
  if (!plans.length) return null
  return (
    <>
      <div className="chips" role="group" aria-label="Billing period" style={{ justifyContent: 'center' }}>
        <button className="chip" aria-pressed={!annual} onClick={() => setAnnual(false)}>Monthly</button>
        <button className="chip" aria-pressed={annual} onClick={() => setAnnual(true)}>Annual · 2 months free</button>
      </div>
      <div className="plans">
        {plans.map((p) => {
          const price = annual ? p.annual_monthly_equivalent : p.monthly
          const mine = current === p.id && (p.id === 'kit' || !currentBilling || currentBilling === (annual ? 'annual' : 'monthly'))
          return (
            <div key={p.id} className={`plan ${p.id === 'tracker' ? 'pop' : ''} ${mine ? 'mine' : ''}`}>
              <div className="pbadge">{p.id === 'tracker' && <span className="pill on">Most popular</span>}</div>
              <h3>{p.name}</h3>
              <div className="s">{p.tagline}</div>
              <div className="price"><b>{money(price)}</b>{p.monthly > 0 && <span> / month</span>}</div>
              <div className="s">{p.monthly === 0 ? 'No card needed' : annual ? `$${p.annual.toLocaleString()} billed yearly · save ${p.annual_saving_percent}%` : 'Billed monthly'}</div>
              <ul>{FEATURES(p).map((f) => <li key={f}>{f}</li>)}</ul>
              <div className="pcta">
                {mine ? <span className="pill">Your plan</span>
                  : p.monthly === 0 ? (onPick ? null : <a className="btn ghost" href="#/signup">Start free</a>)
                  : <button className="btn" disabled={busy} onClick={() => (onPick ? onPick(p.id, annual ? 'annual' : 'monthly') : (window.location.hash = '#/signup'))}>{onPick ? `Upgrade to ${p.name}` : `Get ${p.name}`}</button>}
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}

import Faq from '../components/Faq'

export default function Pricing({ user }) {
  return (
    <div className="wrap pricing">
      <span className="eyebrow">Pricing</span>
      <h1>Start free. Pay when you go live.</h1>
      <p className="lead muted">Every plan has every feature. You only pay for more volume and higher limits.</p>
      <PlanCards onPick={user ? () => { window.location.hash = '#/dashboard/settings' } : null} />
      <div className="panel">
        <h3>What is a run?</h3>
        <p>A run is one email checked by one watch, caught or not. So your 50 free runs include emails WatchFox ignored. A watch with several types still uses one run per email. Sandbox emails and test calls count too.</p>
        <p className="small muted">If you run out, new mail waits (nothing is lost) until the 1st (UTC) or until you upgrade. Need more than {''}50,000 runs? <a href="mailto:hello@watchfox.run">Talk to us</a>.</p>
      </div>
      <Faq where="pricing" title="Questions about plans and runs" />
    </div>
  )
}
