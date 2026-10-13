# WatchFox API guide (for developers and AI coding agents)

WatchFox watches mailboxes for you. You say what to catch (enquiries, invoices, support requests, purchase orders,
job applications, claims, or a type you define), and WatchFox calls your server with a signed webhook when an email
matches. It is a plain JSON-over-HTTPS REST API. Base URL: `{{BASE}}`

If you are an AI agent building an integration: read "The 10-minute path" and "Rules that prevent mistakes", then use
the endpoint reference at the end (or `{{BASE}}/v1/openapi.json`). Everything needed is in this one file.

## Machine-readable resources

- This guide as plain text: `{{BASE}}/llms-full.txt` (index: `{{BASE}}/llms.txt`)
- OpenAPI 3 document: `{{BASE}}/v1/openapi.json` (also `{{BASE}}/openapi.json`)
- Human docs: `{{BASE}}/#/docs`
- Health check, no key needed: `GET {{BASE}}/v1/health`

## Authentication

Every API call sends a key: `Authorization: Bearer wf_live_...`
- A person creates the first key in the dashboard (API keys). After that, `POST /v1/keys` makes more (max 10 per account).
- The full key is shown once at creation and cannot be recovered. Keep it on your server, never in a browser or app.
- Sign-in to the dashboard is GitHub, Google or Microsoft only. There are no passwords. An agent cannot create an account or
  the first key by itself: ask the human to do that and give you the key in an environment variable such as `WATCHFOX_KEY`.
- API keys cannot manage billing, invite or remove team members, or rename the organization. Those are done by the
  organization owner in the dashboard (Settings).
- Errors are JSON: `{"detail": "human readable message"}`.

## Concepts

| Term | Meaning |
|---|---|
| Mailbox / connection | An inbox WatchFox may read (read-only): `gmail`, `m365`, `imap` or `sandbox`. Ids look like `cn_...`. |
| Watch | A mailbox + what to catch + where to send matches (webhook). Ids `wt_...`. Looks only at mail that arrives after it is created (plus `backfill_days`, 0 to 7). |
| What / type | One yes/no question about an email. Built-in ids: `enquiry`, `invoice`, `support`, `purchase_order`, `job_application`, `claim`. Your own types have ids `type_...`. |
| Match | An email where a type's answer is yes. One email can match several types; each is judged on its own. |
| Score | 0 to 1 per type. Not a probability. A match is score >= that type's threshold. |
| Confidence | The score as a percent relative to the threshold: `high` >= 85, `medium` 65-84, `low` < 65. |
| Run | One email checked by one watch, caught or not. Plans limit runs per month. |
| Event | JSON describing a checked email: `email.matched` (a match) or `email.checked`. |

## The 10-minute path (sandbox, no real mailbox needed)

```bash
export WATCHFOX_KEY=wf_live_...
BASE={{BASE}}

# 1. a pretend mailbox
curl -s -X POST $BASE/v1/connections -H "Authorization: Bearer $WATCHFOX_KEY" \
  -H "Content-Type: application/json" -d '{"provider":"sandbox"}'
# -> {"id":"cn_8f2a1c04b7e9","provider":"sandbox",...}

# 2. a watch (webhook_url is optional; without it read matches with GET /v1/matches)
curl -s -X POST $BASE/v1/watches -H "Authorization: Bearer $WATCHFOX_KEY" \
  -H "Content-Type: application/json" \
  -d '{"connection_id":"cn_8f2a1c04b7e9","what":"enquiry","webhook_url":"https://yourapp.com/hooks/watchfox"}'
# -> {"id":"wt_...","webhook_secret":"whsec_...",...}   SAVE webhook_secret: it is shown only once

# 3. drop an email into the sandbox; it is checked within a couple of seconds
curl -s -X POST $BASE/v1/connections/cn_8f2a1c04b7e9/sandbox-emails -H "Authorization: Bearer $WATCHFOX_KEY" \
  -H "Content-Type: application/json" \
  -d '{"subject":"Pricing","body":"Could you quote us for 40 licences?","sender":"priya@acme.example"}'

# 4. read the result (or wait for your webhook)
curl -s "$BASE/v1/matches?limit=5" -H "Authorization: Bearer $WATCHFOX_KEY"

# 5. send your webhook a sample event to test your endpoint and signature check
curl -s -X POST $BASE/v1/watches/wt_.../test -H "Authorization: Bearer $WATCHFOX_KEY"
```

Test text without any mailbox: `POST /v1/classify` with `{"subject","body","what"}` (uses one run).

## Connecting a real mailbox

