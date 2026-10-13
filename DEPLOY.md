# Deploying WatchFox to Azure

> This file describes the deployment pattern used for the hosted service (containers on Azure, PostgreSQL, a private network to the agent). The production pipeline lives in a separate private repository; the workflow in this repository only runs the tests. Replace every `<placeholder>` with your own value.

Flow: push to `main` on GitHub → Actions runs the tests (SQLite and PostgreSQL) → builds the image → publishes it to
`ghcr.io/<owner>/watchfox` → points the Azure web app at the new image and restarts it.

## What runs where
| Piece | Where |
|---|---|
| Website, API, watches | Azure App Service for Containers, `<webapp>` (Linux B1, Canada Central, **one instance only**) |
| Database | Azure Database for PostgreSQL Flexible Server `<pg-server>` (Burstable B1ms, SSL required) |
| Checking agent | Your GPU server, reached over Tailscale. The container joins the tailnet in userspace mode with `TS_AUTHKEY` and sends only agent calls through a local SOCKS proxy |
| Backup agent | A third-party service, used only while the main agent is unreachable |
| Image | GitHub Container Registry (private) |

No model, secret or local data is in the repository or in the image.

## One instance only
WatchFox polls mailboxes, keeps in-memory queues and runs the log clean-up inside the app. Two instances would check every email twice.
Keep the App Service plan at one instance (do not turn on scale-out or autoscale).

## App settings (Azure portal → web app → Environment variables)
`WATCHFOX_URL` (https address users see), `WATCHFOX_SECRET` (never change it: it decrypts stored mailbox credentials and signs sessions),
`DATABASE_URL` (`postgresql://user:pass@host/watchfox?sslmode=require`), `LAYA_URL`, `LAYA_API_KEY`, `TS_AUTHKEY` (+ `TS_HOSTNAME`),
`LAYA_FALLBACK_KEY` (optional backup), `OPENAI_API_KEY` (optional AI assistant), `MS_CLIENT_ID/SECRET/TENANT`, `GITHUB_CLIENT_ID/SECRET`,
`GOOGLE_CLIENT_ID/SECRET`, `SMTP_HOST/PORT/USER/PASSWORD/FROM` (optional: emails invitations),
`WEBSITES_PORT=8787`. See `.env.example`.

## Tailscale on Azure App Service (what we learned)
- The container joins the tailnet in userspace mode (no special privileges): `TS_AUTHKEY` must be a **reusable, ephemeral** key tagged `tag:app-prod`. Keys expire (90 days by default): create a new one and replace the `TS_AUTHKEY` app setting before then.
- App Service shows only a link-local address on `eth0`, so Tailscale thinks there is no network and never signs in. The entrypoint sets
  `TS_ASSUME_NETWORK_UP_FOR_TEST=1` to get past that. Without it `/v1/health` stays on `"agent":"backup"`.
- Check it: `curl -H "Authorization: Bearer YOUR_KEY" https://<app>.azurewebsites.net/v1/health` should say `"agent":"primary"` (without a key it only says `ok` and `ready`). If it says `backup`, the container log shows `[tailscale]` / `[tailscaled]` lines explaining why.
- Logs: `az webapp log tail -g <resource-group> -n <webapp>`.

## Domain: your-domain.example (Cloudflare DNS, Azure certificate)
Records in Cloudflare, all **DNS only** (grey cloud): `A @ -> <app-ip>`, `CNAME www -> <webapp>.azurewebsites.net`,
`TXT asuid` and `TXT asuid.www` (Azure's domain verification id: `az webapp show -g <resource-group> -n <webapp> --query customDomainVerificationId`).
Azure issued free managed certificates for `your-domain.example` and `www.your-domain.example` and renews them itself; that only works while the records stay DNS only.
`WATCHFOX_URL=https://your-domain.example`; the app redirects `www` to the main address. If the A record's IP ever changes: `az webapp config hostname get-external-ip`.

## Sign-in and email
People sign in with GitHub, Google or Microsoft only (`WATCHFOX_PASSWORD_LOGIN` is off in production), so there are no passwords, confirmations or resets.
Email is optional: the organization owner can always copy an invitation link (and fetch it again later). To email invitations, either use Cloudflare Email Service (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_EMAIL_TOKEN`, `EMAIL_FROM_ADDRESS`; Workers Paid plan, domain onboarded under Email Sending) or set `SMTP_*`.

## Payments (Helcim)
Subscriptions are charged by Helcim: the owner pays the first period in HelcimPay.js (a real purchase that also saves the card; the card never reaches WatchFox), the Helcim subscription then starts at the first renewal date, and WatchFox saves the Helcim customer code and
subscription id, and a job every six hours checks Helcim for failed renewals (7-day grace period, then back to Kit). Setup: `HELCIM_API_TOKEN` app setting, then
`watchfox helcim-setup` once (creates four plans in Helcim: Tracker and Alpha, monthly and yearly, in USD on the active USD terminal). The Helcim API Access
Configuration must list `your-domain.example` for HelcimPay.js. Try the flow cheaply with `HELCIM_TEST_EMAILS` + `HELCIM_TEST_AMOUNT`. Prices are in `billing.py`; if you
change them, update the matching Helcim plans too. Tax (GST/HST) is not added: plans are created without tax.

## After the first deploy
1. Register the sign-in redirect addresses with each provider, using the real URL (`https://your-domain.example`):
   `<WATCHFOX_URL>/auth/login/github/callback`, `/auth/login/google/callback`, `/auth/login/microsoft/callback`.
2. Microsoft mailbox connecting also uses `/auth/m365/callback` and `/auth/m365/admin-done`.
3. Tailscale: the node joins as `tag:app-prod`, which your access rule already lets reach the agent server.
4. Optional: custom domain and certificate (App Service → Custom domains), then change `WATCHFOX_URL` and the redirect addresses.

## Moving existing data
`watchfox copy-db --from ~/.watchfox/watchfox.db --to "postgresql://…"` copies every table. Stored credentials only open with the
`WATCHFOX_SECRET` of the machine that wrote them. Starting production with fresh accounts is usually simpler.

## Local development
Without `DATABASE_URL` the app uses a SQLite file. To run the tests on PostgreSQL:
`docker run -d -e POSTGRES_PASSWORD=testpw -p 55432:5432 postgres:16-alpine` then
`WATCHFOX_TEST_PG=postgresql://postgres:testpw@localhost:55432 pytest`.
