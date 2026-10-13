import { useEffect, useState } from 'react'
import Fox from '../components/Fox'
import { api } from '../api'

const MESSAGES = { 'bad-link': 'That link is no longer valid.' }

export default function Auth({ mode, onAuthed }) {
  const signup = mode === 'signup'
  const [opts, setOpts] = useState(null)
  const [f, setF] = useState({ email: '', password: '' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const q = new URLSearchParams(window.location.hash.split('?')[1] || '')
  const urlErr = q.get('error') || MESSAGES[q.get('notice')] || ''
  useEffect(() => { api.signInOptions().then((r) => setOpts({ oauth: r.oauth || [], password: !!r.password })).catch(() => setOpts({ oauth: [], password: false })) }, [])

  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr('')
    try {
      const r = await (signup ? api.signup(f) : api.login(f))
      if (r.dev_link) { try { sessionStorage.setItem('wf-dev-link', r.dev_link) } catch {} }
      await onAuthed(); window.location.hash = '#/dashboard'
    } catch (x) { setErr(x.message) } finally { setBusy(false) }
  }

  return (
    <div className="auth">
      <Fox size={52} />
      <h1>{signup ? 'Get your API key' : 'Welcome back'}</h1>
      <p className="muted">{signup ? 'Sign in with an account you already have. It takes a few seconds, and your first key is one click away.' : 'Sign in to your dashboard.'}</p>
      {urlErr && <div className="err">{urlErr}</div>}
      {opts && (
        <div className="oauth">
          {opts.oauth.map((o) => <a key={o.id} className="btn ghost" href={`/auth/login/${o.id}/start`}>Continue with {o.label}</a>)}
          {opts.oauth.length === 0 && !opts.password && <p className="muted">Sign-in is not set up on this server yet.</p>}
        </div>
      )}
      {opts && opts.oauth.length > 0 && <p className="small muted" style={{ marginTop: '1rem' }}>New here? Continuing creates your account. You start on the free Kit plan: 50 runs a month. By continuing you agree to the <a href="/terms">Terms</a> and the <a href="/privacy">Privacy policy</a>.</p>}
      {opts && opts.password && (
        <>
          <div className="or"><span>or</span></div>
          <form onSubmit={submit}>
            <label htmlFor="e">Email</label>
            <input id="e" type="email" autoComplete="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
            <label htmlFor="p">Password</label>
            <input id="p" type="password" autoComplete={signup ? 'new-password' : 'current-password'} required minLength={signup ? 10 : 1} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
            {!signup && <p className="small" style={{ margin: '.4rem 0 0' }}><a href="#/forgot">Forgot your password?</a></p>}
            {err && <div className="err">{err}</div>}
            <button className="btn" disabled={busy}>{busy ? 'One moment…' : signup ? 'Create account' : 'Sign in'}</button>
          </form>
        </>
      )}
    </div>
  )
}