1. Hosted page (best for a SaaS: your customer connects their own mailbox, you never see a password):
   `POST /v1/connect-links` body `{"provider":"gmail"|"m365","redirect_url":"https://yourapp.com/done","state":"...","app_name":"Your App","metadata":{...}}`
   returns `{"url": "...", "expires_in": 3600}`. Redirect the customer to `url`. They come back to `redirect_url`
   with `status=connected`, `connection_id`, `email` and your `state` in the query string.
   Branding the page (all optional): `app_name` (your product name), `logo_url` (https, max 400 chars; replaces the WatchFox logo
   in the header), `accent_color` (`#RRGGBB`; colours buttons and links, use a colour dark enough for white text), `support_email`
   (shown as "Need help?"), `hide_powered_by: true` (removes "Powered by WatchFox"; Tracker and Alpha plans only, else 400).
   The page is co-branded, not white-labelled: it is served from `{{BASE}}/connect`, and Microsoft 365's own consent screen names
   WatchFox. To keep the customer entirely inside your own UI use `POST /v1/connections` (Gmail app password or IMAP) from your server;
   Microsoft 365 always needs the redirect. Pass `connection_id` in the body to reconnect an existing mailbox.
2. From your server: `POST /v1/connections`
   - Gmail with an app password: `{"provider":"gmail","user":"x@gmail.com","password":"16-char app password"}`
   - IMAP: `{"provider":"imap","host":"imap.example.com","port":993,"user":"...","password":"...","folder":"INBOX"}`
   - Sandbox: `{"provider":"sandbox"}`
   - Any of these accepts `"metadata": {...}` (under 2 KB), copied into every webhook for that mailbox.
   IMAP hosts must be on a public address and port 993; private addresses are refused with 400.
   Credentials are checked before saving; a wrong one returns 400 with the reason.
3. Microsoft 365 connects only through the hosted page (sign-in), and delivers new mail by push within seconds.
   Gmail and IMAP are checked about once a minute.

A mailbox that needs the person to sign in again has `status: "needs_attention"` and the API returns 409 for downloads;
the webhook event `connection.needs_attention` is sent to watches' webhooks. Send the person a new connect link with
`connection_id` set.

## Watches

`POST /v1/watches`

| Field | Type | Notes |
|---|---|---|
| `connection_id` | string, required | The mailbox. |
| `what` | string, list or object | Preset id, list of ids/definitions, or custom definition(s). Default `"enquiry"`. A `type_...` id works anywhere a preset id does. |
| `webhook_url` | string | https, public address, no redirects. Optional. |
| `webhook_secret` | string | Your own signing secret; generated if you send a URL and no secret. |
| `metadata` | object | Under 2 KB; added to every webhook; wins over the mailbox's metadata. |
| `include` | list | Any of `"body"`, `"eml"`, `"attachments"`. Needs `webhook_url`. |
| `backfill_days` | int 0-7 | Also check mail received this many days before creation. |

- `PATCH /v1/watches/{id}` changes `what`, `webhook_url`, `include`. `DELETE` removes the watch and its records.
- Creating an exact duplicate (same mailbox, same types, same webhook) returns 409.
- `GET /v1/watches/{id}/deliveries` shows webhook attempts: `delivered`, `retrying`, `failed`, HTTP code, attempts.

Watch object: `id, connection_id, what[], labels[], webhook_url, active, backfill_days, created, last_checked, last_error`;
`webhook_secret` is returned only by the create response (and when a webhook is first added by PATCH).

## Types: built-in, tuned, and defined by hand

- `GET /v1/presets` lists every built-in type and your own, with description and test results.
- Tuned types (recommended for custom needs):
  1. `POST /v1/types/tune` with `{"label","description","positives":[{"subject","body"}...],"negatives":[...],"treat_as_yes":[kind ids]}`
     (takes about 30 s; needs at least 3 positives; look-alike negatives improve results a lot). Nothing is saved.
  2. `POST /v1/types` with the JSON returned by step 1 (you may edit it first). Returns `{"id":"type_..."}`.
  3. Use `type_...` as `what` in watches and `/v1/classify`.
  - `GET /v1/types`, `GET/PUT/DELETE /v1/types/{id}` manage your types. `GET /v1/types/kinds` lists everyday email kinds that can be marked as "also counts as yes".
  - Optional AI help for setup only (never used to check mail): `POST /v1/types/suggest` `{"sentence":"..."}` drafts name, description and examples; `POST /v1/types/examples` adds more invented examples. These return 501 when the assistant is not set up (and 502 if it fails once); the tune/save flow works without them.
