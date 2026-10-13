# WatchFox

**An email-watching API.** Tell it which mailbox to watch and what to catch (an enquiry, an invoice, a support ticket, a purchase order,
a job application, a claim, or a type you define). When a matching email arrives, WatchFox calls your server with a **signed webhook**.
Your app never has to read an inbox or train a classifier.

Live service: **https://watchfox.run** (free plan, 50 runs a month; the "try it" box on the home page runs the real checker without signing up).

- **Open-weights decision model, self-hosted.** Emails are judged by [`convaiinnovations/laya-typed-decisions`](https://huggingface.co/convaiinnovations/laya-typed-decisions),
  a small (421M parameter) encoder that answers typed, calibrated questions, served by the open-source
  [`laya-serve`](https://github.com/NandhaKishorM/laya). It is not a chat model: no tokens, no per-email API bill, no invented answers, and a
  score you can put a threshold on. On a single 12 GB consumer GPU it makes roughly 400 decisions per second when requests are batched.
- **Zero email retention.** WatchFox keeps only the subject, sender, a 300-character snippet, attachment names and scores (for 90 days, with CSV
  export). The full text, the `.eml` and attachments are read from the mailbox at the moment you ask, streamed to you, and forgotten.
- **Types that tune themselves.** Describe a new type and give examples; WatchFox tries several wordings of the question on your examples
  and a built-in set of look-alikes, picks the one that separates them best, sets the threshold and adds exclusions. Press "wrong catch" on a
  real email and it re-tunes from that mistake. An optional AI assistant can draft the examples, and is never used when mail is checked.
- **Built for the people who sell it on.** A hosted "connect your mailbox" page that carries your logo, colour and name, signed webhooks with
  retries, team organizations, plans with run metering enforced identically for the dashboard and the API, an admin console, and docs that
  coding agents can read (`/llms.txt`, `/llms-full.txt`, an OpenAPI file generated from the live API).

```
 your customer's mailbox            WatchFox (FastAPI, PostgreSQL)                    the agent (private)
 Microsoft 365 (push) ──┐         ┌───────────────────────────────┐                ┌──────────────────────┐
 Gmail / IMAP (poll) ───┼──mail──▶│ watches ─▶ engine ── batch ───┼─ private net ─▶│ laya-serve, open     │
 sandbox (testing) ─────┘         │               │   (up to 64)  │                │ weights, own GPU     │
                                  │               ▼               │                └──────────────────────┘
                                  │ matches ─▶ signed webhook ────┼──▶ your server (HMAC-SHA256, retries)
                                  │ log (subject, score, 90 days) │
                                  └───────────────────────────────┘     full email / attachments: read live from the mailbox on request
```

## Quick start (no GPU needed)

Requirements: Python 3.10+, Node 20+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
pip install --no-deps "laya>=0.4.1"            # only the small email helpers; the model is not installed
(cd web && npm ci && npm run build)

python examples/stub_agent.py &                # a stand-in for the agent: keyword rules, not the model
LAYA_URL=http://127.0.0.1:9000 LAYA_API_KEY=local WATCHFOX_PASSWORD_LOGIN=1 \
  WATCHFOX_ALLOW_INSECURE_WEBHOOKS=1 watchfox serve          # http://localhost:8787
```

Open http://localhost:8787, create an account (locally the confirmation link appears on screen), make an API key, add a **sandbox** mailbox,
create a watch with your webhook URL, and send a sample email into the sandbox. `examples/quickstart.py` does the same from code and
`examples/webhook_receiver.py` shows how to verify the signature. The documentation is at `/#/docs`.

### Using the real model
Run [`laya-serve`](https://github.com/NandhaKishorM/laya) with the `typed-decisions` checkpoint on a GPU machine (a 12 GB card is plenty) and point
WatchFox at it:

```bash
export LAYA_URL=https://your-gpu-host:9443        # for example a private network address (Tailscale works well)
export LAYA_API_KEY=...                           # whatever bearer key your laya-serve is configured with
```

Every request carries `"model": "typed-decisions"`; without it the server may load a different checkpoint. The thresholds shipped for the six
built-in types were measured with the real model: the stand-in agent is only for trying the product. If the main agent is unreachable, WatchFox
can fall back to a hosted backup path (`LAYA_FALLBACK_*` in `.env.example`) and says which path is serving.

## Tests

```bash
pip install -e ".[dev]"
pytest -q                                                      # SQLite
docker run -d --name wf-pg -e POSTGRES_PASSWORD=testpw -p 55432:5432 postgres:16-alpine
WATCHFOX_TEST_PG=postgresql://postgres:testpw@localhost:55432 pytest -q    # PostgreSQL
python tests/load_local.py 100 50              # volume test on PostgreSQL with the agent stubbed: 100 accounts, 5,000 emails
```

The suite covers the API, webhooks, plan limits (checked through API keys too), the sign-in flows, billing, abuse controls, the admin console and
the agent client (with a mocked server). CI runs it on both databases.

## Security

Written down because it was learned the hard way during a pre-launch audit:

- **Client address is taken from the end of `X-Forwarded-For`** (the proxy appends it); trusting the first value lets anyone bypass every per-address limit.
- **No server-side request forgery.** Webhook URLs and mail-server hosts must resolve only to public addresses (private, loopback, link-local and
  carrier-grade-NAT/Tailscale ranges are refused), checked when saved and again when used; redirects are not followed; only the agent client may use the private network.
- **Abuse controls for the free plan:** one free allowance per real mailbox across all accounts, address aliases and disposable domains refused, limits on new accounts per
  address and per hour, shared daily caps for free accounts, and a global cap on the public demo.
- Strict response headers and a Content-Security-Policy, request bytes counted as they arrive, validation errors that never echo input, and stored secrets encrypted
  (API keys and webhook secrets are hashed or encrypted).
- A test fails if any public page, the OpenAPI file or `llms.txt` mentions the inference host or model internals, and if the live checking path ever imports the setup assistant.

## Layout

```
watchfox/     server.py (API), runner.py (watchers + webhooks), engine.py (agent client, failover), store.py (SQLite + PostgreSQL),
              abuse.py, admin.py, billing.py, payments.py (Helcim), tuning/ (type tuner), sources/ (Microsoft 365, Gmail, IMAP)
web/          React + Vite: landing page, docs, FAQ, sign-in, dashboard, admin console
examples/     quickstart (Python, Node), webhook receiver, stand-in agent
tests/        pytest suite, load test, end-to-end script
tools/        reset_database.py (count, back up, then empty a PostgreSQL database)
DEPLOY.md     how it is deployed (containers on a cloud platform, PostgreSQL, a private network to the agent)
```

## Notes

- The sample emails in the tuning library and the tests are fictional: the names, companies and addresses are made up (reserved `.example` domains).
- Payments use Helcim; a card number never reaches this code. Sign-in is GitHub, Google and Microsoft only (a password switch exists for tests).
- Maturity: the first release covers email only. See the "Accuracy" section of the docs: the built-in types are tuned on a small set, so check them on your own mail.

## License

MIT. See [LICENSE](LICENSE). The open-weights model and `laya-serve` have their own licenses; check them before deploying.
