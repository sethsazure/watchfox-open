"""Documentation for AI coding agents: /llms.txt, /llms-full.txt and a cleaned-up OpenAPI document.

The guide is written by hand (guide.md). The endpoint reference at its end is built from the live API, so it cannot drift from
what the server really accepts."""
import re
from pathlib import Path
from typing import Any, Dict, List

from . import config

HIDDEN = ("/web", "/auth", "/hooks", "/v1/admin", "/v1/billing", "/v1/team", "/v1/org", "/v1/plan/upgrade", "/v1/connect/", "/v1/demo",
          "/v1/openapi.json", "/{path")

# One plain sentence for every public operation, so an agent knows what each is for without reading code.
SUMMARY = {
    "GET /v1/health": "Service status, no key needed: `ok` and `ready`. With a valid key it also reports `version`, `agent` (primary, backup or down) and `queued`.",
    "GET /v1/presets": "The built-in types and your own, with descriptions and thresholds.",
    "GET /v1/providers": "Which mailbox kinds this server can connect (gmail, m365, imap, sandbox).",
    "POST /v1/keys": "Create an API key. The full key is returned once. Max 10 per account.",
    "GET /v1/keys": "List your API keys (prefix, name, last used). Never returns full keys.",
    "DELETE /v1/keys/{kid}": "Revoke an API key immediately.",
    "POST /v1/connections": "Connect a mailbox from your server (imap, gmail with app password, or sandbox). Credentials are verified first.",
    "GET /v1/connections": "List mailboxes with status (`active` or `needs_attention`) and delivery mode.",
    "GET /v1/connections/{cid}": "One mailbox.",
    "DELETE /v1/connections/{cid}": "Disconnect a mailbox; its credentials, watches and records are deleted.",
    "POST /v1/connect-links": "Create a one-time hosted page where your customer connects Gmail or Microsoft 365. Returns `url`; redirect them there. Optional branding: `app_name`, `logo_url`, `accent_color`, `support_email`, `hide_powered_by` (paid plans).",
    "POST /v1/connections/{cid}/sandbox-emails": "Drop a test email (optionally with attachments) into a sandbox mailbox. Uses one run. 429 when runs are used up.",
    "GET /v1/connections/{cid}/emails/{eid}/raw": "Download the original .eml, read live from the mailbox. Only for emails that matched a watch.",
    "GET /v1/connections/{cid}/emails/{eid}/attachments": "List an email's attachments (id, filename, content_type, size, url).",
    "GET /v1/connections/{cid}/emails/{eid}/attachments/{aid}": "Download one attachment as the file itself.",
    "POST /v1/watches": "Create a watch: mailbox + what to catch + optional webhook. Returns `webhook_secret` once. 409 for an exact duplicate.",
    "GET /v1/watches": "List watches.",
    "GET /v1/watches/{wid}": "One watch.",
    "PATCH /v1/watches/{wid}": "Change what a watch catches, its webhook, or what the webhook carries (`include`).",
    "DELETE /v1/watches/{wid}": "Delete a watch and the records it produced.",
    "POST /v1/watches/{wid}/test": "Send one signed sample event to the watch's webhook and report what your server answered.",
    "GET /v1/watches/{wid}/deliveries": "Recent webhook deliveries with status, HTTP code and attempts.",
    "GET /v1/matches": "Emails that matched, newest first. Filters: what, watch_id, limit (max 1000).",
    "GET /v1/emails": "Every email that was checked, matched or not. Filters: watch_id, limit.",
    "GET /v1/events": "Server-Sent Events stream of every checked email, same shape as a webhook event. Read it from a server.",
    "GET /v1/log": "The 90-day activity log with delivery status. Page with `before` = previous `next_before`.",
    "GET /v1/log/export": "The activity log as CSV (same filters as /v1/log). 6 per minute.",
    "POST /v1/classify": "Score text against any types without a mailbox. Nothing is saved. Uses one run. 120 per minute.",
    "GET /v1/types": "Your custom types.",
    "POST /v1/types": "Save a tuned type (use the JSON returned by /v1/types/tune). Counts against the plan's custom-type limit.",
    "GET /v1/types/kinds": "Everyday email kinds that can be marked as also counting as yes when tuning.",
    "POST /v1/types/tune": "Draft a type's settings from your examples (about 30 s). Needs >= 3 positives. Nothing is saved. 12 per hour.",
    "POST /v1/types/suggest": "AI assistant: one sentence in, a drafted type out. 501 if the assistant is not set up.",
    "POST /v1/types/examples": "AI assistant: more invented example emails (matches and look-alikes) for a type.",
    "GET /v1/types/{tid}": "A custom type with the examples it was tuned on.",
    "PUT /v1/types/{tid}": "Replace a type's settings (also applies a re-tune to a built-in type).",
    "DELETE /v1/types/{tid}": "Delete a custom type, or reset a built-in type to its default settings.",
    "POST /v1/feedback": "Flag a checked email: kind `wrong` (should not have matched) or `missed` (should have). Stores that one email's subject and up to 3,000 characters for re-tuning.",
    "GET /v1/corrections": "Types that have flagged emails waiting to teach them.",
    "DELETE /v1/corrections/{what}": "Forget the flagged emails for a type.",
    "POST /v1/corrections/{what}/retune": "Draft improved settings using the flagged emails. Review, then apply with PUT /v1/types/{what}. 12 per hour.",
    "GET /v1/plans": "The plans, their limits and prices.",
    "GET /v1/plan": "Your plan, runs used/remaining, reset time and count limits.",
    "GET /v1/usage": "Runs used this month and when the count resets.",
}


