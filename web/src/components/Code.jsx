import { useState } from 'react'
import { BASE } from '../api'

// A code block. `tabs={{ curl: '...', python: '...' }}` for several languages, or children for one.
// The text {{BASE}} is replaced with this site's own address, so examples work as pasted.
export default function Code({ tabs, children, label }) {
  const keys = tabs ? Object.keys(tabs) : []
  const [k, setK] = useState(keys[0])
  const [done, setDone] = useState(false)
  const raw = tabs ? tabs[k] : String(children).replace(/^\n/, '').replace(/\s+$/, '')
  const text = raw.replaceAll('{{BASE}}', BASE)
  const copy = async () => { try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200) } catch {} }
  return (
    <figure className="code" style={{ margin: '1rem 0' }}>
      <div className="bar">
        <span className="tabs">
          {keys.length > 0 ? keys.map((x) => <button key={x} aria-pressed={x === k} onClick={() => setK(x)}>{x}</button>) : <span className="lbl">{label || ''}</span>}
        </span>
        <button onClick={copy}>{done ? 'Copied' : 'Copy'}</button>
      </div>
      <pre><code>{text}</code></pre>
    </figure>
  )
}
