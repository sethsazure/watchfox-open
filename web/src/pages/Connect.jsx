import { useEffect, useMemo, useState } from 'react'
import Fox from '../components/Fox'
import { GuideImage, Step1Art, Step2Art, Step3Art } from '../components/GuideArt'

const token = () => new URLSearchParams(window.location.search).get('t') || ''

async function post(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || 'Something went wrong. Please try again.')
  return d
}

function Frame({ app, brand = {}, children }) {
  // the colour was checked by the server as #RRGGBB; it only recolours the buttons and links on this page
  const style = brand.color ? { '--accent': brand.color } : undefined
  return (
    <div className={`conn ${brand.color ? 'branded' : ''}`} style={style}>
      <div className="conn-top">
        {brand.logo
          ? <span className="mark">{/* eslint-disable-next-line jsx-a11y/alt-text */}<img className="conn-logo" src={brand.logo} alt={app || ''} referrerPolicy="no-referrer" /></span>
          : <span className="mark"><Fox size={26} /><span>WatchFox</span></span>}
        <span className="small muted">{brand.logo ? (app || 'Secure mailbox connection') : app ? `Connecting for ${app}` : 'Secure mailbox connection'}</span>
      </div>
      {app && <p className="small conn-notice">You are giving <b>{app}</b> read-only access to this mailbox through WatchFox. Only continue if you trust {app}. You can disconnect at any time.</p>}
      <div className="conn-body">{children}</div>
      <p className="small muted conn-foot">
        Read-only. You can disconnect at any time.{!brand.hide && ' Powered by WatchFox.'}
        {brand.support && <> Need help? <a href={`mailto:${brand.support}`}>{brand.support}</a></>}
      </p>
    </div>
  )
}

const TRUST = ['Reads new emails only', 'Never sends, moves or deletes mail', 'Disconnect at any time']

function Done({ app, back, email }) {
  useEffect(() => { if (back) { const t = setTimeout(() => { window.location.href = back }, 1800); return () => clearTimeout(t) } }, [back])
  return (
    <div className="center">
      <div className="bigtick" aria-hidden="true">✓</div>
      <h1>Connected</h1>
      <p className="lead" style={{ margin: '0 auto 1.4rem' }}>{email ? <><b>{email}</b> is connected.</> : 'Your mailbox is connected.'}{back ? ` Taking you back to ${app || 'the app'}…` : ' You can close this tab.'}</p>
      {back && <a className="btn" href={back}>Continue</a>}
    </div>
  )
}

const stepsFor = (name) => [
  { n: 1, title: 'Make sure 2-Step Verification is on', Art: Step1Art,
    text: 'Google only offers app passwords to accounts that use 2-Step Verification. If it already says “On”, move to the next step.',
    action: ['Open Google security settings', 'https://myaccount.google.com/security'] },
  { n: 2, title: 'Create an app password', Art: Step2Art,
    text: `Type ${name} as the name and press Create. This is a separate password just for ${name}. It is not your usual Google password.`,
    action: ['Open App passwords', 'https://myaccount.google.com/apppasswords'] },
  { n: 3, title: 'Copy the 16 letters Google shows', Art: Step3Art,
    text: 'Google shows the password once. Select it and copy it. You can remove it later on the same page if you want to stop sharing.' },
]