- Teach from mistakes: `POST /v1/feedback` flags a checked email as a wrong catch or a miss; `POST /v1/corrections/{what}/retune` returns a draft to review; apply it with `PUT /v1/types/{what}`. `GET /v1/corrections`, `DELETE /v1/corrections/{what}`.
- Define by hand: pass an object as `what`:
  `{"id":"urgent_repair","label":"Urgent repair","question":"Is this an urgent request for a repair or technician?","threshold":0.5,"exclude_question":"Is this an automated notification?","exclude_threshold":0.5}`
  Optional: `excludes` (list of `{question, threshold}`), `skip_bulk` (bool), `ignore_subject` (list of regexes).
  Write the question so that yes = what you want. Use `/v1/classify` on real samples to choose thresholds; 0.5 is rarely exactly right.

## Webhook event

Your server receives `POST` with JSON (`Content-Type: application/json`):

```json
{
  "id": "evt_5c1e7a90d3b2",
  "type": "email.matched",
  "created": 1791491913,
  "watch_id": "wt_3f9a1c04b7e2",
  "connection_id": "cn_8f2a1c04b7e9",
  "metadata": {"customer_id": "42"},
  "email": {
    "id": "18f3a2c9d4e1b7a0",
    "subject": "RFQ-2026-118: Document management system",
    "from": "Procurement <procurement@gulfbuild.example>",
    "received": 1791491900,
    "snippet": "first 300 characters of the email text",
    "attachments": [{"id": "0", "filename": "RFQ.pdf", "content_type": "application/pdf", "size": 184220,
                     "url": "{{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/attachments/0"}],
    "links": {"raw": "{{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/raw",
              "attachments": "{{BASE}}/v1/connections/cn_8f2a1c04b7e9/emails/18f3a2c9d4e1b7a0/attachments"}
  },
  "matches": [{"what": "enquiry", "label": "Enquiries", "score": 0.64, "confidence": 94, "confidence_level": "high"}],
  "results": {"enquiry": {"label": "Enquiries", "score": 0.64, "threshold": 0.38, "exclude_score": 0.29,
                          "matched": true, "excluded": false, "reason": null}}
}
```

Headers: `X-WatchFox-Event` (`email.matched` | `connection.needs_attention`), `X-WatchFox-Delivery` (`dl_...`),
`X-WatchFox-Signature` (`t=<unix>,v1=<hex>`).

Verify the signature: HMAC-SHA256 with the watch's `webhook_secret` over the exact string `"{t}." + raw request body bytes`;
compare with `v1` in constant time; reject if `t` is more than 300 seconds old. Use the RAW body, not re-serialised JSON.

