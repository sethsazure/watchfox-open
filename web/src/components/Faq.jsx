import faq from '../../public/faq.json'

const GROUPS = ['Runs and billing', 'Catching and accuracy', 'Connecting mailboxes', 'Webhooks and the API', 'Privacy and your data', 'Account and team']

// [label](address) in an answer becomes a link; everything else is plain text
function Text({ text }) {
  const parts = text.split(/(\[[^\]]+\]\([^)]+\))/g)
  return parts.map((p, i) => {
    const m = p.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    return m ? <a key={i} href={m[2]}>{m[1]}</a> : p
  })
}

/* A list of common questions. `where` picks the ones for this page (home, pricing or docs); `grouped` adds headings (documentation). */
export default function Faq({ where, grouped = false, title }) {
  const items = faq.filter((f) => f.where.includes(where))
  const one = (f) => (
    <details key={f.id} className="faqitem" id={`faq-${f.id}`}>
      <summary>{f.q}</summary>
      <p><Text text={f.a} /></p>
    </details>
  )
  return (
    <div className="faq">
      {title && <h2>{title}</h2>}
      {grouped
        ? GROUPS.filter((g) => items.some((f) => f.group === g)).map((g) => <div key={g}><h3>{g}</h3>{items.filter((f) => f.group === g).map(one)}</div>)
        : items.map(one)}
    </div>
  )
}