function Gmail({ session, t, finish }) {
  const [step, setStep] = useState(1)
  const [email, setEmail] = useState(session.reconnect_email || '')
  const [pw, setPw] = useState('')
  const [show, setShow] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [help, setHelp] = useState(false)
  const cur = stepsFor(session.app_name || 'WatchFox')[step - 1]

  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr('')
    try { finish(await post('/v1/connect/gmail', { t, email, app_password: pw })) } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }

  return (
    <>
      <h1>{session.reconnect_email ? `Reconnect ${session.reconnect_email}` : 'Connect your Gmail'}</h1>
      <p className="lead">It takes about a minute. You will create a one-off password for {session.app_name || 'WatchFox'} in your Google account and paste it here.</p>
      <ul className="trust">{TRUST.map((x) => <li key={x}>{x}</li>)}</ul>

      <div className="progress" aria-label={`Step ${step} of 4`}>
        {[1, 2, 3, 4].map((n) => <button key={n} className={n === step ? 'on' : n < step ? 'done' : ''} onClick={() => n < step && setStep(n)} aria-current={n === step ? 'step' : undefined}>{n < step ? '✓' : n}</button>)}
      </div>

      {step < 4 ? (
        <div className="stepcard">
          <div className="stepart">{<GuideImage n={cur.n} Art={cur.Art} />}<p className="small muted" style={{ marginTop: '.5rem' }}>Illustration. Google's page may look slightly different.</p></div>
          <div>
            <span className="eyebrow">Step {step} of 4</span>
            <h2>{cur.title}</h2>
            <p>{cur.text}</p>
            {cur.action && <p><a className="btn ghost" href={cur.action[1]} target="_blank" rel="noreferrer">{cur.action[0]} ↗</a></p>}
            <div className="row" style={{ marginTop: '1.4rem' }}>
              {step > 1 && <button className="btn ghost" onClick={() => setStep(step - 1)}>Back</button>}
              <button className="btn" onClick={() => setStep(step + 1)}>{step === 3 ? 'I have copied it' : 'Done, next'} →</button>
            </div>
            <button className="linkbtn" onClick={() => setHelp(!help)}>I don't see “App passwords”</button>
            {help && (
              <div className="note small">
                <b>Common reasons:</b>
                <ul>
                  <li>2-Step Verification is off. Turn it on in step 1 first.</li>
                  <li>The account only uses security keys for 2-Step Verification, or has Google's Advanced Protection.</li>
                  <li>It is a work or school account. An administrator may have switched app passwords off. Ask your IT team, or use another mailbox.</li>
                </ul>
              </div>
            )}
          </div>
        </div>
      ) : (
        <form className="stepcard single" onSubmit={submit}>
          <div>
            <span className="eyebrow">Step 4 of 4</span>
            <h2>Paste it here</h2>
            <label htmlFor="em">Your Gmail address</label>
            <input id="em" type="email" required autoComplete="email" value={email} readOnly={!!session.reconnect_email} onChange={(e) => setEmail(e.target.value)} placeholder="name@gmail.com" />
            <label htmlFor="pw">App password (16 letters)</label>
            <div className="pwrow">
              <input id="pw" type={show ? 'text' : 'password'} required autoComplete="off" spellCheck="false" className="mono" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="abcd efgh ijkl mnop" />
              <button type="button" className="btn ghost sm" onClick={() => setShow(!show)}>{show ? 'Hide' : 'Show'}</button>
            </div>
            <p className="small muted">Spaces don't matter. We sign in once to check it works, then keep it encrypted.</p>
            {err && <div className="err" role="alert">{err}</div>}
            <div className="row" style={{ marginTop: '1.2rem' }}>
              <button type="button" className="btn ghost" onClick={() => setStep(3)}>Back</button>
              <button className="btn" disabled={busy || !email || !pw}>{busy ? 'Checking with Google…' : 'Connect Gmail'}</button>
            </div>
            {err && <p className="small muted">Still stuck? Create a fresh app password in <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">Google's App passwords page</a> and paste the new one.</p>}
          </div>
        </form>
      )}
    </>
  )
}

function Microsoft({ session, t }) {
  const [copied, setCopied] = useState(false)
  return (
    <>
      <h1>{session.reconnect_email ? `Reconnect ${session.reconnect_email}` : 'Connect your Microsoft 365 mailbox'}</h1>
      <p className="lead">You will sign in on Microsoft's own page and approve read-only access. We never see your password.</p>
      <ul className="trust">{TRUST.map((x) => <li key={x}>{x}</li>)}</ul>
      <div className="stepcard single">
        <div>
          <h2>What happens next</h2>
          <ol>
            <li>Microsoft's sign-in page opens. Use the account you want to connect.</li>
            <li>Microsoft asks whether WatchFox may <b>read your mail</b>. Choose Accept.</li>
            <li>You come straight back here.</li>
          </ol>
          <p><a className="btn" href={`/auth/m365/start?t=${encodeURIComponent(t)}`}>Continue with Microsoft →</a></p>
          {session.admin_consent_url && (
            <details className="note small">
              <summary>Microsoft says “Need admin approval”</summary>
              <p>Some organisations only let an administrator approve new apps. Send this link to your IT admin. They approve once for the whole organisation, then you can connect.</p>
              <div className="pwrow"><input readOnly value={session.admin_consent_url} onFocus={(e) => e.target.select()} />
                <button className="btn ghost sm" onClick={async () => { try { await navigator.clipboard.writeText(session.admin_consent_url); setCopied(true) } catch {} }}>{copied ? 'Copied' : 'Copy'}</button></div>
            </details>
          )}
        </div>
      </div>
    </>
  )
}

export default function Connect() {
  const t = useMemo(token, [])
  const [session, setSession] = useState(null)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const done = window.location.pathname.startsWith('/connect/done')
  const email = new URLSearchParams(window.location.search).get('email')

  useEffect(() => {
    if (done) return
    fetch(`/v1/connect/session?t=${encodeURIComponent(t)}`).then(async (r) => {
      const d = await r.json().catch(() => ({}))
      r.ok ? setSession(d) : setError(d.detail || 'This link is not valid.')
    }).catch(() => setError('Could not reach WatchFox. Check your connection and try again.'))
  }, [t, done])

  if (done) return <Frame><Done email={email} /></Frame>
  if (error) return <Frame><div className="center"><h1>This link has expired</h1><p className="lead" style={{ margin: '0 auto' }}>{error} Go back to where you started and choose Connect again.</p></div></Frame>
  if (!session) return <Frame><p className="muted">Loading…</p></Frame>
  const app = session.app_name
  return (
    <Frame app={app} brand={session.brand || {}}>
      {result ? <Done app={app} back={result.redirect} email={result.email} />
        : session.provider === 'm365' ? <Microsoft session={session} t={t} /> : <Gmail session={session} t={t} finish={setResult} />}
    </Frame>
  )
}