```python
import hashlib, hmac, time
def verify(secret: str, raw_body: bytes, header: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300:
        return False
    expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

```javascript
import crypto from "node:crypto";
export function verify(secret, rawBody, header) {
  const p = Object.fromEntries(header.split(",").map((s) => s.split("=")));
  if (Math.abs(Date.now() / 1000 - Number(p.t)) > 300) return false;
  const expected = crypto.createHmac("sha256", secret).update(`${p.t}.${rawBody}`).digest("hex");
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(p.v1));
}
```

Delivery: answer any 2xx within 10 seconds. Failures retry up to 4 attempts in total (now, +10 s, +1 min, +5 min).
Redirects are not followed. The same event can arrive twice: de-duplicate on `id`. Answer fast, work afterwards.

## Getting the whole email and attachments

WatchFox keeps NO copy of email text, the `.eml`, or attachments. It reads them from the mailbox when asked.
- Inside the webhook: set `include` on the watch: `body` adds `email.body` (up to 256 KB), `eml` adds `email.eml_base64`,
  `attachments` adds `content_base64` to each attachment. Up to 5 MB together; larger items get an `eml_omitted` /
  `omitted` note and must be downloaded.
- By download (needs your API key, only emails that matched one of your watches, only while the email is still in the mailbox):
  `GET /v1/connections/{cid}/emails/{eid}/raw` returns the `.eml` (`message/rfc822`);
  `GET .../attachments` lists attachments; `GET .../attachments/{aid}` returns one file.
  Up to 100 MB per email (413 above that). 404 if not matched or the email was deleted. 409 if the mailbox needs reconnecting.
  Responses are `Cache-Control: no-store`. Download when the webhook arrives; do not rely on it later.

## Reading results without a webhook

- `GET /v1/matches?what=&watch_id=&limit=` caught emails, newest first (limit up to 1000).
- `GET /v1/emails?watch_id=&limit=` every checked email.
- `GET /v1/events` Server-Sent Events stream of every checked email (same shape as webhooks); read it server-side.
- `GET /v1/log` the 90-day activity log with filters `matched, what, watch_id, connection_id, delivery, q, since, limit (1-200), before`;
  page with `next_before`. `GET /v1/log/export` the same as CSV (`max_rows` up to 500,000).
  Logs older than 90 days are deleted automatically; export what you need to keep. The log has no email content.

## Plans, runs and limits (enforced identically for the dashboard and for API keys)

| Plan | Runs/month | Mailboxes | Watches | Custom types | Team members | Price (USD) |
|---|---|---|---|---|---|---|
| Kit | 50 | 2 | 3 | 3 | 3 | free |
| Tracker | 5,000 | 10 | 25 | 15 | 10 | 29/mo or 290/yr |
| Alpha | 50,000 | 50 | 100 | 50 | 50 | 149/mo or 1,490/yr |

- A run = one email checked by one watch, caught or not. Test calls (`/v1/classify`) and sandbox emails count. Resets on the 1st (UTC).
- Over a count limit (mailboxes, watches, custom types, team seats, 10 API keys): the create call returns 400 with a message naming the limit.
- Out of runs: new mail is not checked (not lost: it is checked after an upgrade or the monthly reset); `POST /v1/classify` and sandbox emails return 429.
- Check where you stand: `GET /v1/usage` and `GET /v1/plan` (adds the count limits and the next plan up).
- If a plan is downgraded, existing items keep working; only creating new ones is blocked until under the limit.
- Fair use on Kit (free): a real mailbox gets 50 free runs a month in total across all free accounts that connect it; free accounts also share a daily cap on `/v1/classify` and sandbox emails per network address. Creating many free accounts does not multiply the allowance.

Rate limits (HTTP 429 with `Retry-After` seconds):
| What | Limit |
|---|---|
| Any API-key request | 1,200 per minute per account |
| `POST /v1/classify` | 120 per minute |
| Downloads (`raw`, attachments) | 120 per minute |
| Sandbox emails | 60 per minute, 200 stored per sandbox mailbox, 5 MB each, 5 attachments |
| Webhook test (`POST /v1/watches/{id}/test`) | 20 per minute |
| Type tuning / retuning | 12 per hour |
| AI assistant (suggest, examples) | 40 per hour |
| Log export | 6 per minute |

## Errors

| Status | Meaning | What to do |
|---|---|---|
| 400 | Bad request or a plan count limit reached; `detail` says which | Fix the request or upgrade |
| 401 | Missing or invalid key | Check the `Authorization` header |
| 403 | Email not verified, or owner-only action | Ask the owner |
| 404 | Not found in your account, or email gone / never matched | Do not retry |
| 409 | Duplicate watch, or mailbox needs reconnecting | Edit existing / send a connect link |
| 413 | Email over 100 MB | Cannot be downloaded |
| 429 | Rate limit or monthly runs used up | Wait `Retry-After`; or upgrade |
| 502 / 503 | Mailbox unreachable, or service warming up | Retry with backoff |

Retry only 429 (after `Retry-After`), 502 and 503. Never blindly retry 4xx. Creating calls are not idempotent: list
first (`GET /v1/watches`, `GET /v1/connections`) before re-creating after a timeout.

## Rules that prevent mistakes

1. Store `webhook_secret` when the watch is created; it is never shown again.
2. Verify the signature over the raw body before trusting a webhook.
3. Time fields are Unix seconds (UTC). Ids are opaque strings; never parse them.
4. A watch ignores mail that arrived before it was created unless `backfill_days` is set.
5. Do not poll faster than needed; use webhooks or `GET /v1/events`.
6. Do not ask for credentials in code: the hosted connect page keeps customer passwords away from your servers.
7. Plan limits are server-enforced; do not try to work around them. Read `GET /v1/plan` and handle 400/429 messages.
8. Email content is not retained. Copy what you need when the webhook arrives.
9. Never put API keys or webhook secrets in client-side code, logs or commits.

## Starter prompt for a coding agent

"Integrate WatchFox. Read {{BASE}}/llms-full.txt first. Use the key in env var WATCHFOX_KEY. Create a sandbox mailbox and
a watch for the types I name, with my webhook URL. Implement a webhook endpoint that verifies X-WatchFox-Signature over
the raw body, answers 204 quickly, de-duplicates on event id, then processes `matches` and, if needed, downloads the
`.eml` / attachments from `email.links` using the same key. Write a test that posts a signed sample event. Prove it works with
`POST /v1/watches/{id}/test` and a sandbox email."
