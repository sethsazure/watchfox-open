// Same as quickstart.py in Node 18+.   WATCHFOX_URL=... WATCHFOX_KEY=wf_live_... node quickstart.mjs
const BASE = process.env.WATCHFOX_URL ?? "http://localhost:8787";
const H = { Authorization: `Bearer ${process.env.WATCHFOX_KEY}`, "Content-Type": "application/json" };
const call = (method, path, body) =>
  fetch(BASE + path, { method, headers: H, body: body && JSON.stringify(body) }).then((r) => r.json());

const box = await call("POST", "/v1/connections", { provider: "sandbox" });
const watch = await call("POST", "/v1/watches", { connection_id: box.id, what: "enquiry" });
await call("POST", `/v1/connections/${box.id}/sandbox-emails`, {
  subject: "Pricing", body: "Could you quote us for 40 licences?", sender: "priya@acme.com",
});
await new Promise((r) => setTimeout(r, 6000));
console.log((await call("GET", `/v1/matches?watch_id=${watch.id}`)).map((m) => m.email.subject));
