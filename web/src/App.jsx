import Theme from './Theme.jsx'
import { useCallback, useEffect, useState } from 'react'
import Home from './pages/Home'
import Docs from './pages/Docs'
import Auth from './pages/Auth'
import Pricing from './pages/Pricing'
import Recover from './pages/Recover'
import { Privacy, Terms } from './pages/Legal'
import Dashboard from './pages/Dashboard'
import Connect from './pages/Connect'
import Admin from './pages/Admin'
import Fox from './components/Fox'
import { api } from './api'

const PATHS = ['privacy', 'terms']      // these two also work as real addresses (/privacy, /terms), so crawlers and plain links find them
const route = () => {
  const h = window.location.hash.replace(/^#\/?/, '').split('/')[0].split('?')[0]
  const p = window.location.pathname.replace(/^\/|\/$/g, '')
  if (window.location.hash) return h || 'home'          // an explicit #/ means the home page, even when the address is /privacy
  return PATHS.includes(p) ? p : 'home'
}

// The hosted connect page has no site chrome and is reached by path; the rest of the site uses hash routes.
export default function App() {
  const p = window.location.pathname
  if (p.startsWith('/admin')) return <Admin />      // the admin console is its own page, outside the site and the dashboard
  return p.startsWith('/connect') ? <Connect /> : <Site />
}

function Site() {
  const [page, setPage] = useState(route())
  const [user, setUser] = useState(undefined)   // undefined = checking, null = signed out

  const check = useCallback(async () => { try { setUser(await api.me()) } catch { setUser(null) } }, [])
  useEffect(() => { check() }, [check])
  useEffect(() => {
    const on = () => { setPage(route()); if (!window.location.hash.startsWith('#/docs/')) window.scrollTo(0, 0) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const out = async () => { await api.logout(); setUser(null); window.location.hash = '#/' }

  useEffect(() => { if (user && (page === 'login' || page === 'signup')) window.location.hash = '#/dashboard' }, [user, page])   // already signed in
  useEffect(() => {          // each page has its own title in the browser tab and in search results
    const names = { docs: 'API documentation', pricing: 'Pricing', login: 'Sign in', signup: 'Get an API key', privacy: 'Privacy policy', terms: 'Terms of service', dashboard: 'Dashboard' }
    document.title = names[page] ? `${names[page]} · WatchFox` : 'WatchFox'
  }, [page])

  let body
  if (page === 'docs') body = <Docs />
  else if ((page === 'login' || page === 'signup') && user) body = null
  else if (page === 'login' || page === 'signup') body = <Auth mode={page} onAuthed={check} />
  else if (page === 'pricing') body = <Pricing user={user} />
  else if (page === 'privacy') body = <Privacy />
  else if (page === 'terms') body = <Terms />
  else if (['forgot', 'reset', 'join'].includes(page)) body = <Recover mode={page} onAuthed={check} />
  else if (page === 'dashboard') body = user === undefined ? null : user && user.suspended ? (
    <div className="wrap" style={{ padding: '4rem 0' }}><h1>This account is suspended</h1><p className="lead muted">Its API keys and checks are paused. Nothing has been deleted. Please write to <a href="mailto:hello@watchfox.run">hello@watchfox.run</a> and we will help.</p><button className="btn ghost" onClick={out}>Sign out</button></div>
  ) : user ? <Dashboard user={user} onOut={out} /> : <Auth mode="login" onAuthed={check} />
  else body = <Home />

  return (
    <>
      <div className="nav">
        <div className="wrap">
          <a className="mark" href="#/"><Fox size={30} /><span>WatchFox</span></a>
          <nav>
            <Theme />
            <a href="#/pricing" aria-current={page === 'pricing' ? 'page' : undefined}>Pricing</a>
            <a href="#/docs" aria-current={page === 'docs' ? 'page' : undefined}>Docs</a>
            {user ? <a className="btn sm" href="#/dashboard">Dashboard</a> : (
              <>
                <a href="#/login">Sign in</a>
                <a className="btn sm" href="#/signup">Get API key</a>
              </>
            )}
          </nav>
        </div>
      </div>
      <main>{body}</main>
      {page !== 'dashboard' && (
        <footer className="rule">
          <div className="wrap">
            <span className="mark" style={{ fontSize: '1rem' }}><Fox size={22} /> WatchFox</span>
            <span>Sharp eyes. Quiet paws.</span>
            <nav><a href="#/pricing">Pricing</a><a href="#/docs">Docs</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="#/signup">Get API key</a></nav>
          </div>
        </footer>
      )}
    </>
  )
}
