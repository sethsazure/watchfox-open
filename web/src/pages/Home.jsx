import { useEffect, useState } from 'react'
import Code from '../components/Code'
import Fox from '../components/Fox'
import InboxLive from '../components/InboxLive'
import Demo from '../components/Demo'
import Faq from '../components/Faq'
import { api } from '../api'

const WORDS = ['Enquiries', 'Invoices', 'Support tickets', 'Purchase orders', 'Job applications', 'Claims', 'Anything you define']

const FEATURES = [
  ['01', 'Quiet paws', 'WatchFox can read mail. It can never send, move, delete or mark anything. Access is read-only by design.'],
  ['02', 'Signed webhooks', 'Every delivery is signed so you can prove it came from us, and retried with backoff if your server is down.'],
  ['03', 'Starts from now', 'A watch looks at mail that arrives after you create it. Your back catalogue is left alone unless you ask for it.'],
  ['04', 'Define your own', 'Anything you can say as a yes/no question becomes a watch. One sentence. No training, no prompts to tune.'],
  ['05', 'Nothing to host', 'No models, servers or queues on your side. Call the API from any language and get JSON back.'],
  ['06', 'Minimal storage', 'We keep only the subject, sender and a short snippet. Full emails and attachments are never stored: when you ask, we fetch them from the mailbox and keep no copy.'],
]

const ROAD = [
  ['Email', 'Gmail, Microsoft 365 and IMAP mailboxes.', 'Live now', true],
  ['Folders', 'A small agent you install next to the folder.', 'Next'],
  ['FTP', 'Drop zones watched by the same agent.', 'Next'],
  ['Databases', 'New rows and records as they appear.', 'Planned'],
  ['Your own API', 'Send any document, get a verdict.', 'Planned'],
]

const go = (id) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })

