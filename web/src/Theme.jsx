import { useEffect, useState } from 'react'

const ORDER = ['auto', 'light', 'dark']
const LABEL = { auto: 'Auto', light: 'Light', dark: 'Dark' }

function read() { try { const t = localStorage.getItem('wf-theme'); return ORDER.includes(t) ? t : 'auto' } catch { return 'auto' } }

export function applyTheme(t) {
  const el = document.documentElement
  if (t === 'auto') el.removeAttribute('data-theme'); else el.setAttribute('data-theme', t)
}

/* Auto follows the device. Light and Dark override it. The choice is remembered in this browser. */
export default function Theme() {
  const [t, setT] = useState(read)
  useEffect(() => { applyTheme(t) }, [t])
  const next = () => {
    const n = ORDER[(ORDER.indexOf(t) + 1) % ORDER.length]
    try { localStorage.setItem('wf-theme', n) } catch {}
    setT(n)
  }
  const icon = t === 'dark' ? '☾' : t === 'light' ? '☀' : '◐'
  return <button className="themebtn" onClick={next} title={`Theme: ${LABEL[t]}. Click to change (Auto, Light, Dark).`} aria-label={`Theme: ${LABEL[t]}`}><span aria-hidden="true">{icon}</span><span className="tl"> {LABEL[t]}</span></button>
}
