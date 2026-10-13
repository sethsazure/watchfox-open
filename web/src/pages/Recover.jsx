import { useEffect, useState } from 'react'
import Fox from '../components/Fox'
import { api } from '../api'

const PROVIDER = { github: 'GitHub', google: 'Google', microsoft: 'Microsoft' }
const query = () => new URLSearchParams((window.location.hash.split('?')[1] || ''))

function Shell({ title, sub, children }) {
  return (
    <div className="auth">
      <Fox size={52} />
      <h1>{title}</h1>
      {sub && <p className="muted">{sub}</p>}
      {children}
    </div>
  )
}

export default function Recover({ mode, onAuthed }) {
  const [email, setEmail] = useState('')
  const [pw, setPw] = useState('')
  const [info, setInfo] = useState(null)
  const [done, setDone] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const token = query().get('token') || ''

  useEffect(() => {
    if (mode === 'join') api.inviteInfo(token).then(setInfo).catch((e) => setErr(e.message))
  }, [mode, token])

  const run = async (fn) => { setBusy(true); setErr(''); try { await fn() } catch (e) { setErr(e.message) } setBusy(false) }

  if (mode === 'forgot') {
    return (
      <Shell title="Forgot your password?" sub="Enter your email and we will send a link to choose a new one.">
        {done ? (
          <>
            <p>{done.message}</p>
            {done.dev_link && <p className="small"><b>Local install, no mail server:</b> <a href={done.dev_link}>open the reset link</a></p>}
          </>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); run(async () => setDone(await api.forgot(email))) }}>
            <label htmlFor="e">Email</label>
            <input id="e" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            {err && <div className="err">{err}</div>}
            <button className="btn" disabled={busy}>{busy ? 'One moment…' : 'Send the link'}</button>
          </form>
        )}
        <p className="small muted" style={{ marginTop: '1.5rem' }}><a href="#/login">Back to sign in</a></p>
      </Shell>
    )
  }

  if (mode === 'reset') {
    return (
      <Shell title="Choose a new password">
        <form onSubmit={(e) => { e.preventDefault(); run(async () => { await api.reset({ token, password: pw }); await onAuthed(); window.location.hash = '#/dashboard' }) }}>
          <label htmlFor="p">New password</label>
          <input id="p" type="password" autoComplete="new-password" required minLength={10} value={pw} onChange={(e) => setPw(e.target.value)} />
          <p className="small muted">At least 10 characters.</p>
          {err && <div className="err">{err} <a href="#/forgot">Ask for a new link</a></div>}
          <button className="btn" disabled={busy}>{busy ? 'One moment…' : 'Save and sign in'}</button>
        </form>
      </Shell>
    )
  }

  return (
    <Shell title={info ? `Join ${info.org}` : 'Join your team'} sub={info ? `You were invited as a developer. Sign in with the account you want to use; it keeps working with this team.` : ''}>
      {err && !info && <div className="err">{err}</div>}
      {info && (
        <>
          <div className="oauth">
            {(info.oauth || []).map((id) => <a key={id} className="btn ghost" href={`/auth/login/${id}/start?invite=${encodeURIComponent(token)}`}>Continue with {PROVIDER[id] || id}</a>)}
          </div>
          <p className="small muted">You will share the organization's mailboxes, watches and keys. A person can belong to only one organization, so use an account that is not already set up with WatchFox.</p>
          {info.password && (
            <form onSubmit={(e) => { e.preventDefault(); run(async () => { await api.join({ token, password: pw }); await onAuthed(); window.location.hash = '#/dashboard' }) }}>
              <label htmlFor="e">Email</label>
              <input id="e" value={info.email} disabled />
              <label htmlFor="p">Password</label>
              <input id="p" type="password" autoComplete="new-password" required minLength={10} value={pw} onChange={(e) => setPw(e.target.value)} />
              {err && <div className="err">{err}</div>}
              <button className="btn" disabled={busy}>{busy ? 'One moment…' : 'Join with a password'}</button>
            </form>
          )}
        </>
      )}
    </Shell>
  )
}