export default function Home() {
  const [presets, setPresets] = useState([])
  useEffect(() => { api.presets().then(setPresets).catch(() => {}) }, [])

  return (
    <>
      <header className="hero">
        <div className="wrap grid">
          <div>
            <span className="eyebrow"><i /> Email watch · live now</span>
            <h1 className="display">Know the moment the right email lands.</h1>
            <p className="lead">
              WatchFox reads every new email in a mailbox and calls your app when it finds an enquiry, an invoice, a support
              ticket, or anything else you define. One API. Nothing to train, nothing to host.
            </p>
            <div className="row">
              <a className="btn" href="#/signup">Get your API key →</a>
              <a className="btn ghost" href="#/docs">Read the docs</a>
            </div>
            <div className="proof"><span>Read-only access</span><span>Signed webhooks</span><span>Sandbox to test</span><span>50 free runs a month</span></div>
          </div>
          <InboxLive />
        </div>
      </header>

      <div className="wrap catches">
        <span className="eyebrow">Ready to catch</span>
        <div className="chips">{WORDS.map((w) => <span key={w} className="chip static">{w}</span>)}</div>
      </div>

      <section id="how">
        <div className="wrap">
          <div className="head"><span className="eyebrow">How it works</span><h2>Three calls from first request to first catch.</h2></div>
          <div className="steps">
            <div className="step">
              <span className="n">01</span><h3>Connect a mailbox</h3>
              <p>Send your customer to a hosted page: a Microsoft sign-in, or a short guided Gmail step. Or connect an IMAP mailbox with one request.</p>
              <pre className="plain">POST /v1/connect-links</pre>
            </div>
            <div className="step">
              <span className="n">02</span><h3>Say what to catch</h3>
              <p>Pick from the built-in catches or describe your own in one sentence. Add the webhook URL to call.</p>
              <pre className="plain">POST /v1/watches</pre>
            </div>
            <div className="step">
              <span className="n">03</span><h3>Get the webhook</h3>
              <p>Each matching email arrives at your server as signed JSON: sender, subject, snippet, and what it matched.</p>
              <pre className="plain">POST your-app.com/hook</pre>
            </div>
          </div>
        </div>
      </section>

      <section id="demo" style={{ paddingTop: 0 }}>
        <div className="wrap"><Demo /></div>
      </section>

      <section id="developers" className="rule">
        <div className="wrap grid2">
          <div>
            <span className="eyebrow">For developers</span>
            <h2 style={{ margin: '1.2rem 0 1rem' }}>It is just JSON over HTTPS.</h2>
            <p className="lead">Create a watch, point it at a webhook, and you are done. Use the sandbox mailbox to test everything without connecting real mail.</p>
            <ul className="ticks">
              <li><span><b>REST and JSON.</b> Works from any language, no SDK to install.</span></li>
              <li><span><b>Signed and retried.</b> HMAC signature on every delivery, four attempts with backoff.</span></li>
              <li><span><b>The whole email, if you want it.</b> Add the full text, the <code>.eml</code> and every attachment to the webhook, or download them when you want. We keep no copy.</span></li>
              <li><span><b>Test mode.</b> A sandbox mailbox and a “send test webhook” call, so you can wire it up before any real mail is involved.</span></li>
            </ul>
            <p style={{ marginTop: '1.6rem' }}><a className="btn ghost" href="#/docs">Open the API docs</a></p>
          </div>
          <div>
            <Code tabs={{
              curl: `curl -X POST {{BASE}}/v1/watches \\
  -H "Authorization: Bearer $WATCHFOX_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "connection_id": "cn_8f2a1c04b7e9",
    "what": ["enquiry", "invoice"],
    "webhook_url": "https://yourapp.com/hooks/watchfox"
  }'`,
              python: `import requests

watch = requests.post(
    "{{BASE}}/v1/watches",
    headers={"Authorization": f"Bearer {WATCHFOX_KEY}"},
    json={
        "connection_id": "cn_8f2a1c04b7e9",
        "what": ["enquiry", "invoice"],
        "webhook_url": "https://yourapp.com/hooks/watchfox",
    },
).json()`,
              node: `const watch = await fetch("{{BASE}}/v1/watches", {
  method: "POST",
  headers: {
    Authorization: \`Bearer \${process.env.WATCHFOX_KEY}\`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    connection_id: "cn_8f2a1c04b7e9",
    what: ["enquiry", "invoice"],
    webhook_url: "https://yourapp.com/hooks/watchfox",
  }),
}).then((r) => r.json());`,
            }} />
            <Code label="your webhook receives">{`{
  "type": "email.matched",
  "watch_id": "wt_3f9a1c04b7e2",
  "email": {
    "subject": "RFQ-2026-118: Document management",
    "from": "procurement@gulfbuild.example",
    "snippet": "Please quote 40 licences with implementation…"
  },
  "matches": [{ "what": "enquiry", "label": "Enquiries", "score": 0.64 }]
}`}</Code>
          </div>
        </div>
      </section>

      <section className="rule" id="saas">
        <div className="wrap">
          <div className="head">
            <span className="eyebrow">For SaaS products</span>
            <h2>Your customers connect without leaving your flow.</h2>
            <p className="lead">Add “Connect your mailbox” to your product with one API call. Your customer connects on a short page with your name, logo and colours, then comes straight back to you.</p>
          </div>
          <div className="flow">
            <div className="fl"><span className="n">1</span><h3>Your app</h3><div className="mini"><b>Settings</b><i /><i /><span className="mbtn">Connect your mailbox</span></div><p>They click one button. Your backend creates the link.</p></div>
            <div className="arr" aria-hidden="true">→</div>
            <div className="fl"><span className="n">2</span><h3>A guided page</h3><div className="mini"><b>Connecting for YourApp</b><i /><i /><span className="mbtn">Paste your app password</span></div><p>Microsoft sign-in, or a four-step Gmail guide with pictures. Checked on the spot.</p></div>
            <div className="arr" aria-hidden="true">→</div>
            <div className="fl"><span className="n">3</span><h3>Back in your app</h3><div className="mini"><b>Settings</b><i /><i /><span className="mbtn on">✓ Connected</span></div><p>They land on your page with their mailbox id and your own <code>state</code>.</p></div>
          </div>
          <ul className="ticks" style={{ maxWidth: '44rem' }}>
            <li><span><b>Reconnect alerts.</b> If a sign-in expires or is revoked, you get a webhook and send the person back to the same page. Nothing else changes.</span></li>
            <li><span><b>Your data in every event.</b> Attach your customer id as metadata and it comes back in every webhook.</span></li>
          </ul>
        </div>
      </section>

      <section className="rule">
        <div className="wrap">
          <div className="panel" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '1rem' }}>
            <div><b>Start free with 50 runs a month.</b><div className="small muted">A run is one email checked, caught or not. Move to Tracker (5,000 runs) or Alpha (50,000 runs) when you go live.</div></div>
            <a className="btn ghost" href="#/pricing">See plans</a>
          </div>
        </div>
      </section>

      <section className="rule">
        <div className="wrap">
          <div className="head"><span className="eyebrow">What it catches</span><h2>Six catches built in. Anything else in a sentence.</h2></div>
          <div className="catch">
            {presets.map((p) => (
              <div className="cc" key={p.id}>
                <span className="badge">{p.maturity === 'tuned' ? 'Tuned' : 'Starter'}</span>
                <h3>{p.label}</h3><p>{p.description}</p>
              </div>
            ))}
            <div className="cc" style={{ borderStyle: 'dashed' }}>
              <span className="badge">Yours</span><h3>Describe it</h3>
              <p>“Is this an urgent request for a repair?” becomes a watch. Send the question with your request.</p>
            </div>
          </div>
          <p className="small muted" style={{ marginTop: '1.2rem' }}>Tuned means checked against real emails. Starter means sensible settings that are not yet tuned on real data. <a href="#/docs/accuracy">How accurate is it?</a></p>
        </div>
      </section>

      <section className="rule">
        <div className="wrap">
          <div className="head"><span className="eyebrow">Why WatchFox</span><h2>Built to stay out of your way.</h2></div>
          <div className="features">
            {FEATURES.map(([n, t, d]) => <div className="feat" key={n}><span className="ic">{n}</span><h3>{t}</h3><p>{d}</p></div>)}
          </div>
        </div>
      </section>

      <section className="rule">
        <div className="wrap">
          <div className="head"><span className="eyebrow">Roadmap</span><h2>Email first. Everything else that holds documents next.</h2></div>
          <div className="road">
            {ROAD.map(([t, d, when, live]) => (
              <div className={`rd ${live ? 'live' : ''}`} key={t}><h3>{t}</h3><p>{d}</p><span className="when">{live ? '● ' : ''}{when}</span></div>
            ))}
          </div>
        </div>
      </section>

      <section className="rule">
        <div className="wrap">
          <span className="eyebrow">Questions</span>
          <Faq where="home" title="Good to know before you start" />
          <p className="muted small">More answers in the <a href="#/docs/faq">documentation FAQ</a>.</p>
        </div>
      </section>

      <section className="cta invert">
        <div className="wrap">
          <Fox size={84} />
          <h2 style={{ fontSize: 'clamp(2.4rem, 6vw, 4.6rem)' }}>Give your app a sharper eye.</h2>
          <p className="lead" style={{ margin: '1.2rem auto 0' }}>Create an account, grab a key, and catch your first email in a few minutes.</p>
          <div className="row"><a className="btn" href="#/signup" style={{ background: 'var(--fg)', color: 'var(--bg)' }}>Get your API key →</a><a className="btn ghost" href="#/docs">Read the docs</a></div>
        </div>
      </section>
    </>
  )
}
