import { useEffect, useState } from 'react'
import Code from '../components/Code'
import Faq from '../components/Faq'

const SECTIONS = [
  ['quickstart', 'Quickstart'], ['agents', 'Build with an AI agent'], ['auth', 'Authentication'], ['concepts', 'Concepts'], ['mailboxes', 'Connect a mailbox'], ['alerts', 'Mailbox alerts'],
  ['watches', 'Watches'], ['what', 'What to catch'], ['webhooks', 'Webhooks'], ['files', 'Full email and attachments'], ['results', 'Results and events'], ['log', 'Activity log'],
  ['classify', 'Test a message'], ['plans', 'Plans and runs'], ['team', 'Team and sign-in'], ['errors', 'Errors and limits'], ['faq', 'FAQ'], ['accuracy', 'Accuracy'], ['security', 'Security and privacy'], ['roadmap', 'Roadmap'],
]
const current = () => window.location.hash.replace(/^#\/?docs\/?/, '') || 'quickstart'
const E = ({ m, p }) => <h3><span className={`method ${m === 'POST' ? 'post' : ''}`}>{m}</span><code style={{ fontSize: '.9em' }}>{p}</code></h3>

export default function Docs() {
  const [cur, setCur] = useState(current())
  useEffect(() => {
    const go = () => {
      const id = current(); setCur(id)
      requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView())
    }
    go()
    window.addEventListener('hashchange', go)
    return () => window.removeEventListener('hashchange', go)
  }, [])

  return (
    <div className="wrap docs">
      <aside className="toc">{SECTIONS.map(([id, l]) => <a key={id} href={`#/docs/${id}`} aria-current={cur === id ? 'true' : undefined}>{l}</a>)}</aside>
      <div className="doc">
        <h1>API documentation</h1>
        <p className="lead">Everything you need to watch a mailbox from your own code. REST and JSON over HTTPS. No SDK, no downloads, nothing to host.</p>
        <p>Base URL: <code>{window.location.origin}</code></p>

        <section id="agents">
          <h2>Build with an AI agent</h2>
          <p>These docs are written to be read by coding agents (Claude Code, Codex, Copilot, Cursor and others) as well as people. Give your agent one of these and it has everything it needs, with no scraping:</p>
          <table>
            <thead><tr><th>Resource</th><th>What it is</th></tr></thead>
            <tbody>
              <tr><td><a href="/llms-full.txt"><code>/llms-full.txt</code></a></td><td>The complete guide in one plain-text file: authentication, concepts, quickstart, webhook signature code, downloads, limits, errors, rules, and a generated reference of every endpoint with its fields.</td></tr>
              <tr><td><a href="/llms.txt"><code>/llms.txt</code></a></td><td>A short index that points agents to the guide and the OpenAPI file.</td></tr>
              <tr><td><a href="/v1/openapi.json"><code>/v1/openapi.json</code></a></td><td>OpenAPI 3 description of the public API, ready for code generators and tool-calling agents (also at <code>/openapi.json</code>).</td></tr>
              <tr><td><code>GET /v1/health</code></td><td>Open status check, no key needed.</td></tr>
            </tbody>
          </table>
          <p>An agent cannot sign in for you: create the first API key yourself in the dashboard and put it in an environment variable such as <code>WATCHFOX_KEY</code>. Then paste a prompt like this into your agent:</p>
          <Code label="prompt for your coding agent">{`Integrate WatchFox. Read {{BASE}}/llms-full.txt first and follow it exactly.
The API key is in the environment variable WATCHFOX_KEY. Never print it or commit it.
1. Create a sandbox mailbox and a watch for these types: <enquiry, invoice, ...> with my webhook URL <https://...>.
2. Build the webhook endpoint: verify X-WatchFox-Signature over the RAW body, answer 204 quickly,
   ignore repeated event ids, then handle event.matches. Download the .eml or attachments from
   event.email.links with the same key if needed.
3. Write a test that posts a correctly signed sample event.
4. Prove it works with POST /v1/watches/{id}/test and a sandbox email.`}</Code>
          <p>Plan limits (runs, mailboxes, watches, custom types, team seats and rate limits) are enforced on the server, so they apply to the API exactly as they do in the dashboard. An agent that goes over one gets a clear <code>400</code> or <code>429</code> message it can act on.</p>
        </section>

        <section id="quickstart">
          <h2>Quickstart</h2>
          <p>From zero to a signed webhook in five steps. This uses a <b>sandbox mailbox</b>, a pretend inbox you control, so you can test without connecting real mail.</p>
          <ol>
            <li><a href="#/signup">Create an account</a> and make an API key in the dashboard.</li>
            <li>Create a sandbox mailbox.</li>
            <li>Create a watch with your webhook URL. Save the <code>webhook_secret</code> from the response.</li>
            <li>Drop a test email into the sandbox.</li>
            <li>Your server receives a signed <code>email.matched</code> event within a few seconds.</li>
          </ol>
          <Code tabs={{
            curl: `export WATCHFOX_KEY=wf_live_...

# 1. a sandbox mailbox
curl -X POST {{BASE}}/v1/connections \\
  -H "Authorization: Bearer $WATCHFOX_KEY" -H "Content-Type: application/json" \\
  -d '{"provider": "sandbox"}'
# -> {"id": "cn_8f2a1c04b7e9", "provider": "sandbox", ...}

# 2. a watch that calls your webhook when it finds an enquiry
curl -X POST {{BASE}}/v1/watches \\
  -H "Authorization: Bearer $WATCHFOX_KEY" -H "Content-Type: application/json" \\
  -d '{"connection_id": "cn_8f2a1c04b7e9", "what": "enquiry",
       "webhook_url": "https://yourapp.com/hooks/watchfox"}'
# -> {"id": "wt_3f9a1c04b7e2", "webhook_secret": "whsec_...", ...}

# 3. an email arrives
curl -X POST {{BASE}}/v1/connections/cn_8f2a1c04b7e9/sandbox-emails \\
  -H "Authorization: Bearer $WATCHFOX_KEY" -H "Content-Type: application/json" \\
  -d '{"subject": "Pricing", "body": "Could you quote us for 40 licences?", "sender": "priya@acme.example"}'`,
            python: `import requests

BASE, KEY = "{{BASE}}", "wf_live_..."
H = {"Authorization": f"Bearer {KEY}"}

box = requests.post(f"{BASE}/v1/connections", headers=H, json={"provider": "sandbox"}).json()

watch = requests.post(f"{BASE}/v1/watches", headers=H, json={
    "connection_id": box["id"],
    "what": "enquiry",
    "webhook_url": "https://yourapp.com/hooks/watchfox",
}).json()
print(watch["webhook_secret"])      # store this: it is shown once

requests.post(f"{BASE}/v1/connections/{box['id']}/sandbox-emails", headers=H, json={
    "subject": "Pricing", "body": "Could you quote us for 40 licences?", "sender": "priya@acme.example",
})`,
            node: `const BASE = "{{BASE}}";
const H = { Authorization: \`Bearer \${process.env.WATCHFOX_KEY}\`, "Content-Type": "application/json" };
const post = (path, body) => fetch(BASE + path, { method: "POST", headers: H, body: JSON.stringify(body) }).then((r) => r.json());

const box = await post("/v1/connections", { provider: "sandbox" });
const watch = await post("/v1/watches", {
  connection_id: box.id,
  what: "enquiry",
  webhook_url: "https://yourapp.com/hooks/watchfox",
});
console.log(watch.webhook_secret);   // store this: it is shown once

await post(\`/v1/connections/\${box.id}/sandbox-emails\`, {
  subject: "Pricing", body: "Could you quote us for 40 licences?", sender: "priya@acme.example",
});`,
          }} />
          <p>No server of your own yet? Skip the webhook: matches appear under <b>Caught</b> in the dashboard and in <code>GET /v1/matches</code>. Once you have a webhook, <code>POST /v1/watches/{'{id}'}/test</code> sends it a sample event.</p>
        </section>

        <section id="auth">
          <h2>Authentication</h2>
          <p>Send your API key as a bearer token on every request. Create and revoke keys in the dashboard or through <code>/v1/keys</code>.</p>
          <Code label="header">{`Authorization: Bearer wf_live_xxxxxxxxxxxxxxxx`}</Code>
          <p>Keys are secret. Use them from your server, never from a browser or mobile app. A key is shown once when created; we store only a hash, so a lost key cannot be recovered, only replaced.</p>
          <E m="GET" p="/v1/keys" /><E m="POST" p="/v1/keys" /><E m="DELETE" p="/v1/keys/{id}" />
          <p><code>POST /v1/keys</code> takes <code>{`{"name": "production"}`}</code> and returns the full <code>key</code> once.</p>
        </section>

        <section id="concepts">
          <h2>Concepts</h2>
          <table>
            <thead><tr><th>Term</th><th>Meaning</th></tr></thead>
            <tbody>
              <tr><td>Mailbox</td><td>An inbox WatchFox may read: Gmail, Microsoft 365, IMAP, or a sandbox. Also called a <i>connection</i> (<code>cn_…</code>). Read-only.</td></tr>
              <tr><td>Watch</td><td>A mailbox plus what to catch plus where to send matches (<code>wt_…</code>). It reads each new email and checks it.</td></tr>
              <tr><td>What</td><td>One yes/no question about an email, such as “is this an invoice?”. Six are built in; you can define your own.</td></tr>
              <tr><td>Match</td><td>An email where the answer to a <i>what</i> is yes. Each what is judged on its own, so one email can match several.</td></tr>
              <tr><td>Event</td><td>The JSON describing an email that was checked. A match produces <code>email.matched</code>; the rest <code>email.checked</code>.</td></tr>
              <tr><td>Score</td><td>A number from 0 to 1 for each what: higher means more likely yes. It is <b>not a probability</b>. An email matches when its score reaches that what's threshold.</td></tr>
              <tr><td>Confidence</td><td>The same score, shown as a percentage anyone can read: how clearly the email fits, compared with the line for a match. <code>high</code> is 85 and over, <code>medium</code> 65 to 84, <code>low</code> (borderline) under 65. A guide to how clear a catch is, <b>not</b> the chance that it is right.</td></tr>
            </tbody>
          </table>
          <p>A watch looks only at mail that arrives <b>after</b> it is created (set <code>backfill_days</code> up to 7 to include recent mail). Microsoft 365 mailboxes deliver new mail within seconds. Gmail and IMAP mailboxes are checked about once a minute, so a catch reaches your webhook within a minute or two. Sandbox mailboxes are checked every couple of seconds.</p>
        </section>

        <section id="mailboxes">
          <h2>Connect a mailbox</h2>
          <p>There are two ways: send your customer to a <b>hosted connect page</b> (best for a SaaS product), or connect a mailbox <b>from your own server</b>.</p>

          <h3>The hosted connect page</h3>
          <p>You create a one-time link with your API key and send your customer there, either by redirecting their browser or by email. They connect their own mailbox on a WatchFox-hosted page that you can brand with your name, logo and colour (see below), then land back on your site. You never handle their password.</p>
          <E m="POST" p="/v1/connect-links" />
          <table>
            <thead><tr><th>Field</th><th>Meaning</th></tr></thead>
            <tbody>
              <tr><td><code>provider</code></td><td><code>gmail</code> or <code>m365</code>. Not needed when you pass <code>connection_id</code>.</td></tr>
              <tr><td><code>redirect_url</code></td><td>Where to send the person afterwards. Must be https (or localhost while developing).</td></tr>
              <tr><td><code>state</code></td><td>Any string up to 200 characters, for example a random id you issued for this customer. Returned unchanged on the redirect, so you know who came back. Check that it matches what you issued.</td></tr>
              <tr><td><code>metadata</code></td><td>A small JSON object (under 2 KB), such as <code>{`{"customer_id": "42"}`}</code>. Stored on the mailbox and included in every webhook for it.</td></tr>
              <tr><td><code>app_name</code></td><td>Your product's name, shown at the top of the page ("Connecting for ExampleCorp").</td></tr>
              <tr><td><code>connection_id</code></td><td>Reconnect an existing mailbox instead of adding one. See <a href="#/docs/alerts">Mailbox alerts</a>.</td></tr>
              <tr><td><code>logo_url</code></td><td>Your logo, shown at the top of the page in place of WatchFox's. <code>https</code> only, up to 400 characters. A wide logo on a transparent or white background works best; it is shown up to 2.4 rem tall.</td></tr>
              <tr><td><code>accent_color</code></td><td>Your brand colour as <code>#RRGGBB</code> (for example <code>#1A73E8</code>). It colours the buttons and links on the page. Pick a colour dark enough for white text.</td></tr>
              <tr><td><code>support_email</code></td><td>Your help address, shown as “Need help?” at the bottom of the page.</td></tr>
              <tr><td><code>hide_powered_by</code></td><td><code>true</code> removes the “Powered by WatchFox” line. Needs the Tracker or Alpha plan; on Kit it returns <code>400</code>.</td></tr>
            </tbody>
          </table>
          <h3>Branding the connect page</h3>
          <p>The page is hosted by WatchFox, so it is <b>co-branded, not fully white-labelled</b>. Here is exactly what you control and what stays.</p>
          <table>
            <thead><tr><th>You can brand</th><th>Stays WatchFox</th></tr></thead>
            <tbody>
              <tr><td>Your logo and product name at the top. With a <code>logo_url</code> the WatchFox logo is removed from the page header.</td><td>The address of the page, <code>{window.location.host}/connect</code>. It is not served from your domain.</td></tr>
              <tr><td>The colour of the buttons and links (<code>accent_color</code>).</td><td>Microsoft 365: Microsoft's own sign-in and consent screen names WatchFox as the app asking for read-only access. That screen is Microsoft's and cannot be changed from here.</td></tr>
              <tr><td>Your help address (<code>support_email</code>), and your product name in the Gmail steps (the customer is told to name the app password after <code>app_name</code>).</td><td>The “Powered by WatchFox” line, unless you are on Tracker or Alpha and set <code>hide_powered_by</code>.</td></tr>
            </tbody>
          </table>
          <p>Need the customer to never leave your own interface? For Gmail app passwords and IMAP you can build your own form and call <code>POST /v1/connections</code> from your server (see below). Microsoft 365 always needs the sign-in redirect.</p>
          <Code label="a branded link">{`curl -X POST {{BASE}}/v1/connect-links \\
  -H "Authorization: Bearer $WATCHFOX_KEY" -H "Content-Type: application/json" \\
  -d '{"provider": "gmail", "redirect_url": "https://yourapp.com/mailbox/done", "state": "a1b2c3",
       "app_name": "ExampleCorp", "logo_url": "https://yourapp.com/logo.svg", "accent_color": "#1A73E8",
       "support_email": "help@yourapp.example", "hide_powered_by": true}'`}</Code>
          <Code tabs={{
            curl: `curl -X POST {{BASE}}/v1/connect-links \\
  -H "Authorization: Bearer $WATCHFOX_KEY" -H "Content-Type: application/json" \\
  -d '{"provider": "gmail",
       "redirect_url": "https://yourapp.com/settings/mailbox/done",
       "state": "a1b2c3", "metadata": {"customer_id": "42"}, "app_name": "ExampleCorp"}'
# -> {"url": "{{BASE}}/connect?t=...", "expires_in": 3600}`,
            node: `// 1. your backend, when the customer clicks "Connect your mailbox"
app.post("/api/mailbox/connect", requireLogin, async (req, res) => {
  const state = crypto.randomUUID();
  await db.pending.insert({ state, customerId: req.user.customerId });
  const link = await post("/v1/connect-links", {
    provider: req.body.provider,                       // "gmail" or "m365"
    redirect_url: \`\${APP_URL}/settings/mailbox/done\`,
    state, metadata: { customer_id: req.user.customerId }, app_name: "ExampleCorp",
  });
  res.json({ url: link.url });                         // the frontend then does: window.location = url
});

// 2. the customer comes back here after connecting
app.get("/settings/mailbox/done", requireLogin, async (req, res) => {
  const pending = await db.pending.take(req.query.state);          // proves this return belongs to this customer
  if (req.query.status !== "connected" || !pending) return res.redirect("/settings?mailbox=failed");
  const watch = await post("/v1/watches", {
    connection_id: req.query.connection_id, what: ["enquiry"],
    webhook_url: \`\${API_URL}/webhooks/watchfox\`,
  });
  await db.mailboxes.insert({ customerId: pending.customerId, connectionId: req.query.connection_id,
                              watchId: watch.id, webhookSecret: watch.webhook_secret });
  res.redirect("/settings?mailbox=connected");
});`,
            python: `# 1. when the customer clicks "Connect your mailbox"
link = requests.post(f"{BASE}/v1/connect-links", headers=H, json={
    "provider": "m365",                                 # or "gmail"
    "redirect_url": "https://yourapp.com/settings/mailbox/done",
    "state": state, "metadata": {"customer_id": customer_id}, "app_name": "ExampleCorp",
}).json()
# redirect the browser to link["url"]

# 2. on the redirect_url: ?status=connected&connection_id=cn_...&email=...&state=...
watch = requests.post(f"{BASE}/v1/watches", headers=H, json={
    "connection_id": connection_id, "what": ["enquiry"], "webhook_url": "https://yourapp.com/webhooks/watchfox",
}).json()`,
          }} />
          <p>The person returns to <code>redirect_url</code> with <code>?status=connected&amp;connection_id=cn_…&amp;email=…&amp;state=…</code>. If something fails, they stay on the WatchFox page with an explanation and no redirect happens. Without a <code>redirect_url</code> they see a "Connected" page.</p>

          <h3>What your customer sees</h3>
          <p><b>Gmail.</b> A page headed with your app's name walks them through four short steps with pictures: check that 2-Step Verification is on, create an <i>app password</i> in their Google account (a one-off password just for WatchFox, not their usual one), copy the 16 letters, and paste them with their Gmail address. WatchFox checks that the password works straight away and tells them clearly if it doesn't. Then they are sent back to you. They need 2-Step Verification, and some company Google accounts have app passwords switched off by an administrator.</p>
          <p><b>Microsoft 365.</b> One button opens Microsoft's own sign-in page. They sign in, press Accept to allow read-only access to their mail, and come straight back. Some organisations let only an administrator approve new apps; in that case the page offers a link they can send to their IT admin, who approves once for the whole organisation.</p>

          <h3>Connect from your server</h3>
          <E m="POST" p="/v1/connections" />
          <Code label="body">{`{"provider": "gmail", "user": "inbox@gmail.com", "password": "abcdefghijklmnop"}        // Gmail with an app password
{"provider": "imap", "host": "imap.example.com", "port": 993, "user": "inbox@example.com", "password": "...", "folder": "INBOX"}
{"provider": "sandbox"}                                                                  // a pretend mailbox for testing
// every form accepts "metadata": {...}`}</Code>
          <p>IMAP servers must be on a public address and use port 993 (IMAP over TLS); addresses inside private networks are refused. The credentials are checked before they are saved; a wrong one returns 400 with the reason. Prefer the hosted page for customers' mailboxes, so their passwords never pass through your servers.</p>

          <h3>Sandbox</h3>
          <p>A pretend mailbox for building and testing. Add emails yourself; they are picked up within a couple of seconds.</p>
          <E m="POST" p="/v1/connections/{id}/sandbox-emails" />
          <Code label="body">{`{"subject": "Invoice 2291", "body": "Please find attached invoice 2291 for $2,450.", "sender": "billing@supplier.example",
 "attachments": [{"filename": "invoice.pdf", "content_type": "application/pdf", "content_base64": "JVBERi0xLjQK..."}]}`}</Code>
          <p><code>attachments</code> is optional (up to 5 files, 5 MB in all), so you can test how your code handles files. Sandbox emails are stored until you delete the mailbox, since you created them for testing.</p>

          <h3>List, read and remove</h3>
          <E m="GET" p="/v1/connections" /><E m="GET" p="/v1/connections/{id}" /><E m="DELETE" p="/v1/connections/{id}" />
          <Code label="connection object">{`{
  "id": "cn_8f2a1c04b7e9",
  "provider": "m365",                 // gmail, m365, imap or sandbox
  "email": "boss@contoso.example",
  "method": "oauth",                  // oauth, app_password, password or sandbox
  "status": "active",                 // or "needs_attention"
  "status_reason": null,
  "delivery": "push",                 // "push" for Microsoft 365 when active, otherwise "polling"
  "metadata": { "customer_id": "42" },
  "created": 1791491000
}`}</Code>
          <p>Removing a mailbox deletes its stored credentials, its watches and their records.</p>

          <h3>How new mail reaches WatchFox</h3>
          <ul>
            <li><b>Microsoft 365:</b> pushed to WatchFox by Microsoft within seconds. WatchFox renews the connection automatically and also does a slow safety check about every 10 minutes, so a missed notification never loses an email.</li>
            <li><b>Gmail and IMAP:</b> checked about once a minute.</li>
            <li>A watch only looks at mail received after it was created (plus <code>backfill_days</code>).</li>
          </ul>
        </section>

        <section id="alerts">
          <h2>Mailbox alerts and reconnecting</h2>
          <p>Sign-ins don't last forever. A person can revoke access, change their Google password (which cancels a Gmail app password), or their organisation can change policy. When WatchFox can no longer read a mailbox it stops checking it, so it can never lock the account with repeated failed logins, and tells you once.</p>
          <ul>
            <li>The connection's <code>status</code> becomes <code>needs_attention</code> and <code>status_reason</code> says why.</li>
            <li>A <code>connection.needs_attention</code> event is sent to the webhook of every watch on that mailbox, and appears on <code>/v1/events</code>.</li>
          </ul>
          <Code label="webhook event">{`{
  "id": "evt_0b7c2d9e41aa",
  "type": "connection.needs_attention",
  "created": 1791495000,
  "connection_id": "cn_8f2a1c04b7e9",
  "provider": "gmail",
  "email": "me@gmail.com",
  "reason": "Google did not accept this email address and app password. ...",
  "metadata": { "customer_id": "42" }
}`}</Code>
          <p>To fix it, create a connect link for the <b>same mailbox</b> and send the person to it. They reconnect on the same hosted page; their watches, history and <code>connection_id</code> stay as they were, so nothing on your side changes.</p>
          <Code label="reconnect">{`POST /v1/connect-links
{"connection_id": "cn_8f2a1c04b7e9", "redirect_url": "https://yourapp.com/settings/mailbox/done", "state": "a1b2c3"}`}</Code>
          <p>The reconnect page only accepts the same email address. Temporary problems, such as a mail server being unreachable, are retried quietly and only raise an alert after several failures in a row.</p>
        </section>

        
<section id="watches">
          <h2>Watches</h2>
          <E m="POST" p="/v1/watches" />
          <table>
            <thead><tr><th>Field</th><th>Type</th><th>Meaning</th></tr></thead>
            <tbody>
              <tr><td><code>connection_id</code></td><td>string, required</td><td>The mailbox to watch.</td></tr>
              <tr><td><code>what</code></td><td>string, list, or object</td><td>What to catch: a preset id, a list of them, or custom definitions. See <a href="#/docs/what">What to catch</a>. Default <code>"enquiry"</code>.</td></tr>
              <tr><td><code>webhook_url</code></td><td>string</td><td>Where to POST matches. Must be <code>https</code> and publicly reachable. Optional: without it you read matches through the API.</td></tr>
              <tr><td><code>webhook_secret</code></td><td>string</td><td>Your own signing secret. If you send a <code>webhook_url</code> and no secret, one is generated.</td></tr>
              <tr><td><code>metadata</code></td><td>object</td><td>Your own values (under 2 KB), added to every webhook for this watch.</td></tr>
              <tr><td><code>include</code></td><td>list</td><td>Any of <code>"body"</code>, <code>"eml"</code>, <code>"attachments"</code>: send the full text, the whole <code>.eml</code> and the files inside the webhook. See <a href="#/docs/files">Full email and attachments</a>. Needs a <code>webhook_url</code>.</td></tr>
              <tr><td><code>backfill_days</code></td><td>integer 0–7</td><td>Also check mail received this many days before the watch was created. Default 0.</td></tr>
            </tbody>
          </table>
          <Code label="watch object">{`{
  "id": "wt_3f9a1c04b7e2",
  "connection_id": "cn_8f2a1c04b7e9",
  "what": ["enquiry", "invoice"],
  "labels": ["Enquiries", "Invoices"],
  "webhook_url": "https://yourapp.com/hooks/watchfox",
  "webhook_secret": "whsec_...",          // only in the create response
  "active": true,
  "backfill_days": 0,
  "created": 1791491000,
  "last_checked": 1791491060,
  "last_error": null                      // e.g. "The Microsoft sign-in expired. Connect the mailbox again."
}`}</Code>
          <E m="GET" p="/v1/watches" /><E m="GET" p="/v1/watches/{id}" /><E m="DELETE" p="/v1/watches/{id}" />
          <E m="POST" p="/v1/watches/{id}/test" />
          <p>Sends one sample <code>email.matched</code> event (marked <code>"test": true</code>) to the watch's webhook, once, and tells you what your server answered: <code>{`{"delivered": true, "status_code": 204}`}</code>.</p>
          <E m="GET" p="/v1/watches/{id}/deliveries" />
          <p>The latest webhook deliveries with <code>status</code> (<code>delivered</code>, <code>retrying</code>, <code>failed</code>), HTTP <code>code</code> and number of <code>attempts</code>. Use it to debug your endpoint.</p>
        </section>

        <section id="what">
          <h2>What to catch</h2>
          <E m="GET" p="/v1/presets" />
          <table>
            <thead><tr><th>id</th><th>Catches</th><th>Settings</th></tr></thead>
            <tbody>
              <tr><td><code>enquiry</code></td><td>Someone asking for a quote, price or proposal: RFQs, RFPs, spec packs. Skips adverts and cold pitches, newsletters and automatic mail, invoices and receipts, and documents such as purchase orders, order confirmations and quotations sent to you.</td><td>Tuned</td></tr>
              <tr><td><code>invoice</code></td><td>An invoice or a bill asking for payment.</td><td>Tuned</td></tr>
              <tr><td><code>support</code></td><td>Someone reporting a problem or asking for help with something they already use.</td><td>Tuned</td></tr>
              <tr><td><code>purchase_order</code></td><td>A purchase order, or an order placed for goods or services.</td><td>Tuned</td></tr>
              <tr><td><code>job_application</code></td><td>A resume or an application for a job.</td><td>Tuned</td></tr>
              <tr><td><code>claim</code></td><td>Someone claiming a refund, a replacement, compensation or a warranty repair.</td><td>Tuned</td></tr>
            </tbody>
          </table>
          <p><b>Tuned</b> means the question, threshold and exclusions were chosen by measuring them on labelled sample emails. Enquiries were tuned on real quote requests; the others on a built-in set of about 145 sample emails, so check them on your own mail first (see <a href="#/docs/classify">Test a message</a>).</p>
          <p>Note that <code>enquiry</code> is about requests for a quote, price or proposal. It was tuned for that, not for general sales questions.</p>

          <h3>Create your own type (tuned for you)</h3>
          <p>In the dashboard open <b>Types</b>, name the type, describe it in a sentence, and paste at least three example emails that should be caught (optionally some look-alikes that should not). WatchFox tries several ways of asking, scores them on your examples plus its built-in samples, picks the best, sets the threshold and adds exclusions for the look-alikes it still gets wrong, then shows what it caught and missed. Save it and use it like any other type. The same is available over the API:</p>
          <Code label="tune and save a type">{`# 1. Draft the settings (about 30 seconds, nothing is saved yet)
curl -X POST {{BASE}}/v1/types/tune -H "Authorization: Bearer $WATCHFOX_KEY" -H "Content-Type: application/json" -d '{
  "label": "Shipping delays",
  "description": "A supplier telling us an order will arrive later than promised.",
  "positives": [{"subject": "Delay on PO 4411", "body": "The shipment is delayed by two weeks."}, ...],
  "negatives": [{"subject": "Your order shipped", "body": "On time, tracking below."}]
}'
# 2. Save the result as it is, or edit it first
curl -X POST {{BASE}}/v1/types -H "Authorization: Bearer $WATCHFOX_KEY" -H "Content-Type: application/json" -d '<the JSON from step 1>'
# 3. Use the returned id (type_...) as "what" in watches and /v1/classify.  GET /v1/types lists yours.`}</Code>
          <p>Tuning is measured, not magic: the more real examples you give, especially look-alikes it should ignore, the better it gets. If the AI assistant is switched on for your WatchFox, you can also type one sentence (<code>POST /v1/types/suggest</code>) and get a name, description, examples and look-alikes drafted for you, and add more examples with one click (<code>POST /v1/types/examples</code>). The assistant only ever sees the name and description you type, never an email, and it is never used when WatchFox checks your mail.</p>
          <h3>Teach it from mistakes</h3>
          <p>On a caught email press <b>Wrong catch</b>, or on one it did not catch press <b>Should have been caught</b> (<code>POST /v1/feedback</code>). Then choose <b>Tune again with these</b> on the Types page (<code>POST /v1/corrections/{'{'}what{'}'}/retune</code>). You review the new settings before they are used (<code>PUT /v1/types/{'{'}what{'}'}</code>), and it works on the built-in types too: you get your own tuned copy and can reset it any time.</p>

          <h3>Define your own by hand</h3>
          <p>Anything you can phrase as a yes/no question. Pass an object in <code>what</code> (alone or in a list with presets):</p>
          <Code label="custom what">{`{
  "id": "urgent_repair",                        // key used in results
  "label": "Urgent repair",                     // shown in dashboards and events
  "question": "Is this an urgent request for a repair or a technician?",
  "threshold": 0.5,                             // match from this score (0 to 1)
  "exclude_question": "Is this an automated notification?",   // optional veto
  "exclude_threshold": 0.5
}`}</Code>
          <p>To veto on several things at once, send <code>excludes</code> instead: a list of <code>{`{"question": "...", "threshold": 0.5}`}</code>. The email is cancelled if any one of them reaches its own threshold. The built-in enquiry catch uses three questions (marketing and automated mail; invoices, bills and receipts; cold sales pitches) plus two plain rules: it ignores bulk mail (newsletters and mass mailings, recognised by their unsubscribe header or link) and subjects that look like a purchase order, order confirmation, quotation number such as <code>Q-4471</code> or price list. You can add the same rules to your own catches with <code>skip_bulk</code> (true or false) and <code>ignore_subject</code> (a list of regular expressions, case-insensitive).</p>
          <p>Write the question so that <b>yes</b> means “this is what I want”. Keep it short and concrete. Try it with <code>/v1/classify</code> on a handful of real emails and set <code>threshold</code> from the scores you see: scores are not probabilities, and a good threshold is rarely 0.5 exactly. An <code>exclude_question</code> is a second question that cancels a match when it answers yes at or above <code>exclude_threshold</code>, useful for filtering out newsletters and automated mail.</p>
        </section>

        <section id="webhooks">
          <h2>Webhooks</h2>
          <p>When a watch with a <code>webhook_url</code> finds a match, WatchFox POSTs the event as JSON.</p>
          <Code label="POST https://yourapp.com/hooks/watchfox">{`{
  "id": "evt_5c1e7a90d3b2",
  "type": "email.matched",
  "created": 1791491913,
  "watch_id": "wt_3f9a1c04b7e2",
  "connection_id": "cn_8f2a1c04b7e9",
  "metadata": { "customer_id": "42" },
  "email": {
    "id": "18f3a2c9d4e1b7a0",
    "subject": "RFQ-2026-118: Document management system",
    "from": "Procurement <procurement@gulfbuild.example>",
    "received": 1791491900,
    "snippet": "Dear Supplier, Please find attached our Request for Quotation…",
    "attachments": [{ "id": "0", "filename": "RFQ-2026-118.pdf", "content_type": "application/pdf", "size": 184220,
                    "url": "{{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/attachments/0" }],
    "links": { "raw": "{{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/raw" }
  },
  "matches": [{ "what": "enquiry", "label": "Enquiries", "score": 0.64, "confidence": 94, "confidence_level": "high" }],
  "results": {
    "enquiry": { "label": "Enquiries", "score": 0.64, "confidence": 94, "confidence_level": "high", "threshold": 0.38,
                 "exclude_score": 0.29, "matched": true, "excluded": false, "reason": null },
    "invoice": { "label": "Invoices", "score": 0.12, "threshold": 0.5,
                 "exclude_score": null, "matched": false, "excluded": false }
  }
}`}</Code>
          <p><code>metadata</code> is what you attached to the mailbox and the watch (the watch's values win), so you can tell whose email it is without a lookup. <code>snippet</code> is the first 300 characters of the email text. Attachments are listed with their name, type and size. Their contents, and the whole <code>.eml</code>, come only if you ask: see <a href="#/docs/files">Full email and attachments</a>.</p>

          <h3>Headers</h3>
          <table>
            <thead><tr><th>Header</th><th>Value</th></tr></thead>
            <tbody>
              <tr><td><code>X-WatchFox-Event</code></td><td><code>email.matched</code> or <code>connection.needs_attention</code></td></tr>
              <tr><td><code>X-WatchFox-Delivery</code></td><td>Unique id of this delivery (<code>dl_…</code>).</td></tr>
              <tr><td><code>X-WatchFox-Signature</code></td><td><code>t=1791491913,v1=&lt;hex&gt;</code></td></tr>
            </tbody>
          </table>

          <h3>Verify the signature</h3>
          <p>Compute <code>HMAC-SHA256</code> with your watch's secret over the string <code>{'{t}.{raw request body}'}</code> and compare it with <code>v1</code>. Use the <b>raw body bytes</b>, not re-serialised JSON. Reject requests whose <code>t</code> is more than five minutes old.</p>
          <Code tabs={{
            python: `import hashlib, hmac, time

def verify(secret: str, raw_body: bytes, header: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300:
        return False
    expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])`,
            node: `import crypto from "node:crypto";

export function verify(secret, rawBody, header) {
  const parts = Object.fromEntries(header.split(",").map((p) => p.split("=")));
  if (Math.abs(Date.now() / 1000 - Number(parts.t)) > 300) return false;
  const expected = crypto.createHmac("sha256", secret).update(\`\${parts.t}.\${rawBody}\`).digest("hex");
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(parts.v1));
}`,
            express: `import express from "express";
const app = express();

// keep the raw body: the signature is computed over the exact bytes
app.post("/hooks/watchfox", express.raw({ type: "application/json" }), (req, res) => {
  if (!verify(process.env.WATCHFOX_WEBHOOK_SECRET, req.body.toString("utf8"), req.get("X-WatchFox-Signature"))) {
    return res.sendStatus(400);
  }
  const event = JSON.parse(req.body);
  console.log(event.matches.map((m) => m.label), event.email.subject);
  res.sendStatus(204);     // answer 2xx quickly, do slow work afterwards
});`,
          }} />

          <h3>Delivery and retries</h3>
          <ul>
            <li>Answer with any <b>2xx</b> within 10 seconds. Anything else, or no answer, counts as a failure.</li>
            <li>Failures are retried up to <b>4 attempts</b> in total: immediately, then after 10 seconds, 1 minute and 5 minutes.</li>
            <li>Redirects are not followed. Webhook URLs must be <code>https</code> and resolve to a public address.</li>
            <li>The same event can arrive more than once. Use <code>id</code> to ignore repeats.</li>
            <li>Check <code>GET /v1/watches/{'{id}'}/deliveries</code> if something is missing.</li>
          </ul>
        </section>

        <section id="files">
          <h2>Full email and attachments</h2>
          <p>By default a webhook carries what WatchFox captured: the type that matched, the scores, the sender, the subject, a 300-character snippet and a list of attachments. When your code needs the real thing for further processing, there are two ways to get it. Both give you the <b>entire original <code>.eml</code></b> and every attachment, and WatchFox <b>keeps no copy</b> of either.</p>

          <h3>1. Inside the webhook</h3>
          <p>Set <code>include</code> on the watch to any of these. They are added to every webhook for that watch.</p>
          <table>
            <thead><tr><th><code>include</code></th><th>Adds to <code>email</code> in the webhook</th></tr></thead>
            <tbody>
              <tr><td><code>"body"</code></td><td><code>body</code>: the full text of the email (up to 256 KB).</td></tr>
              <tr><td><code>"eml"</code></td><td><code>eml_base64</code>: the whole original message as an <code>.eml</code> file, base64 encoded.</td></tr>
              <tr><td><code>"attachments"</code></td><td>Each attachment gets <code>content_base64</code>: the file itself, base64 encoded.</td></tr>
            </tbody>
          </table>
          <Code label="POST /v1/watches">{`{"connection_id": "cn_8f2a1c04b7e9", "what": ["enquiry"],
 "webhook_url": "https://yourapp.com/hooks/watchfox",
 "include": ["body", "eml", "attachments"]}`}</Code>
          <Code label="what the webhook then contains (email part)">{`"email": {
  "id": "18f3a2c9d4e1b7a0",
  "subject": "RFQ-2026-118: Document management system",
  "from": "Procurement <procurement@gulfbuild.example>",
  "snippet": "Dear Supplier, Please find attached our Request for Quotation…",
  "body": "Dear Supplier,\\n\\nPlease find attached our Request for Quotation …(the full text)",
  "eml_base64": "RnJvbTogUHJvY3VyZW1lbnQg…",
  "attachments": [
    { "id": "0", "filename": "RFQ-2026-118.pdf", "content_type": "application/pdf", "size": 184220,
      "content_base64": "JVBERi0xLjQK…",
      "url": "{{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/attachments/0" }
  ],
  "links": { "raw": "{{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/raw",
             "attachments": "{{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/attachments" }
}`}</Code>
          <p><b>Size limit.</b> Webhooks should stay small, so the <code>.eml</code> and the files together are included only up to <b>5 MB</b>. Anything over that is replaced by an <code>eml_omitted</code> or <code>omitted</code> note; fetch it with the download calls below. Even without <code>include</code>, every webhook lists the attachments (name, type, size) and has the <code>links</code> and <code>url</code> fields.</p>

          <h3>2. By download</h3>
          <p>These calls read the message from the customer's mailbox <b>at that moment</b> and pass it straight to you.</p>
          <E m="GET" p="/v1/connections/{id}/emails/{email_id}/raw" />
          <p>The entire original message as an <code>.eml</code> file (<code>message/rfc822</code>).</p>
          <E m="GET" p="/v1/connections/{id}/emails/{email_id}/attachments" />
          <p>A list of the attachments: <code>id</code>, <code>filename</code>, <code>content_type</code>, <code>size</code> and <code>url</code>.</p>
          <E m="GET" p="/v1/connections/{id}/emails/{email_id}/attachments/{attachment_id}" />
          <p>One attachment, as the file itself with its own content type.</p>
          <Code tabs={{
            curl: `# the webhook gave you connection_id and email.id
curl -o email.eml -H "Authorization: Bearer $WATCHFOX_KEY" \\
  {{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/raw

curl -o spec.pdf -H "Authorization: Bearer $WATCHFOX_KEY" \\
  {{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/attachments/0`,
            python: `ev = request.get_json()                       # the webhook
base = f"{BASE}/v1/connections/{ev['connection_id']}/emails/{ev['email']['id']}"

eml = requests.get(f"{base}/raw", headers=H).content           # the whole .eml
for a in ev["email"]["attachments"]:
    data = requests.get(a["url"], headers=H).content           # each file
    open(a["filename"], "wb").write(data)`,
            node: `const ev = JSON.parse(rawBody);                  // the webhook
const eml = await fetch(ev.email.links.raw, { headers: H }).then((r) => r.arrayBuffer());
for (const a of ev.email.attachments) {
  const data = await fetch(a.url, { headers: H }).then((r) => r.arrayBuffer());
  await fs.promises.writeFile(a.filename, Buffer.from(data));
}`,
          }} />
          <h3>Rules</h3>
          <ul>
            <li><b>Only emails that matched one of your watches</b> can be downloaded. You cannot use these calls to browse the rest of a customer's mailbox. Anything else returns 404.</li>
            <li>The email must still be in the mailbox. If the person deleted or moved it, you get 404. Store what you need when the webhook arrives.</li>
            <li>A single email can be up to <b>100 MB</b>. A larger one returns 413 and is never read. (The webhook itself carries at most 5 MB inline.)</li>
            <li>If the mailbox needs the person to sign in again, you get 409 (see <a href="#/docs/alerts">Mailbox alerts</a>).</li>
            <li>Downloads are limited to 120 per minute per account. Responses are sent with <code>Cache-Control: no-store</code>.</li>
            <li>Calls need your API key, so URLs inside webhooks are not usable by anyone else.</li>
          </ul>

          <h3>Zero retention</h3>
          <table>
            <thead><tr><th>WatchFox keeps</th><th>WatchFox never keeps</th></tr></thead>
            <tbody>
              <tr><td>The subject, the sender, a 300-character snippet, the attachment file names, and the scores, so you can list what was caught.</td><td>The full email text, the <code>.eml</code> file, and attachment contents. Neither the download calls nor <code>include</code> save a copy.</td></tr>
            </tbody>
          </table>
          <p>The message is read from the mailbox, handed to you, and gone. Content sent inside a webhook is held in memory only while WatchFox delivers it, for up to about six minutes if your server is down and deliveries are being retried, and is then discarded. Deleting a watch deletes the snippets and delivery logs it produced. The only content WatchFox stores is what <i>you</i> put into a sandbox mailbox for testing, which is removed when you delete that mailbox, and the examples you choose to give a type: when you press <b>Wrong catch</b> or <b>Should have been caught</b> on an email, WatchFox reads that one email and keeps its subject and up to 3,000 characters of text so the type can learn from it. That happens only when you press the button. Delete them any time (Types, Corrections, Clear, or <code>DELETE /v1/corrections/{'{'}what{'}'}</code>).</p>
        </section>

        <section id="results">

          <h2>Results and events</h2>
          <p>You do not need a webhook. Read results with these calls, or stream them.</p>
          <E m="GET" p="/v1/matches" />
          <p>Emails that matched, newest first. Query: <code>what</code> (filter by a what id), <code>watch_id</code>, <code>limit</code> (default 50, max 1000).</p>
          <E m="GET" p="/v1/emails" />
          <p>Every email that was checked, matched or not. Query: <code>watch_id</code>, <code>limit</code>.</p>
          <Code label="item">{`{
  "watch_id": "wt_3f9a1c04b7e2",
  "email": { "id": "...", "subject": "...", "from": "...", "received": 1791491900, "snippet": "...", "attachments": [] },
  "matched": true,
  "results": { "enquiry": { "label": "Enquiries", "score": 0.64, "threshold": 0.38, "matched": true, "excluded": false } },
  "checked": 1791491913
}`}</Code>
          <E m="GET" p="/v1/events" />
          <p>A Server-Sent Events stream of every email checked, with the same shape as a webhook event (<code>email.matched</code> or <code>email.checked</code>). Read it from your server, since browsers cannot add the Authorization header to <code>EventSource</code>.</p>
          <Code tabs={{
            curl: `curl -N {{BASE}}/v1/events -H "Authorization: Bearer $WATCHFOX_KEY"`,
            python: `import json, requests

with requests.get(f"{BASE}/v1/events", headers=H, stream=True) as r:
    for line in r.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            event = json.loads(line[6:])
            if event["matches"]:
                print(event["matches"][0]["label"], event["email"]["subject"])`,
          }} />
        </section>

        <section id="log">
          <h2>Activity log</h2>
          <p><b>Logs are kept for 90 days.</b> After that WatchFox deletes them automatically, so download anything you need to keep: page through <code>GET /v1/log</code> on a schedule (for example once a day with <code>since</code>), or get a spreadsheet with <code>GET /v1/log/export</code> (same filters, CSV). The dashboard has an <b>Export CSV</b> button on the Activity log page.</p>
          <p>Every email WatchFox checked for you, what it decided, and what happened to your webhook. The dashboard's <b>Activity log</b> page shows the same data, and this call returns it to your code. Use it to audit what was delivered, find failed webhooks, or build your own screen.</p>
          <E m="GET" p="/v1/log" />
          <p>Responses include <code>retention_days</code> so your code can see the current limit. The export also takes <code>max_rows</code> (up to 500,000).</p>
          <table>
            <thead><tr><th>Query</th><th>Meaning</th></tr></thead>
            <tbody>
              <tr><td><code>matched</code></td><td><code>true</code> for emails that were caught, <code>false</code> for the rest. Leave out for both.</td></tr>
              <tr><td><code>what</code></td><td>Only emails where this type matched, such as <code>enquiry</code>.</td></tr>
              <tr><td><code>watch_id</code>, <code>connection_id</code></td><td>Only one watch or one mailbox.</td></tr>
              <tr><td><code>delivery</code></td><td>Webhook outcome: <code>delivered</code>, <code>failed</code>, <code>retrying</code>, <code>pending</code>, or <code>none</code> (no webhook was sent).</td></tr>
              <tr><td><code>q</code></td><td>Text to find in the subject or sender.</td></tr>
              <tr><td><code>since</code></td><td>Only emails checked at or after this time (Unix seconds).</td></tr>
              <tr><td><code>limit</code></td><td>1 to 200. Default 50.</td></tr>
              <tr><td><code>before</code></td><td>For the next page: pass the <code>next_before</code> from the previous response.</td></tr>
            </tbody>
          </table>
          <Code tabs={{
            curl: `# every caught email whose webhook failed, newest first
curl "{{BASE}}/v1/log?matched=true&delivery=failed&limit=100" \\
  -H "Authorization: Bearer $WATCHFOX_KEY"`,
            python: `params = {"matched": "true", "limit": 200}
while True:
    page = requests.get(f"{BASE}/v1/log", headers=H, params=params).json()
    for e in page["items"]:
        print(e["checked_at"], e["email"]["subject"], e["delivery"] and e["delivery"]["status"])
    if not page["next_before"]:
        break
    params["before"] = page["next_before"]          # the next, older page`,
            node: `let before;
do {
  const q = new URLSearchParams({ matched: "true", limit: "200", ...(before && { before }) });
  const page = await fetch(\`\${BASE}/v1/log?\${q}\`, { headers: H }).then((r) => r.json());
  for (const e of page.items) console.log(e.checked_at, e.email.subject, e.delivery?.status);
  before = page.next_before;
} while (before);`,
          }} />
          <Code label="response">{`{
  "items": [
    {
      "id": "wt_3f9a1c04b7e2:18f3a2c9d4e1b7a0",
      "checked_at": 1791491913,
      "received_at": 1791491900,
      "watch_id": "wt_3f9a1c04b7e2",
      "connection_id": "cn_8f2a1c04b7e9",
      "email": { "id": "18f3a2c9d4e1b7a0", "subject": "RFQ-2026-118: Door operators",
                 "from": "procurement@gulfbuild.example", "snippet": "Please quote 40 door operators…",
                 "attachments": [{ "filename": "RFQ-2026-118.pdf" }] },
      "matched": true,
      "matches": [{ "what": "enquiry", "label": "Enquiries", "score": 0.64,
                    "confidence": 94, "confidence_level": "high" }],
      "ignored": [],
      "delivery": { "status": "delivered", "webhook_url": "https://yourapp.com/hooks/watchfox",
                    "http_status": 204, "attempts": 1, "error": null,
                    "sent_at": 1791491913, "last_attempt_at": 1791491913, "delivered_at": 1791491913,
                    "event_id": "evt_5c1e7a90d3b2" },
      "include": ["eml"],
      "links": { "raw": "{{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/raw" }
    }
  ],
  "next_before": 1791491880.5
}`}</Code>
          <ul>
            <li><b><code>ignored</code></b> lists catches that reached the match line but were skipped, with the reason, such as "subject matches an ignore rule" or "looks like bulk or automatic mail".</li>
            <li><b><code>delivery</code></b> is <code>null</code> when no webhook was due. <code>status</code> is <code>delivered</code> (your server answered 2xx), <code>retrying</code> (a try failed, more are coming), <code>failed</code> (all four tries failed; see <code>http_status</code> and <code>error</code>) or <code>pending</code>.</li>
            <li><b><code>links.raw</code></b> appears for caught emails: the call that downloads the original <code>.eml</code> (see <a href="#/docs/files">Full email and attachments</a>).</li>
            <li><b>No email content</b> is in the log: only the subject, sender, a 300-character snippet, attachment names and scores. The log is kept until you delete the watch.</li>
            <li>Per-watch delivery history is also available at <code>GET /v1/watches/{'{id}'}/deliveries</code>.</li>
          </ul>
        </section>

        <section id="classify">

          <h2>Test a message</h2>
          <E m="POST" p="/v1/classify" />
          <p>Check a piece of email text against any what without connecting a mailbox. Nothing is saved. Use it to choose thresholds and to try custom questions on your own emails.</p>
          <Code tabs={{
            curl: `curl -X POST {{BASE}}/v1/classify \\
  -H "Authorization: Bearer $WATCHFOX_KEY" -H "Content-Type: application/json" \\
  -d '{"subject": "Invoice INV-77",
       "body": "Please find attached invoice INV-77 for $1,200, due in 30 days.",
       "what": ["enquiry", "invoice"]}'`,
            python: `r = requests.post(f"{BASE}/v1/classify", headers=H, json={
    "subject": "Invoice INV-77",
    "body": "Please find attached invoice INV-77 for $1,200, due in 30 days.",
    "what": ["enquiry", "invoice"],
}).json()
print(r["matched"])                       # ['Invoices']`,
            node: `const r = await post("/v1/classify", {
  subject: "Invoice INV-77",
  body: "Please find attached invoice INV-77 for $1,200, due in 30 days.",
  what: ["enquiry", "invoice"],
});
console.log(r.matched);                   // ["Invoices"]`,
          }} />
          <Code label="response">{`{
  "matched": ["Invoices"],
  "results": {
    "enquiry": { "label": "Enquiries", "score": 0.26, "threshold": 0.38, "exclude_score": 0.21, "matched": false, "excluded": false },
    "invoice": { "label": "Invoices",  "score": 0.68, "threshold": 0.5,  "exclude_score": null, "matched": true,  "excluded": false }
  }
}`}</Code>
          <p><code>excluded: true</code> means the score reached the threshold but an exclusion cancelled it; <code>reason</code> says which kind (a subject rule, bulk mail, or an exclusion question). Replies and forwards are handled: for a forward, the text below the “From:” line is read too.</p>
        </section>

        <section id="plans">
          <h2>Plans and runs</h2>
          <p>Every account starts on the free <b>Kit</b> plan. A <b>run</b> is one email checked by one watch. It counts whether or not the email is caught, so ignored emails use runs too. A watch with several types still uses one run per email. Sandbox emails and test calls (<code>POST /v1/classify</code>) count as well.</p>
          <table>
            <thead><tr><th>Plan</th><th>Runs a month</th><th>Mailboxes</th><th>Watches</th><th>Custom types</th><th>Team members</th><th>Price</th></tr></thead>
            <tbody>
              <tr><td><b>Kit</b></td><td>50</td><td>2</td><td>3</td><td>3</td><td>3</td><td>Free</td></tr>
              <tr><td><b>Tracker</b></td><td>5,000</td><td>10</td><td>25</td><td>15</td><td>10</td><td>$29 a month, or $290 a year</td></tr>
              <tr><td><b>Alpha</b></td><td>50,000</td><td>50</td><td>100</td><td>50</td><td>50</td><td>$149 a month, or $1,490 a year</td></tr>
            </tbody>
          </table>
          <p><b>Fair use on the free plan.</b> A real mailbox (Gmail, Microsoft 365 or IMAP) gets one free allowance of 50 runs a month in total, however many free accounts it is connected to, and free accounts also share a daily cap on test calls and sandbox emails per network address. New accounts are limited per network address, and disposable email addresses and address aliases (such as <code>name+1@gmail.com</code>) are not accepted. Paid plans are not affected.</p>
          <p>Annual billing is two months free (17% off). Every plan has every feature; plans differ only in volume and limits. Counts reset on the 1st of each month (UTC).</p>
          <E m="GET" p="/v1/usage" />
          <Code label="response">{`{ "plan": "kit", "plan_name": "Kit", "runs_used": 12, "runs_limit": 50, "runs_remaining": 38, "resets_at": 1793491200 }`}</Code>
          <p><b>When the runs are used up</b>, WatchFox stops checking new mail for that account. Emails in a connected mailbox are not marked as seen, so they are checked after you upgrade or when the month resets; nothing in a mailbox is lost. A sandbox email sent while over the limit is refused with <code>429</code>. <code>POST /v1/classify</code> returns <code>429</code>. The dashboard warns at 80% and again at 100%.</p>
          <p><code>GET /v1/plans</code> lists the plans. The organization owner upgrades in Settings: choose a plan, enter the card in Helcim's secure form, and the plan changes at once. Plans are billed in US dollars, monthly or yearly, until cancelled. The owner can update the card or cancel in Settings; a cancelled plan stays until the end of the paid period. If a renewal fails, the plan stays for a short grace period before returning to Kit. Card numbers go to Helcim and never reach WatchFox.</p>
        </section>

        <section id="team">
          <h2>Team and sign-in</h2>
          <p>There are no passwords. People sign in with <b>GitHub, Google or Microsoft</b>, and the first sign-in creates the account. An account has <b>one organization</b>. Its mailboxes, watches, types, keys, logs and plan belong to the organization, and every team member works on the same data. The person who signed up is the <b>owner</b>; invited people are <b>developers</b> and can use every feature and option. Only the owner can invite or remove people, rename the organization and change the plan. Settings in the dashboard has all of it.</p>
          <p>Team management is for the signed-in owner, not for API keys: invite and remove people in <b>Settings</b> in the dashboard. A key can read the plan and its limits (<code>GET /v1/plan</code>), nothing more.</p>
          <p>The invitation is a link that works for 7 days and once. The person opens it, signs in with GitHub, Google or Microsoft, and joins your organization. They can use any account that is not already set up with WatchFox, because a person can belong to only one organization. When someone is removed, the API keys they created stop working.</p>
          <p>Microsoft sign-in does not let WatchFox confirm that an email address belongs to the person, so it never takes over an existing account that has the same email. If you meet that message, sign in with the way you used before.</p>
        </section>

        <section id="errors">
          <h2>Errors and limits</h2>
          <p>Errors are JSON with a human-readable message: <code>{`{"detail": "No such mailbox. Connect one first."}`}</code></p>
          <table>
            <thead><tr><th>Status</th><th>Meaning</th></tr></thead>
            <tbody>
              <tr><td>400</td><td>Something in the request is wrong. The message says what.</td></tr>
              <tr><td>401</td><td>Missing or invalid API key.</td></tr>
              <tr><td>403</td><td>Only the organization owner can do that.</td></tr>
              <tr><td>404</td><td>No such mailbox, watch or key in your account, or an email that is gone or never matched.</td></tr>
              <tr><td>409</td><td>The mailbox needs the person to connect again.</td></tr>
              <tr><td>413</td><td>The email is larger than the 100 MB download limit.</td></tr>
              <tr><td>429</td><td>Rate limit reached (wait for <code>Retry-After</code> seconds), or this month's runs are used up (see Plans and runs).</td></tr>
              <tr><td>503</td><td>Temporarily unavailable (warming up, or the provider is not enabled). Retry after a moment.</td></tr>
            </tbody>
          </table>
          <h3>Limits</h3>
          <table>
            <thead><tr><th>What</th><th>Limit</th></tr></thead>
            <tbody>
              <tr><td>Any request made with an API key</td><td>1,200 per minute per account</td></tr>
              <tr><td>Test calls (<code>/v1/classify</code>)</td><td>120 per minute per account</td></tr>
              <tr><td>Sandbox emails</td><td>60 per minute; 200 stored per sandbox mailbox; 5 MB and 5 attachments each</td></tr>
              <tr><td>Webhook test (<code>POST /v1/watches/{'{id}'}/test</code>)</td><td>20 per minute</td></tr>
              <tr><td>Tuning a type (<code>/v1/types/tune</code>, retune)</td><td>12 per hour</td></tr>
              <tr><td>AI assistant (<code>/v1/types/suggest</code>, <code>/examples</code>)</td><td>40 per hour</td></tr>
              <tr><td>Log export</td><td>6 per minute</td></tr>
              <tr><td>Mailboxes, watches, custom types, team members, runs a month</td><td>by plan, see Plans and runs</td></tr>
              <tr><td>API keys per account</td><td>10</td></tr>
              <tr><td>Email text read per message</td><td>first 3,000 characters</td></tr>
              <tr><td>Size of one downloaded email</td><td>100 MB</td></tr>
              <tr><td>Content inside one webhook (<code>.eml</code> and files)</td><td>5 MB</td></tr>
              <tr><td>Downloads</td><td>120 per minute per account</td></tr>
            </tbody>
          </table>
          <p>Every limit is checked on the server, so the API and the dashboard behave the same. Going over a count limit returns <code>400</code> with a message naming the limit; going over a rate limit or the monthly runs returns <code>429</code> with <code>Retry-After</code>. Lowering your plan never deletes anything: existing items keep working, and only creating new ones is blocked until you are under the limit. Limits may change as WatchFox grows.</p>
          <h3>Also available</h3>
          <p><code>GET /v1/health</code> (open status), <code>GET /v1/providers</code> (which mailbox kinds this server can connect), <code>GET /v1/types/kinds</code> (everyday email kinds a tuned type can also count as yes), <code>GET /v1/plans</code> and <code>GET /v1/plan</code> (plans, and your limits).</p>
        </section>

        <section id="faq">
          <h2>Frequently asked questions</h2>
          <p>The questions people ask most, in plain words. If yours is not here, write to <a href="mailto:hello@watchfox.run">hello@watchfox.run</a>.</p>
          <Faq where="docs" grouped />
        </section>

        <section id="accuracy">
          <h2>Accuracy</h2>
          <p>WatchFox judges emails automatically, so it will sometimes be wrong. Treat a catch as a strong hint, and build your workflow so a person can correct it.</p>
          <ul>
            <li><b>Enquiries</b> are the only catch tuned on real emails. In testing it caught all 16 of 16 real RFQ and RFP emails supplied, including forwards where the request sits below the “From:” line. The weakest scored right at the threshold, so the margin is thin. On a mixed set it caught 15 of 19 real requests with 1 false alarm; the misses were very short requests (“Pls quote. Survey attached.”).</li>
            <li><b>Not yet measured:</b> how often it flags emails that are not enquiries in a real mailbox. The non-enquiry emails in our testing were written by hand.</li>
            <li><b>Support tickets, purchase orders, job applications and claims</b> use starter settings. Try them on your own mail before you rely on them.</li>
            <li>The hardest mistakes are emails that <b>mention a quote but are not a request for one</b>: a supplier sending you a quotation, a customer replying about a quote you sent, or a purchase order. The model cannot reliably tell who is asking whom. The built-in enquiry catch ignores most of these by their subject line, but not all. In our own test of about 100 varied emails, 4 of 69 non-requests were still caught after the rules, and 1 of 31 real requests was missed. That test set was partly written by us, so expect a few more mistakes on real mail.</li>
            <li>English works best. Other languages sometimes work, with more errors.</li>
            <li>Only the first 3,000 characters of each email are read. Attachments are not read; if the real request is inside an attached PDF and the email just says “see attached”, there may be too little to go on.</li>
          </ul>
          <div className="note">Scores are not probabilities. Choose thresholds by looking at scores for your own emails with <code>/v1/classify</code>.</div>
        </section>

        <section id="security">
          <h2>Security and privacy</h2>
          <ul>
            <li><b>Read-only.</b> Microsoft 365 access uses the read-only <code>Mail.Read</code> permission, and Gmail and IMAP mailboxes are opened read-only. WatchFox never sends, moves, deletes or marks mail. A Gmail app password is technically a full-access credential, so WatchFox enforces read-only itself; the person can remove the password in their Google account at any time.</li>
            <li><b>Credentials</b> (OAuth tokens and IMAP passwords) are encrypted at rest. API keys and passwords are stored only as hashes.</li>
            <li><b>What we keep per email:</b> subject, sender, a 300-character snippet, attachment file names, and the scores. <b>Zero retention of content:</b> full emails, <code>.eml</code> files and attachments are never stored. When you ask for them they are read from the mailbox at that moment, handed to you, and not kept.</li>
            <li><b>Processing.</b> Email text is checked by WatchFox's own agent, on WatchFox's infrastructure. If that agent is unavailable, WatchFox can switch to a backup agent run by an outside provider; while that happens, the text sent for checking (subject, sender and up to 3,000 characters of the body) goes to that provider, which does not promise zero retention. Nothing is sent there while the main agent is working.</li>
            <li><b>Deleting.</b> Deleting a watch deletes its stored emails and delivery logs. Disconnecting a mailbox deletes its credentials and its watches.</li>
            <li><b>Webhooks</b> are signed, must use https, and cannot target private network addresses.</li>
          </ul>
        </section>

        <section id="roadmap">
          <h2>Roadmap</h2>
          <p>Email is live. The same idea, the same API, comes next for other places documents arrive:</p>
          <table>
            <thead><tr><th>Watch</th><th>How</th><th>Status</th></tr></thead>
            <tbody>
              <tr><td>Email</td><td>Gmail (app password), Microsoft 365 (push), IMAP, sandbox</td><td>Live</td></tr>
              <tr><td>Folders</td><td>A small agent you install next to the folder</td><td>Next</td></tr>
              <tr><td>FTP</td><td>The same agent, watching drop zones</td><td>Next</td></tr>
              <tr><td>Databases</td><td>New rows and records</td><td>Planned</td></tr>
              <tr><td>Your own API</td><td>Send any document, get a verdict</td><td>Planned</td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>
  )
}
