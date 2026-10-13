import { useEffect, useRef, useState } from 'react'

// A decorative preview: sample emails arrive, get scanned, and either light up or fade. Not live data.
const MAIL = [
  { from: 'Priya Nair', sub: 'Pricing for document automation', hit: 'Enquiry' },
  { from: 'Acme Supplies', sub: 'Invoice INV-2291 for October', hit: 'Invoice' },
  { from: 'Weekly Digest', sub: 'Your product updates this week', hit: null },
  { from: 'Mark Lee', sub: 'Cannot export reports since the update', hit: 'Support' },
  { from: 'Gulf Build', sub: 'RFQ-2026-118: Document management', hit: 'Enquiry' },
  { from: 'LinkedIn', sub: 'You appeared in 9 searches', hit: null },
  { from: 'Northwind', sub: 'Purchase order PO-5521', hit: 'Order' },
  { from: 'Jenny Wu', sub: 'Re: Proposal follow-up', hit: 'Enquiry' },
  { from: 'Sarah', sub: 'Re: Friday standup', hit: null },
  { from: 'Anna K.', sub: 'Application: Field technician', hit: 'Applicant' },
]

export default function InboxLive() {
  const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const [rows, setRows] = useState(() => (reduce ? MAIL.slice(0, 5).map((m, i) => ({ ...m, id: i, state: m.hit ? 'hit' : 'miss' })) : []))
  const n = useRef(0)

  useEffect(() => {
    if (reduce) return
    const timers = []
    const add = () => {
      const m = MAIL[n.current % MAIL.length]
      const id = n.current++
      setRows((r) => [{ ...m, id, state: 'scan' }, ...r].slice(0, 5))
      timers.push(setTimeout(() => setRows((r) => r.map((x) => (x.id === id ? { ...x, state: m.hit ? 'hit' : 'miss' } : x))), 1000))
    }
    add()
    const t = setInterval(add, 2100)
    return () => { clearInterval(t); timers.forEach(clearTimeout) }
  }, [reduce])

  return (
    <div className="win" aria-hidden="true">
      <div className="top"><span className="dots"><i /><i /><i /></span><span>Inbox · watching</span></div>
      <div className="mails">
        {rows.map((r) => (
          <div key={r.id} className={`mail ${r.state}`}>
            <span className="av">{r.from[0]}</span>
            <span><b>{r.sub}</b><small>{r.from}</small></span>
            <span className="tag">{r.state === 'scan' ? 'Reading' : r.hit ? `✓ ${r.hit}` : 'Ignored'}</span>
          </div>
        ))}
      </div>
      <div className="foot">Preview with sample emails</div>
    </div>
  )
}