def _base() -> str:
    return config.BASE_URL.rstrip("/")


def guide() -> str:
    return (Path(__file__).parent / "guide.md").read_text(encoding="utf-8").replace("{{BASE}}", _base())


def _deref(spec: Dict[str, Any], node: Any, depth: int = 0) -> Any:
    if isinstance(node, dict) and "$ref" in node and depth < 6:
        return _deref(spec, spec["components"]["schemas"][node["$ref"].split("/")[-1]], depth + 1)
    return node


def _type(spec: Dict[str, Any], sch: Dict[str, Any]) -> str:
    sch = _deref(spec, sch)
    if "anyOf" in sch:
        return " | ".join(sorted({_type(spec, s) for s in sch["anyOf"] if s.get("type") != "null"})) or "any"
    t = sch.get("type", "object")
    if t == "array":
        return f"list of {_type(spec, sch.get('items', {}))}"
    return t


def public_paths(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {p: ops for p, ops in spec["paths"].items() if not p.startswith(HIDDEN)}


def openapi(app) -> Dict[str, Any]:
    """The OpenAPI document with only the public API, plain-language descriptions and the key scheme."""
    spec = app.openapi_orig()
    spec = dict(spec)
    spec["info"] = {**spec.get("info", {}), "title": "WatchFox API",
                    "description": "Watches mailboxes and calls your webhook when an email matches what you asked for. "
                                   f"Full guide for developers and AI agents: {_base()}/llms-full.txt"}
    spec["servers"] = [{"url": _base()}]
    paths = public_paths(spec)
    for p, ops in paths.items():
        for m, op in ops.items():
            line = SUMMARY.get(f"{m.upper()} {p}")
            if line:
                op["summary"] = line.split(". ")[0].rstrip(".")
                op["description"] = line
            op["operationId"] = re.sub(r"[^a-zA-Z0-9]+", "_", f"{m}_{p}").strip("_")
            if p not in ("/v1/health", "/v1/plans", "/v1/presets", "/v1/providers"):
                op["security"] = [{"bearerAuth": []}]
    spec["paths"] = paths
    spec.setdefault("components", {})["securitySchemes"] = {"bearerAuth": {"type": "http", "scheme": "bearer", "description": "An API key from the dashboard: Authorization: Bearer wf_live_..."}}
    return spec


def reference(spec: Dict[str, Any]) -> str:
    out: List[str] = ["", "## Endpoint reference (generated from the live API)", "",
                      "All paths are relative to " + _base() + ". Send `Authorization: Bearer KEY` unless marked open.", ""]
    for p, ops in sorted(public_paths(spec).items()):
        for m, op in ops.items():
            open_ = p in ("/v1/health", "/v1/plans", "/v1/presets", "/v1/providers")
            out.append(f"### {m.upper()} {p}{' (no key needed)' if open_ else ''}")
            out.append(op.get("description") or op.get("summary") or "")
            params = [_deref(spec, x) for x in op.get("parameters", [])]
            if params:
                out.append("Parameters: " + ", ".join(f"`{x['name']}` ({x['in']}, {_type(spec, x.get('schema', {}))}{', required' if x.get('required') else ''})" for x in params))
            body = ((op.get("requestBody") or {}).get("content") or {}).get("application/json", {}).get("schema")
            if body:
                sch = _deref(spec, body)
                req = set(sch.get("required", []))
                fields = [f"`{k}` ({_type(spec, v)}{', required' if k in req else ''}{', default ' + repr(_deref(spec, v)['default']) if 'default' in _deref(spec, v) and _deref(spec, v)['default'] not in ('', [], {}) else ''})"
                          for k, v in sch.get("properties", {}).items()]
                if fields:
                    out.append("JSON body: " + ", ".join(fields))
            out.append("")
    return "\n".join(out)


def faq_text() -> str:
    """The questions people ask, from the same file that the website shows (web/public/faq.json)."""
    import json
    for path in (config.WEB_DIST / "faq.json", Path(__file__).resolve().parents[1] / "web" / "public" / "faq.json"):
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            continue
    else:
        return ""
    base, out, group = _base(), ["", "## Frequently asked questions", ""], ""
    for f in items:
        if f["group"] != group:
            group = f["group"]
            out += [f"### {group}", ""]
        a = re.sub(r"\[([^\]]+)\]\((/[^)]*|#/[^)]*)\)", lambda m: f"{m.group(1)} ({base}{m.group(2)})", f["a"])
        out += [f"**{f['q']}**", a, ""]
    return "\n".join(out)


def llms_full(app) -> str:
    return guide() + "\n" + faq_text() + "\n" + reference(openapi(app))


def llms_txt() -> str:
    b = _base()
    return f"""# WatchFox

> WatchFox is a hosted API that watches mailboxes (Gmail, Microsoft 365, IMAP) and calls your server with a signed webhook when an
> email matches what you asked for: enquiries, invoices, support requests, purchase orders, job applications, claims, or types you
> define and tune. Email content is never retained. REST + JSON, bearer-token auth.

## Start here
- [Complete guide for developers and AI agents]({b}/llms-full.txt): auth, concepts, quickstart, webhooks and signature verification, downloads, limits, errors, endpoint reference. One file, everything needed.
- [OpenAPI 3 document]({b}/v1/openapi.json): machine-readable endpoints and request bodies.

## Also useful
- [Human documentation]({b}/#/docs)
- [Pricing and plan limits]({b}/#/pricing)
- [Service health]({b}/v1/health): open, no key.

## Key facts
- Authentication: `Authorization: Bearer wf_live_...`. A person creates the first key in the dashboard; sign-in is GitHub, Google or Microsoft only (no passwords).
- Try everything without real mail: POST /v1/connections with {{"provider":"sandbox"}}, then POST /v1/connections/{{id}}/sandbox-emails.
- Webhooks are signed: X-WatchFox-Signature = t=<unix>,v1=HMAC_SHA256(secret, "<t>." + raw body).
- Plan limits (runs per month, mailboxes, watches, custom types, team members) apply identically to the API and the dashboard; over a limit you get 400 or 429 with a clear message.
"""
