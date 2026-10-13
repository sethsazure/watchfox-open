import { useEffect, useState } from 'react'

/* An in-page confirmation sheet instead of the browser's alert box. ask() resolves true or false. */
export function useAsk() {
  const [q, setQ] = useState(null)
  const ask = (opts) => new Promise((resolve) => setQ({ ...opts, resolve }))
  const close = (v) => { q.resolve(v); setQ(null) }
  useEffect(() => {
    if (!q) return
    const key = (e) => { if (e.key === 'Escape') close(false) }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  })
  const sheet = q && (
    <div className="sheet-back" onClick={() => close(false)}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={q.title} onClick={(e) => e.stopPropagation()}>
        <h3>{q.title}</h3>
        {q.big && <div className="sheet-amt">{q.big}</div>}
        <p>{q.body}</p>
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          {q.no !== null && <button className="btn sm ghost" onClick={() => close(false)}>{q.no || 'Not now'}</button>}
          <button className={`btn sm ${q.danger ? 'danger' : ''}`} autoFocus onClick={() => close(true)}>{q.yes || 'Continue'}</button>
        </div>
      </div>
    </div>
  )
  return [ask, sheet]
}
