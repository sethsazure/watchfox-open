"""WatchFox cloud API (/v1), website sign-in (/web), mailbox sign-in (/auth) and the website itself."""
import hashlib
import ipaddress
import json
import os
import queue
import re
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote, urlencode, urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config, limiter, mailfetch, presets, push, store, vault
from . import abuse, accounts, admin, agent_docs, billing, payments
from .auth import COOKIE, current_user, require_run, require_verified, set_session
from .engine import engine
from .items import Item
from .tuning import llm, tuner
from .tuning.library import CATEGORY_INFO
from .runner import runner
from .sources import AuthError, m365, make, registry

app = FastAPI(title="WatchFox API", version=__version__, docs_url=None, redoc_url=None, openapi_url="/v1/openapi.json")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PROVIDERS = [
    {"id": "gmail", "label": "Gmail", "kind": "link"},
    {"id": "m365", "label": "Microsoft 365", "kind": "link"},
    {"id": "imap", "label": "IMAP", "kind": "credentials"},
    {"id": "sandbox", "label": "Sandbox mailbox", "kind": "sandbox"},
]
COMING_SOON = [
    {"id": "folder", "label": "Folders", "note": "Watched by a small agent you install next to the folder."},
    {"id": "ftp", "label": "FTP", "note": "Watched by a small agent you install."},
    {"id": "database", "label": "Databases", "note": "New rows and records."},
    {"id": "api", "label": "Your own API", "note": "Send any document, get a verdict."},
]


MAX_BODY = 40 * 1024 * 1024        # largest request body accepted (sandbox emails with attachments are the biggest)


_csp_cache: Dict[bool, str] = {}


def _csp(framable: bool) -> str:
    """What the website may load. The card form comes from Helcim, so its address is allowed; everything else is our own files.
    The one inline script (it sets the light or dark theme before the page paints) is allowed by its fingerprint."""
    if framable not in _csp_cache:
        hashes = ""
        try:
            html = (config.WEB_DIST / "index.html").read_text(encoding="utf-8")
            import base64
            hashes = "".join(f" 'sha256-{base64.b64encode(hashlib.sha256(m.encode()).digest()).decode()}'"
                             for m in re.findall(r"<script>(.*?)</script>", html, re.S))
        except OSError:
            pass
        helcim = "https://secure.helcim.app https://*.helcim.app https://*.helcim.com"
        _csp_cache[framable] = (f"default-src 'self'; script-src 'self'{hashes} https://secure.helcim.app; style-src 'self' 'unsafe-inline'; "
                                f"img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' {helcim}; frame-src {helcim}; "
                                f"base-uri 'self'; form-action 'self'; object-src 'none'" + ("" if framable else "; frame-ancestors 'none'"))
    return _csp_cache[framable]


class _TooBig(Exception):
    pass


class BodyLimit:
    """Counts the bytes actually received, so a request that hides its size (chunked, or a lying Content-Length) cannot fill memory."""
    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        got, started = 0, False

        async def counted():
            nonlocal got
            msg = await receive()
            if msg["type"] == "http.request":
                got += len(msg.get("body", b""))
                if got > self.limit:
                    raise _TooBig()
            return msg

        async def watched(msg):
            nonlocal started
            started = started or msg["type"] == "http.response.start"
            await send(msg)
        try:
            await self.app(scope, counted, watched)
        except _TooBig:
            if not started:
                await Response("Request body too large.", status_code=413)(scope, receive, send)


app.add_middleware(BodyLimit, limit=MAX_BODY)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    """A 422 that lists what is wrong without echoing the input back (the echo could not be encoded if it held broken text)."""
    errs = [{"loc": [str(x) for x in e.get("loc", [])], "msg": str(e.get("msg", "Invalid value")).encode("utf-8", "replace").decode("utf-8")} for e in exc.errors()]
    return JSONResponse({"detail": errs}, status_code=422)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Refuse oversized bodies and add browser security headers to every response."""
    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return Response("Request body too large.", status_code=413)
    except ValueError:
        return Response("Bad Content-Length.", status_code=400)
    host = (request.headers.get("host") or "").split(":")[0].lower()
    site = urlparse(config.BASE_URL).hostname or ""
    if site and host == "www." + site:                            # www.example.com goes to example.com
        return RedirectResponse(config.BASE_URL + request.url.path + (f"?{request.url.query}" if request.url.query else ""), status_code=301)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    framable = request.url.path.startswith(("/v1/connect", "/connect"))
    if request.url.path.startswith(("/admin", "/v1/admin")):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Cache-Control"] = "no-store"
    if not framable:
        resp.headers.setdefault("X-Frame-Options", "DENY")           # nobody may embed the dashboard in another site
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(self), usb=()")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    if os.getenv("WATCHFOX_CSP", "1") != "0":
        resp.headers.setdefault("Content-Security-Policy", _csp(framable))
    if config.BASE_URL.startswith("https://"):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


def _retention_loop() -> None:
    """Once an hour, delete activity log rows older than the retention period (90 days by default)."""
    n = 0
    while True:
        try:
            store.prune(time.time() - config.LOG_RETENTION_DAYS * 86400)
        except Exception:
            pass
        if n % 6 == 0:                                   # every six hours: compare plans with what Helcim is actually charging
            payments.sync_all()
        n += 1
        time.sleep(3600)


@app.on_event("startup")
def _startup() -> None:
    store.conn()
    threading.Thread(target=engine.load, daemon=True, name="engine").start()
    store.backfill_canon()
    runner.start_saved()
    push.start()
    threading.Thread(target=_retention_loop, daemon=True, name="log-retention").start()


def client_ip(request: Request) -> str:
    return abuse.client_ip(request)


# ---------------------------------------------------------------- authentication (see auth.py)


class Credentials(BaseModel):
    email: str
    password: str


@app.post("/web/signup")
def signup(body: Credentials, request: Request, response: Response):
    if not config.PASSWORD_LOGIN:
        raise HTTPException(404, "Not found.")
    if not config.SIGNUP_OPEN:
        raise HTTPException(403, "Sign-ups are closed.")
    limiter.hit("signup", client_ip(request), config.SIGNUPS_PER_HOUR, 3600)
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    if len(body.password) < 10:
        raise HTTPException(400, "Use a password of at least 10 characters.")
    if len(body.password) > 200 or len(email) > 254:
        raise HTTPException(400, "That email or password is too long.")
    if store.user_by_email(email):
        raise HTTPException(409, "That email already has an account. Sign in instead.")
    uid = store.create_user(email, vault.hash_password(body.password), verified=False, name=f"{email.split('@')[0]}'s organization")
    set_session(response, uid)
    link = accounts.send_verification(uid, email)
    return {"id": uid, "email": email, "verified": False, **({"dev_link": link} if link else {})}


@app.post("/web/login")
def login(body: Credentials, request: Request, response: Response):
    if not config.PASSWORD_LOGIN:
        raise HTTPException(404, "Not found.")
    limiter.hit("login", client_ip(request), 20, 600)
    u = store.user_by_email(body.email.strip()[:254])
    if len(body.password) > 200 or not u or not vault.verify_password(body.password, u["pw_hash"]):
        if not u:
            vault.hash_password("x")             # same work as a real check, so timing does not reveal which emails exist
        raise HTTPException(401, "Wrong email or password.")
    set_session(response, u["id"])
    return {"id": u["id"], "email": u["email"]}


@app.post("/web/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------- meta
app.openapi_orig = app.openapi
_spec: Dict[str, Any] = {}


def _public_spec() -> Dict[str, Any]:
    if not _spec:
        _spec.update(agent_docs.openapi(app))
    return _spec


app.openapi = _public_spec            # the public API only, in plain language (see agent_docs.py)


@app.get("/openapi.json", include_in_schema=False)
def openapi_alias():
    return _public_spec()


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    return PlainTextResponse(agent_docs.llms_txt(), media_type="text/plain; charset=utf-8")


@app.get("/llms-full.txt", include_in_schema=False)
def llms_full_txt():
    return PlainTextResponse(agent_docs.llms_full(app), media_type="text/plain; charset=utf-8")


@app.get("/v1/health")
def health(request: Request):
    """Open to everyone: only whether the service is up. How the checking is currently served is shown to a signed-in caller."""
    out: Dict[str, Any] = {"ok": True, "ready": engine.serving != "down"}
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and store.user_for_key(vault.hash_key(auth[7:].strip())):
        out.update({"version": __version__, "agent": engine.serving, "queued": engine.waiting})
    return out


@app.get("/v1/presets")
def list_presets():
    return [{"id": p["id"], "label": p["label"], "description": p["description"], "threshold": p["threshold"],
             "has_exclude": bool(presets.excludes(p)), "maturity": p["status"]} for p in presets.all_presets().values()]


@app.get("/v1/providers")
def list_providers(user=Depends(current_user)):
    avail = {"gmail": True, "m365": m365.configured(), "imap": True, "sandbox": True}
    return {"providers": [dict(p, available=avail[p["id"]]) for p in PROVIDERS], "coming_soon": COMING_SOON}


# ---------------------------------------------------------------- API keys
class KeyIn(BaseModel):
    name: str = "Default"


@app.get("/v1/keys")
def list_keys(user=Depends(current_user)):
    return store.keys(user["id"])


@app.post("/v1/keys")
def create_key(body: KeyIn, user=Depends(current_user)):
    if len(store.keys(user["id"])) >= 10:
        raise HTTPException(400, "You can have up to 10 API keys. Revoke one first.")
    require_verified(user)
    full, prefix, h = vault.new_api_key()
    kid = store.add_key(user["id"], body.name.strip()[:60] or "Default", prefix, h, user.get("uid", ""))
    return {"id": kid, "name": body.name, "key": full, "note": "Copy this key now. It is shown only once."}


@app.delete("/v1/keys/{kid}")
def delete_key(kid: str, user=Depends(current_user)):
    if not store.revoke_key(user["id"], kid):
        raise HTTPException(404, "No such key.")
    return {"ok": True}


# ---------------------------------------------------------------- mailboxes
class LinkIn(BaseModel):
    provider: str = ""
    redirect_url: str = ""
    state: str = ""
    metadata: Dict[str, Any] = {}
    app_name: str = ""
    connection_id: str = ""     # reconnect an existing mailbox instead of adding a new one
    logo_url: str = ""          # branding for the connect page: your logo (https), accent colour (#RRGGBB), help address, and
    accent_color: str = ""      # hide_powered_by removes the "Powered by WatchFox" line (Tracker and Alpha plans)
    support_email: str = ""
    hide_powered_by: bool = False


def _check_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    if len(json.dumps(meta)) > 2000:
        raise HTTPException(400, "metadata must be smaller than 2 KB.")
    try:
        json.dumps(meta, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(400, "metadata contains characters that are not valid text.")
    return meta


def _check_brand(body: LinkIn, wid: str) -> Dict[str, Any]:
    """The look of the hosted connect page: your logo, colour and help address. Every value is checked, since it is shown to your customer."""
    brand: Dict[str, Any] = {}
    if body.logo_url:
        if not body.logo_url.startswith("https://") or len(body.logo_url) > 400 or re.search(r"[\s<>\"']", body.logo_url):
            raise HTTPException(400, "logo_url must be an https address of up to 400 characters.")
        brand["logo"] = body.logo_url
    if body.accent_color:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", body.accent_color):
            raise HTTPException(400, "accent_color must look like #1A73E8.")
        brand["color"] = body.accent_color
    if body.support_email:
        if not accounts.EMAIL_RE.match(body.support_email) or len(body.support_email) > 120:
            raise HTTPException(400, "support_email must be a valid email address.")
        brand["support"] = body.support_email
    if body.hide_powered_by:
        if config.ENFORCE_PLANS and billing.plan_of(wid)["id"] == "kit":
            raise HTTPException(400, "Hiding \"Powered by WatchFox\" needs the Tracker or Alpha plan.")
        brand["hide"] = True
    return brand


@app.post("/v1/connect-links")
def connect_link(body: LinkIn, user=Depends(current_user)):
    """A one-time page where a person connects their mailbox to your account. Send them there, or redirect them."""
    provider = body.provider
    if body.connection_id:
        existing = store.connection(body.connection_id, user["id"])
        if not existing:
            raise HTTPException(404, "No such mailbox.")
        provider = existing["provider"]
    if provider not in ("gmail", "m365"):
        raise HTTPException(400, "provider must be gmail or m365. Use POST /v1/connections for imap.")
    if provider == "m365" and not m365.configured():
        raise HTTPException(503, "Microsoft 365 is not available on this server yet.")
    if not body.connection_id and len(store.connections(user["id"])) >= billing.limit(user["id"], "mailboxes"):
        raise HTTPException(400, f"Your plan allows {billing.limit(user['id'], 'mailboxes')} mailboxes. Upgrade for more.")
    if body.redirect_url and not body.redirect_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise HTTPException(400, "redirect_url must be https.")
    if len(body.state) > 200 or len(body.app_name) > 60:
        raise HTTPException(400, "state is limited to 200 characters and app_name to 60.")
    brand = _check_brand(body, user["id"])
    tok = vault.sign({"uid": user["id"], "p": provider, "r": body.redirect_url, "s": body.state, "m": _check_metadata(body.metadata),
                      "a": body.app_name.strip(), "c": body.connection_id, **({"b": brand} if brand else {})}, 3600)
    return {"url": f"{config.BASE_URL}/connect?t={tok}", "expires_in": 3600}


def _link(t: str) -> Dict[str, Any]:
    link = vault.verify(t)
    if not link or "uid" not in link:
        raise HTTPException(400, "This link has expired or is not valid. Ask for a new one.")
    return link


def _return_url(link: Dict[str, Any], cid: str, email: str) -> Optional[str]:
    if not link.get("r"):
        return None
    q = {"status": "connected", "connection_id": cid, "email": email}
    if link.get("s"):
        q["state"] = link["s"]
    return link["r"] + ("&" if "?" in link["r"] else "?") + urlencode(q)


@app.get("/v1/connect/session")
def connect_session(t: str):
    """Public: what the hosted connect page needs to know about a connect link."""
    link = _link(t)
    reconnect = store.connection(link["c"], link["uid"]) if link.get("c") else None
    host = urlparse(link["r"]).hostname if link.get("r") else ""
    return {"provider": link["p"], "app_name": link.get("a") or "", "return_to": host or "", "brand": link.get("b") or {},
            "reconnect_email": reconnect["email"] if reconnect else None,
            "microsoft_available": m365.configured(),
            "admin_consent_url": f"{config.BASE_URL}/auth/m365/admin-consent" if m365.configured() else None}


class GmailConnect(BaseModel):
    t: str
    email: str
    app_password: str


@app.post("/v1/connect/gmail")
def connect_gmail(body: GmailConnect, request: Request):
    """Public (needs a valid connect link): connect a Gmail mailbox with an app password."""
    link = _link(body.t)
    if link["p"] != "gmail":
        raise HTTPException(400, "This link is not for Gmail.")
    limiter.hit("connect-ip", client_ip(request), 30, 600)
    limiter.hit("connect-link", body.t[-40:], 8, 600)
    email = body.email.strip().lower()
    password = "".join(body.app_password.split())
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter the full Gmail address, such as name@gmail.com.")
    if len(password) != 16 or not password.isalpha():
        raise HTTPException(400, "An app password is 16 letters, like abcd efgh ijkl mnop. Copy it exactly as Google shows it.")
    secret = {"host": "imap.gmail.com", "port": 993, "user": email, "password": password, "folder": "INBOX"}
    try:
        make("gmail", {"secret": secret}).check()
    except ValueError as e:
        raise HTTPException(400, str(e))
    if link.get("c"):
        existing = store.connection(link["c"], link["uid"])
        if not existing:
            raise HTTPException(400, "That mailbox no longer exists.")
        if existing["email"].lower() != email:
            raise HTTPException(400, f"Please use {existing['email']}, the mailbox that is being reconnected.")
    elif not any(c["email"].lower() == email and c["provider"] == "gmail" for c in store.connections(link["uid"])) \
            and len(store.connections(link["uid"])) >= billing.limit(link["uid"], "mailboxes"):
        raise HTTPException(400, "This account has reached its mailbox limit, so this mailbox cannot be added. Ask the app owner to upgrade.")
    cid = store.upsert_connection(link["uid"], "gmail", email, secret, link.get("m"), link.get("c") or "")
    if store.watches_for_connection(cid):
        runner.start_connection(cid)
    return {"connection_id": cid, "email": email, "redirect": _return_url(link, cid, email)}


class ConnectionIn(BaseModel):
    provider: str
    host: str = ""
    port: int = 993
    user: str = ""
    password: str = ""
    folder: str = "INBOX"
    metadata: Dict[str, Any] = {}


def _public_connection(c: Dict[str, Any]) -> Dict[str, Any]:
    cls = registry().get(c["provider"])
    return {"id": c["id"], "provider": c["provider"], "email": c["email"], "method": getattr(cls, "method", ""),
            "status": c.get("status") or "active", "status_reason": c.get("status_reason"),
            "delivery": c.get("delivery") or "polling", "delivery_note": c.get("delivery_note"),
            "metadata": json.loads(c.get("metadata") or "{}"), "created": int(c["created"])}


@app.post("/v1/connections")
def create_connection(body: ConnectionIn, user=Depends(current_user)):
    """Connect a mailbox from your server: imap (host, user, password), gmail (user, password = app password), or sandbox."""
    require_verified(user)
    if len(store.connections(user["id"])) >= billing.limit(user["id"], "mailboxes"):
        raise HTTPException(400, f"Your plan allows {billing.limit(user['id'], 'mailboxes')} mailboxes. Upgrade for more.")
    meta = _check_metadata(body.metadata)
    if body.provider in ("imap", "gmail"):
        host = body.host.strip() or ("imap.gmail.com" if body.provider == "gmail" else "")
        if not (host and body.user and body.password):
            raise HTTPException(400, f"{body.provider} needs user and password" + ("" if body.provider == "gmail" else " and host") + ".")
        if body.provider == "imap" and (body.port != 993 or not abuse.public_host(host, 993)):
            raise HTTPException(400, "The mail server must be a public address on port 993 (IMAP over TLS).")
        password = "".join(body.password.split()) if body.provider == "gmail" else body.password
        secret = {"host": host, "port": body.port, "user": body.user.strip(), "password": password, "folder": body.folder or "INBOX"}
        try:
            make(body.provider, {"secret": secret}).check()
        except ValueError as e:
            raise HTTPException(400, str(e))
        cid = store.upsert_connection(user["id"], body.provider, body.user.strip().lower(), secret, meta)
    elif body.provider == "sandbox":
        cid = store.upsert_connection(user["id"], "sandbox", f"sandbox-{len(store.connections(user['id'])) + 1}", {}, meta)
    else:
        raise HTTPException(400, "Use POST /v1/connect-links for m365, or connect gmail with an app password.")
    return _public_connection(store.connection(cid))


@app.get("/v1/connections")
def list_connections(user=Depends(current_user)):
    return [_public_connection(c) for c in store.connections(user["id"])]


@app.get("/v1/connections/{cid}")
def get_connection(cid: str, user=Depends(current_user)):
    c = store.connection(cid, user["id"])
    if not c:
        raise HTTPException(404, "No such mailbox.")
    return _public_connection(c)


@app.delete("/v1/connections/{cid}")
def remove_connection(cid: str, user=Depends(current_user)):
    if not store.connection(cid, user["id"]):
        raise HTTPException(404, "No such mailbox.")
    runner.stop_connection(cid)
    push.remove(cid)
    store.delete_connection(user["id"], cid)
    return {"ok": True}


class SandboxAttachment(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    content_base64: str


class SandboxEmail(BaseModel):
    subject: str = ""
    body: str
    sender: str = "someone@example.com"
    attachments: List[SandboxAttachment] = []


@app.post("/v1/connections/{cid}/sandbox-emails")
def sandbox_email(cid: str, body: SandboxEmail, request: Request, user=Depends(current_user)):
    """Drop an email into a sandbox mailbox. It is picked up within a couple of seconds, like real mail."""
    c = store.connection(cid, user["id"])
    if not c:
        raise HTTPException(404, "No such mailbox.")
    if c["provider"] != "sandbox":
        raise HTTPException(400, "Only sandbox mailboxes accept test emails.")
    require_verified(user)
    limiter.hit("sandbox-email", user["id"], 60, 60)
    require_run(user)                                   # a test email is checked like real mail, so it uses a run
    abuse.free_gate(user, request)
    if len(store.sandbox_emails(cid)) >= 200:
        raise HTTPException(400, "A sandbox holds up to 200 test emails. Delete it and add a new sandbox to start fresh.")
    if len(body.attachments) > 5:
        raise HTTPException(400, "A sandbox email can carry up to 5 attachments.")
    try:
        raw = mailfetch.build_eml(body.sender, body.subject, body.body, [a.dict() for a in body.attachments])
    except Exception:
        raise HTTPException(400, "An attachment is not valid base64.")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "A sandbox email is limited to 5 MB.")
    return {"id": store.add_sandbox_email(cid, body.sender, body.subject, body.body, raw)}


# ---------------------------------------------------------------- Microsoft sign-in and push endpoints
def _page(msg: str, ok: bool = True, back: str = "") -> HTMLResponse:
    link = f'<p><a href="{back}">Continue</a></p>' if back else "<p>You can close this tab.</p>"
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>WatchFox</title>"
        "<body style=\"font-family:ui-sans-serif,system-ui,-apple-system,Helvetica,Arial,sans-serif;max-width:30rem;margin:18vh auto;padding:0 1.5rem;text-align:center\">"
        f"<h1 style='font-size:2rem;letter-spacing:-.03em'>{'Done' if ok else 'Not connected'}</h1><p>{msg}</p>{link}</body>")


@app.get("/auth/m365/start")
def auth_start(t: str = ""):
    link = vault.verify(t)
    if not link or link.get("p") != "m365":
        return _page("This link has expired or is invalid. Ask for a new one.", False)
    return RedirectResponse(m365.start(link))


@app.get("/auth/m365/callback")
def auth_callback(request: Request):
    params = dict(request.query_params)
    if "error" in params:
        text = params.get("error_description", params["error"])
        if "AADSTS65001" in text or "AADSTS90094" in text or "admin" in text.lower():
            text = "Your organisation needs an administrator to approve this app first. Ask your IT team, then try again."
        return _page(f"Sign-in was not completed: {text}", False)
    try:
        cid, email, link = m365.finish(params)
    except Exception as e:
        return _page(f"Sign-in failed: {e}", False)
    conn = store.connection(cid)
    if conn:
        threading.Thread(target=push.ensure, args=(conn,), daemon=True).start()
        if store.watches_for_connection(cid):
            runner.start_connection(cid)
    back = _return_url(link, cid, email)
    return RedirectResponse(back) if back else RedirectResponse(f"/connect/done?email={email}")


@app.get("/auth/m365/admin-consent")
def admin_consent():
    if not m365.configured():
        return _page("Microsoft 365 is not available yet.", False)
    return RedirectResponse(m365.admin_consent_url(f"{config.BASE_URL}/auth/m365/admin-done"))


@app.get("/auth/m365/admin-done")
def admin_done(request: Request):
    if request.query_params.get("error"):
        return _page("The approval was not completed.", False)
    return _page("Thank you. Your organisation has approved WatchFox. Ask the person who wants to connect their mailbox to try again.", True)


@app.api_route("/hooks/m365/notify", methods=["POST", "GET"], include_in_schema=False)
async def m365_notify(request: Request):
    """Microsoft calls this when mail arrives. It must answer fast, so we only queue the work."""
    token = request.query_params.get("validationToken")
    if token:                                        # the handshake when a subscription is created
        return PlainTextResponse(token)
    try:
        push.handle_notifications(await request.json())
    except Exception:
        pass
    return Response(status_code=202)


@app.api_route("/hooks/m365/lifecycle", methods=["POST", "GET"], include_in_schema=False)
async def m365_lifecycle(request: Request):
    token = request.query_params.get("validationToken")
    if token:
        return PlainTextResponse(token)
    try:
        push.handle_lifecycle(await request.json())
    except Exception:
        pass
    return Response(status_code=202)


# ---------------------------------------------------------------- watches
def public_host_ok(url: str) -> bool:
    """True if every address the webhook host resolves to is public. Checked when a webhook is saved and again before
    every delivery, so a host that later points at a private address (DNS rebinding) is refused."""
    return abuse.public_url(url)


def check_webhook_url(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or len(url) > 500:
        raise HTTPException(400, "webhook_url must be a full https:// address.")
    if u.username or u.password:
        raise HTTPException(400, "webhook_url must not contain a username or password.")
    if config.ALLOW_INSECURE_WEBHOOKS:
        return
    if u.scheme != "https":
        raise HTTPException(400, "webhook_url must use https.")
    try:
        socket.getaddrinfo(u.hostname, u.port or 443)
    except socket.gaierror:
        raise HTTPException(400, "webhook_url host could not be found.")
    if not public_host_ok(url):
        raise HTTPException(400, "webhook_url must point to a public address.")


class WatchIn(BaseModel):
    connection_id: str
    what: Union[str, List[Union[str, Dict[str, Any]]]] = "enquiry"
    webhook_url: str = ""
    webhook_secret: str = ""
    backfill_days: int = 0
    metadata: Dict[str, Any] = {}
    include: List[str] = []       # any of: body, eml, attachments


INCLUDE = ("body", "eml", "attachments")


def _public_watch(w: Dict[str, Any], secret: str = "") -> Dict[str, Any]:
    out = {"id": w["id"], "connection_id": w["connection_id"], "what": w["what"],
           "labels": [p["label"] for p in presets.resolve(w["what"], w["user_id"])], "webhook_url": w["webhook_url"] or None,
           "active": runner.active(w["id"]), "metadata": w.get("metadata") or {}, "include": w.get("include") or [], "backfill_days": w["backfill_days"], "created": int(w["created"]),
           "last_checked": int(w["last_run"]) if w["last_run"] else None, "last_error": w["last_error"]}
    if secret:
        out["webhook_secret"] = secret
    return out


@app.post("/v1/watches")
def create_watch(body: WatchIn, user=Depends(current_user)):
    if not store.connection(body.connection_id, user["id"]):
        raise HTTPException(404, "No such mailbox. Connect one first (see GET /v1/connections).")
    require_verified(user)
    if len(store.watches(user["id"])) >= billing.limit(user["id"], "watches"):
        raise HTTPException(400, f"Your plan allows {billing.limit(user['id'], 'watches')} watches. Upgrade for more.")
    if not 0 <= body.backfill_days <= 7:
        raise HTTPException(400, "backfill_days must be between 0 and 7.")
    if any(i not in INCLUDE for i in body.include):
        raise HTTPException(400, f"include may contain only: {', '.join(INCLUDE)}.")
    if body.include and not body.webhook_url:
        raise HTTPException(400, "include only applies to webhooks. Add a webhook_url, or download files with the email endpoints.")
    try:
        presets.resolve(body.what, user["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    want = sorted(body.what if isinstance(body.what, list) else [body.what])
    if any(w["connection_id"] == body.connection_id and sorted(w["what"]) == want and (w.get("webhook_url") or "") == (body.webhook_url or "")
           for w in store.watches(user["id"])):
        raise HTTPException(409, "You already have this exact watch. Edit it, or change the type or webhook to make another.")
    secret = ""
    if body.webhook_url:
        check_webhook_url(body.webhook_url)
        secret = body.webhook_secret or "whsec_" + vault.secrets.token_urlsafe(24)
    wid = store.add_watch(user["id"], body.connection_id, body.what if isinstance(body.what, list) else [body.what],
                          body.webhook_url, secret, body.backfill_days, _check_metadata(body.metadata), sorted(set(body.include)))
    w = store.watch(wid)
    runner.start(w)
    return _public_watch(w, secret)


class WatchPatch(BaseModel):
    what: Optional[List[str]] = None
    webhook_url: Optional[str] = None       # "" removes the webhook
    metadata: Optional[Dict[str, Any]] = None
    include: Optional[List[str]] = None


@app.patch("/v1/watches/{wid}")
def edit_watch(wid: str, body: WatchPatch, user=Depends(current_user)):
    """Change what a watch catches, its webhook or what the webhook carries. Mail already checked is not rechecked."""
    w = store.watch(wid, user["id"])
    if not w:
        raise HTTPException(404, "No such watch.")
    if body.what is not None:
        if not body.what:
            raise HTTPException(400, "A watch needs at least one type to catch.")
        try:
            presets.resolve(body.what, user["id"])
        except ValueError as e:
            raise HTTPException(400, str(e))
    if body.include is not None and any(i not in INCLUDE for i in body.include):
        raise HTTPException(400, f"include may contain only: {', '.join(INCLUDE)}.")
    url = body.webhook_url if body.webhook_url is not None else w["webhook_url"]
    new_secret, secret = "", None
    if body.webhook_url is not None:
        if body.webhook_url:
            check_webhook_url(body.webhook_url)
            if not w["webhook_url"]:
                new_secret = secret = "whsec_" + vault.secrets.token_urlsafe(24)
        else:
            secret = ""
    inc = body.include if body.include is not None else w["include"]
    if inc and not url:
        raise HTTPException(400, "include only applies to webhooks. Add a webhook_url first.")
    store.update_watch(user["id"], wid, body.what, body.webhook_url, secret, body.include if url else [],
                       _check_metadata(body.metadata) if body.metadata is not None else None)
    return _public_watch(store.watch(wid), new_secret)


@app.get("/v1/watches")
def list_watches(user=Depends(current_user)):
    return [_public_watch(w) for w in store.watches(user["id"])]


@app.get("/v1/watches/{wid}")
def get_watch(wid: str, user=Depends(current_user)):
    w = store.watch(wid, user["id"])
    if not w:
        raise HTTPException(404, "No such watch.")
    return _public_watch(w)


@app.delete("/v1/watches/{wid}")
def remove_watch(wid: str, user=Depends(current_user)):
    if not store.delete_watch(user["id"], wid):
        raise HTTPException(404, "No such watch.")
    return {"ok": True}


@app.post("/v1/watches/{wid}/test")
def test_watch(wid: str, user=Depends(current_user)):
    """Send one sample match to the watch's webhook, once, and report what your server answered."""
    w = store.watch(wid, user["id"])
    if not w:
        raise HTTPException(404, "No such watch.")
    if not w["webhook_url"]:
        raise HTTPException(400, "This watch has no webhook_url.")
    limiter.hit("webhook-test", user["id"], 20, 60)
    check_webhook_url(w["webhook_url"])
    item = Item(id="test", title="Test email from WatchFox", sender="test@watchfox.dev", kind="email",
                text="This is a test event. Your webhook is reachable.", received=time.time())
    ev = runner.build_event(w, item, {"enquiry": {"label": "Enquiries", "score": 0.9, "threshold": 0.38,
                                                    "exclude_score": None, "matched": True, "excluded": False}})
    ev["test"] = True
    ok, code = runner.deliver(w, ev, delays=[0])
    return {"delivered": ok, "status_code": code or None}


@app.get("/v1/watches/{wid}/deliveries")
def watch_deliveries(wid: str, user=Depends(current_user)):
    if not store.watch(wid, user["id"]):
        raise HTTPException(404, "No such watch.")
    return store.deliveries(wid)


# ---------------------------------------------------------------- the original email and its attachments (never stored)
_downloads = threading.BoundedSemaphore(2)       # an email can be up to 100 MB: only a couple are held in memory at once


def _original(user, cid: str, eid: str) -> bytes:
    """Read the message live from the mailbox. Only emails that matched one of your watches can be downloaded, so a
    developer cannot browse the rest of a customer's mailbox."""
    limiter.hit("fetch", user["id"], 120, 60)
    conn = store.connection(cid, user["id"])
    if not conn:
        raise HTTPException(404, "No such mailbox.")
    if not store.matched_item(user["id"], cid, eid):
        raise HTTPException(404, "No matched email with that id on this mailbox. Only emails that matched one of your watches can be downloaded.")
    if conn["status"] == "needs_attention":
        raise HTTPException(409, "This mailbox needs the person to reconnect before its emails can be read.")
    if not _downloads.acquire(timeout=20):
        raise HTTPException(503, "Many large downloads are running. Try again in a few seconds.", headers={"Retry-After": "5"})
    try:
        return mailfetch.fetch_raw(conn, eid)
    except mailfetch.Gone:
        raise HTTPException(404, "The email is no longer in the mailbox (it was deleted or moved).")
    except mailfetch.TooLarge:
        raise HTTPException(413, f"This email is larger than the {config.MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB download limit.")
    except AuthError as e:
        runner.flag(conn, str(e))
        raise HTTPException(409, str(e))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "The mailbox could not be reached just now. Try again in a moment.")
    finally:
        _downloads.release()


NO_STORE = {"Cache-Control": "no-store"}


@app.get("/v1/connections/{cid}/emails/{eid}/raw")
def email_raw(cid: str, eid: str, user=Depends(current_user)):
    """The whole original message as an .eml file. Read from the mailbox now; WatchFox keeps no copy."""
    return Response(content=_original(user, cid, eid), media_type="message/rfc822",
                    headers={"Content-Disposition": 'attachment; filename="email.eml"', **NO_STORE})


@app.get("/v1/connections/{cid}/emails/{eid}/attachments")
def email_attachments(cid: str, eid: str, user=Depends(current_user)):
    base = f"{config.BASE_URL}/v1/connections/{cid}/emails/{quote(eid, safe='')}/attachments"
    return Response(content=json.dumps([{"id": a["id"], "filename": a["filename"], "content_type": a["content_type"],
                                         "size": a["size"], "url": f"{base}/{a['id']}"}
                                        for a in mailfetch.parse_attachments(_original(user, cid, eid))]),
                    media_type="application/json", headers=NO_STORE)


@app.get("/v1/connections/{cid}/emails/{eid}/attachments/{aid}")
def email_attachment(cid: str, eid: str, aid: str, user=Depends(current_user)):
    for a in mailfetch.parse_attachments(_original(user, cid, eid)):
        if a["id"] == aid:
            return Response(content=a["data"], media_type=a["content_type"],
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(a['filename'])}", **NO_STORE})
    raise HTTPException(404, "No such attachment on that email.")


# ---------------------------------------------------------------- results
@app.get("/v1/matches")
def matches(what: str = "", watch_id: str = "", limit: int = 50, user=Depends(current_user)):
    return [_public_item(i) for i in store.items(user["id"], matched_only=True, watch_id=watch_id, what=what, limit=limit)]


@app.get("/v1/emails")
def emails(watch_id: str = "", limit: int = 100, user=Depends(current_user)):
    return [_public_item(i) for i in store.items(user["id"], watch_id=watch_id, limit=limit)]


def _with_confidence(results: Dict[str, Any]) -> Dict[str, Any]:
    for r in results.values():            # rows saved before confidence existed
        if "confidence" not in r:
            r["confidence"] = presets.confidence(r["score"], r["threshold"])
            r["confidence_level"] = presets.confidence_level(r["confidence"])
    return results


def _public_item(i: Dict[str, Any]) -> Dict[str, Any]:
    _with_confidence(i["results"])
    return {"watch_id": i["watch_id"], "email": {"id": i["item_id"], "subject": i["title"], "from": i["sender"],
                                                 "received": int(i["received"]), "snippet": i["snippet"],
                                                 "attachments": [{"filename": a} if isinstance(a, str) else a for a in i["attachments"]]},
            "matched": i["matched"], "results": i["results"], "checked": int(i["processed"])}


def _csv_cell(v: Any) -> str:
    t = "" if v is None else str(v)
    return "'" + t if t[:1] in ("=", "+", "-", "@", "\t", "\r") else t       # stops spreadsheets running a cell as a formula


@app.get("/v1/log/export")
def export_log(matched: Optional[bool] = None, watch_id: str = "", connection_id: str = "", what: str = "",
               delivery: str = "", q: str = "", since: float = 0, max_rows: int = 100000, user=Depends(current_user)):
    """The activity log as a CSV file, with the same filters as GET /v1/log. Logs are deleted after the retention period
    (90 days), so download what you need to keep."""
    import csv
    import io

    limiter.hit("export", user["id"], 6, 60)
    max_rows = max(1, min(max_rows, 500000))

    def rows():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["checked_at", "received_at", "watch_id", "connection_id", "email_id", "subject", "from", "caught", "caught_as",
                    "confidence", "skipped", "webhook_status", "webhook_http_status", "webhook_attempts", "webhook_error"])
        yield buf.getvalue(); buf.seek(0); buf.truncate()
        before, sent = 0.0, 0
        while sent < max_rows:
            page = activity_log(matched, watch_id, connection_id, what, delivery, q, since, before, min(200, max_rows - sent), user)
            for e in page["items"]:
                x = e.get("delivery") or {}
                iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
                w.writerow([_csv_cell(v) for v in (
                    iso(e["checked_at"]), iso(e["received_at"]), e["watch_id"], e["connection_id"], e["email"]["id"],
                    e["email"]["subject"], e["email"]["from"], "yes" if e["matched"] else "no",
                    "; ".join(m["label"] for m in e["matches"]), "; ".join(f"{m['label']} {m['confidence']}%" for m in e["matches"]),
                    "; ".join(f"{i['label']}: {i['reason']}" for i in e["ignored"]),
                    x.get("status", ""), x.get("http_status", ""), x.get("attempts", ""), x.get("error", ""))])
            yield buf.getvalue(); buf.seek(0); buf.truncate()
            sent += len(page["items"])
            if not page["next_before"] or not page["items"]:
                break
            before = page["next_before"]

    return StreamingResponse(rows(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="watchfox-log-{time.strftime("%Y%m%d")}.csv"'})


@app.get("/v1/log")
def activity_log(matched: Optional[bool] = None, watch_id: str = "", connection_id: str = "", what: str = "",
                 delivery: str = "", q: str = "", since: float = 0, before: float = 0, limit: int = 50,
                 user=Depends(current_user)):
    """Every email WatchFox checked for you, newest first, with what it decided and what happened to the webhook.
    Page through with next_before. Content is never included: only the subject, sender, snippet and scores."""
    if delivery and delivery not in ("delivered", "failed", "retrying", "pending", "none"):
        raise HTTPException(400, "delivery must be one of: delivered, failed, retrying, pending, none.")
    fetch = limit * 3 if what else limit                   # a type filter is applied after the query, so read extra rows
    rows = store.log_page(user["id"], matched, watch_id, connection_id, since, before, q, delivery, fetch)
    out, last, stopped_early = [], None, False
    for n, r in enumerate(rows):
        last = r["processed"]
        res = _with_confidence(r["results"])
        if what and not res.get(what, {}).get("matched"):  # `what`: only emails where this type matched
            continue
        hit = [dict(what=k, label=v["label"], score=v["score"], confidence=v["confidence"], confidence_level=v["confidence_level"])
               for k, v in res.items() if v["matched"]]
        ignored = [dict(what=k, label=v["label"], reason=v.get("reason") or "looks like something other than a request")
                   for k, v in res.items() if v.get("excluded")]
        d = None
        if r["d_id"]:
            d = {"id": r["d_id"], "event_id": r["event_id"], "status": r["d_status"], "webhook_url": r["hook"],
                 "http_status": r["d_code"], "attempts": r["d_attempts"], "error": r["d_error"] or None,
                 "sent_at": int(r["d_created"]), "last_attempt_at": int(r["d_updated"]),
                 "delivered_at": int(r["d_updated"]) if r["d_status"] == "delivered" else None}
        elif hit and r["hook"]:
            d = {"status": "pending", "webhook_url": r["hook"], "event_id": r["event_id"]}
        entry = {"id": r["key"], "checked_at": int(r["processed"]), "received_at": int(r["received"]),
                 "watch_id": r["watch_id"], "connection_id": r["cid"],
                 "email": {"id": r["item_id"], "subject": r["title"], "from": r["sender"], "snippet": r["snippet"],
                           "attachments": [{"filename": a} if isinstance(a, str) else a for a in r["attachments"]]},
                 "matched": bool(r["matched"]), "matches": hit, "ignored": ignored, "results": res,
                 "delivery": d, "include": r["inc"]}
        if r["matched"]:
            entry["links"] = {"raw": f"{config.BASE_URL}/v1/connections/{r['cid']}/emails/{quote(r['item_id'], safe='')}/raw"}
        out.append(entry)
        if len(out) >= limit:
            stopped_early = n < len(rows) - 1
            break
    more = stopped_early or len(rows) >= fetch
    return {"items": out, "next_before": last if more else None, "retention_days": config.LOG_RETENTION_DAYS}


_streams: Dict[str, int] = {}
_streams_lock = threading.Lock()


@app.get("/v1/events")
def events(user=Depends(current_user)):
    """Server-sent events: one message per email checked (type email.matched or email.checked)."""
    q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=500)
    uid = user["id"]
    with _streams_lock:                              # each stream holds a server thread, so cap them per account
        if _streams.get(uid, 0) >= 5:
            raise HTTPException(429, "Too many open event streams for this account (5). Close one and try again.")
        _streams[uid] = _streams.get(uid, 0) + 1

    def listener(owner, ev):
        if owner == uid:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    runner.listeners.append(listener)

    def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    yield f"data: {json.dumps(q.get(timeout=15))}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            if listener in runner.listeners:
                runner.listeners.remove(listener)
            with _streams_lock:
                _streams[uid] = max(0, _streams.get(uid, 1) - 1)

    return StreamingResponse(gen(), media_type="text/event-stream")



# ---------------------------------------------------------------- your own types
# The AI assistant is used only on these routes, to help people set types up. Catching email never calls it.
class ExampleIn(BaseModel):
    subject: str = ""
    body: str


class TuneIn(BaseModel):
    label: str
    description: str
    positives: List[ExampleIn] = []
    negatives: List[ExampleIn] = []
    treat_as_yes: List[str] = []
    use_assistant: bool = True


class TypeIn(BaseModel):
    label: str
    description: str
    question: str
    threshold: float
    excludes: Any = []
    ignore_subject: Any = []
    report: Any = {}
    inputs: Any = {}


class SuggestIn(BaseModel):
    sentence: str


class ExamplesIn(BaseModel):
    label: str
    description: str
    positives: int = 6
    negatives: int = 6


def _kind_names() -> Dict[str, str]:
    return {k: f"{v[0]}: {v[1]}" for k, v in CATEGORY_INFO.items()}


def _assistant(user, fn, *a):
    """Run one AI assistant call for a person setting up a type. Returns None if it is not set up or failed."""
    if not llm.available():
        return None
    limiter.hit("assistant", user["id"], 40, 3600)
    try:
        return fn(*a)
    except Exception:
        return None


def _pairs(xs, limit=40):
    xs = [_shape(e) for e in xs]
    return [(e["subject"], "", e["body"][:3000]) for e in xs if e["body"].strip()][:limit]


def _shape(e) -> Dict[str, str]:
    return {"subject": e.subject, "body": e.body} if hasattr(e, "subject") else {"subject": e.get("subject", ""), "body": e.get("body", "")}


@app.get("/v1/types")
def list_types(user=Depends(current_user)):
    return [{"id": t["id"], "label": t["label"], "description": t["description"], "threshold": t["spec"]["threshold"],
             "has_exclude": bool(t["spec"].get("excludes")), "maturity": "custom",
             "caught": (t["spec"].get("report") or {}).get("caught"), "samples": (t["spec"].get("report") or {}).get("samples")}
            for t in store.types(user["id"])]


@app.get("/v1/types/kinds")
def type_kinds(user=Depends(current_user)):
    """Everyday kinds of email the tuner already knows, so a new type can say which of them count as yes."""
    return {"assistant": llm.available(),
            "kinds": [{"id": k, "label": v[0], "description": v[1]} for k, v in CATEGORY_INFO.items()]}


@app.get("/v1/types/{tid}")
def get_type(tid: str, user=Depends(current_user)):
    """A custom type with the examples it was tuned on, for editing."""
    t = store.type_(tid, user["id"])
    if not t:
        raise HTTPException(404, "No such type.")
    return {"id": t["id"], "label": t["label"], "description": t["description"], "question": t["spec"]["question"],
            "threshold": t["spec"]["threshold"], "inputs": t["spec"].get("inputs") or {}}


@app.post("/v1/types/suggest")
def suggest_type(body: SuggestIn, user=Depends(current_user)):
    """One sentence in, a full first draft out: name, description, example emails and look-alikes (AI assistant)."""
    sentence = body.sentence.strip()[:400]
    if len(sentence) < 8:
        raise HTTPException(400, "Say in a sentence what you want to catch.")
    if not llm.available():
        raise HTTPException(501, "The AI assistant is not set up on this server.")
    d = _assistant(user, llm.suggest, sentence, _kind_names())
    if not d or not d["label"]:
        raise HTTPException(502, "The assistant could not draft this right now. Fill the form by hand, or try again.")
    return d


@app.post("/v1/types/examples")
def more_examples(body: ExamplesIn, user=Depends(current_user)):
    """More invented example emails (matches and look-alikes) for a type description (AI assistant)."""
    if not llm.available():
        raise HTTPException(501, "The AI assistant is not set up on this server.")
    n, m = max(0, min(body.positives, 10)), max(0, min(body.negatives, 10))
    d = _assistant(user, llm.examples, body.label.strip()[:60], body.description.strip()[:500], n, m)
    if not d:
        raise HTTPException(502, "The assistant could not write examples right now. Try again.")
    return d


def _run_tune(user, label, desc, pos, neg, kinds, assistant_ok, base_notes=()):
    notes, wordings, skip, made = list(base_notes), [], [], {"positives": 0, "negatives": 0}
    if assistant_ok and llm.available():
        w = _assistant(user, llm.wordings, label, desc)
        if w:
            wordings = w
        want_p, want_n = max(0, 10 - len(pos)), max(0, 10 - len(neg))
        if want_p or want_n:
            ex = _assistant(user, llm.examples, label, desc, want_p, want_n)
            if ex:
                pos += _pairs(ex["positives"]); neg += _pairs(ex["negatives"])
                made = {"positives": len(ex["positives"]), "negatives": len(ex["negatives"])}
        skip = _assistant(user, llm.similar_kinds, label, desc, _kind_names()) or []
        notes.append("The AI assistant drafted extra wordings and invented example emails from your description. It never saw real email.")
    elif llm.available():
        notes.append("The AI assistant was switched off, so only your examples and the built-in samples were used.")
    if len(pos) < 3:
        raise HTTPException(400, "Add at least 3 example emails that should be caught"
                                 + (" (or let the assistant draft some)." if llm.available() else "."))
    try:
        out = tuner.tune(label, desc, pos, neg, kinds, wordings, skip_kinds=skip)
    except RuntimeError:
        raise HTTPException(503, "WatchFox is warming up. Try again in a moment.", headers={"Retry-After": "15"})
    out["report"]["notes"] = notes
    out["report"]["generated"] = made
    return out


@app.post("/v1/types/tune")
def tune_type(body: TuneIn, user=Depends(current_user)):
    """Draft the settings for a new type: tries wordings, picks the best, sets the threshold and exclusions. Takes
    about half a minute. Nothing is saved until you POST /v1/types."""
    require_verified(user)
    limiter.hit("tune", user["id"], 12, 3600)
    label, desc = body.label.strip()[:60], body.description.strip()[:500]
    if not label or len(desc) < 10:
        raise HTTPException(400, "Give the type a name and describe it in a sentence or two.")
    if any(k not in CATEGORY_INFO for k in body.treat_as_yes):
        raise HTTPException(400, "Unknown kind in treat_as_yes. See GET /v1/types/kinds.")
    pos, neg = _pairs(body.positives, 30), _pairs(body.negatives, 30)
    return _run_tune(user, label, desc, pos, neg, body.treat_as_yes, body.use_assistant)


def _clean_inputs(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ("positives", "negatives"):
        v = raw.get(k)
        if v is None:
            continue
        if not isinstance(v, list):
            raise ValueError(f"inputs.{k} must be a list of {{subject, body}}.")
        out[k] = [{"subject": str(e.get("subject") or "")[:200], "body": e["body"][:3000]} for e in v[:60]
                  if isinstance(e, dict) and isinstance(e.get("body"), str) and e["body"].strip()]
    for k in ("treat_as_yes", "skip_kinds"):
        v = raw.get(k)
        if v is not None:
            if not isinstance(v, list) or any(not isinstance(i, str) for i in v):
                raise ValueError(f"inputs.{k} must be a list of kind ids.")
            out[k] = [i for i in v if i in CATEGORY_INFO][:40]
    return out


def _spec_from(body: TypeIn, tid: str = "") -> Dict[str, Any]:
    try:
        ex = presets.clean_excludes(body.excludes)
        if body.inputs and not isinstance(body.inputs, dict):
            raise ValueError("inputs must be an object.")
        inputs = _clean_inputs(body.inputs or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    # subject rules are only kept for a default type (they come from its own built-in rules), never from a request
    ign = presets.PRESETS[tid].get("ignore_subject", []) if tid in presets.PRESETS else []
    rep = body.report if isinstance(body.report, dict) else {}
    return {"question": body.question.strip()[:400], "threshold": body.threshold, "excludes": ex, "skip_bulk": True,
            "ignore_subject": ign, "inputs": inputs,
            "report": {k: rep.get(k) for k in ("samples", "caught", "missed", "false_alarms_after", "separation", "mistakes") if k in rep}}


def _check_type(body: TypeIn):
    if not (body.label.strip() and body.question.strip()) or not 0.01 <= body.threshold <= 0.99:
        raise HTTPException(400, "A type needs a name, a question and a threshold between 0.01 and 0.99.")


@app.post("/v1/types", status_code=201)
def create_type(body: TypeIn, user=Depends(current_user)):
    require_verified(user)
    if len(store.types(user["id"])) >= billing.limit(user["id"], "types"):
        raise HTTPException(400, f"Your plan allows {billing.limit(user['id'], 'types')} custom types. Upgrade for more.")
    _check_type(body)
    tid = store.add_type(user["id"], body.label.strip()[:60], body.description.strip()[:500], _spec_from(body))
    return {"id": tid, "label": body.label.strip()[:60]}


@app.put("/v1/types/{tid}")
def update_type(tid: str, body: TypeIn, user=Depends(current_user)):
    """Replace a custom type's settings, or give yourself a re-tuned copy of a default type (tid = its id, e.g. enquiry)."""
    _check_type(body)
    if tid in presets.PRESETS:
        store.set_type_spec(user["id"], "override:" + tid, presets.PRESETS[tid]["label"], body.description.strip()[:500], _spec_from(body, tid))
    elif store.type_(tid, user["id"]):
        store.set_type_spec(user["id"], tid, body.label.strip()[:60], body.description.strip()[:500], _spec_from(body))
    else:
        raise HTTPException(404, "No such type.")
    return {"ok": True}


@app.delete("/v1/types/{tid}")
def delete_type(tid: str, user=Depends(current_user)):
    if tid in presets.PRESETS:                                   # go back to the built-in settings
        store.delete_type(user["id"], "override:" + tid)
        return {"ok": True}
    if any(tid in w["what"] for w in store.watches(user["id"])):
        raise HTTPException(409, "A watch still uses this type. Remove that watch first.")
    if not store.delete_type(user["id"], tid):
        raise HTTPException(404, "No such type.")
    store.clear_feedback(user["id"], tid)
    return {"ok": True}


# ---- corrections: "this was a wrong catch" / "this should have been caught"
class FeedbackIn(BaseModel):
    what: str
    watch_id: str
    email_id: str
    kind: str                       # "wrong" (it should not have matched) or "missed" (it should have matched)


@app.post("/v1/feedback", status_code=201)
def add_feedback(body: FeedbackIn, user=Depends(current_user)):
    """Tell WatchFox it got an email wrong. The email is read from the mailbox right now and a copy of its subject and
    text (up to 3,000 characters) is kept so the type can be tuned again. Delete it any time with DELETE /v1/corrections/{what}."""
    if body.kind not in ("wrong", "missed"):
        raise HTTPException(400, "kind must be 'wrong' or 'missed'.")
    uid = user["id"]
    row = store.item_row(uid, f"{body.watch_id}:{body.email_id}")
    if not row or body.what not in json.loads(row["results"] or "{}"):
        raise HTTPException(404, "That email was not checked for this type.")
    if len([f for f in store.feedback(uid, body.what)]) >= 100:
        raise HTTPException(400, "You have flagged 100 emails for this type already. Re-tune it, or clear the corrections.")
    w = store.watch(body.watch_id, uid)
    conn = store.connection(w["connection_id"], uid) if w else None
    if not conn:
        raise HTTPException(404, "The mailbox is no longer connected.")
    try:
        raw = mailfetch.fetch_raw(conn, body.email_id)
    except mailfetch.Gone:
        raise HTTPException(404, "That email is no longer in the mailbox, so it cannot be used as an example.")
    except mailfetch.TooLarge:
        raise HTTPException(413, "That email is too large to use as an example.")
    except AuthError as e:
        raise HTTPException(409, str(e))
    from .items import parse_eml
    p = parse_eml(raw)
    added = store.add_feedback(uid, body.what, row["key"], "negative" if body.kind == "wrong" else "positive",
                               (p["subject"] or row["title"] or "")[:200], (p["body"] or "")[:3000])
    return {"saved": added, "count": len(store.feedback(uid, body.what))}


@app.get("/v1/corrections")
def corrections(user=Depends(current_user)):
    out = {}
    for f in store.feedback(user["id"]):
        c = out.setdefault(f["what_id"], {"what": f["what_id"], "wrong": 0, "missed": 0})
        c["wrong" if f["kind"] == "negative" else "missed"] += 1
    res = []
    for wid, c in out.items():
        r = presets.resolve([wid], user["id"])[0] if (wid in presets.PRESETS or store.type_(wid, user["id"])) else None
        if r:
            c["label"] = r["label"]
            c["tuned_for_you"] = bool(store.type_("override:" + wid, user["id"])) if wid in presets.PRESETS else True
            res.append(c)
    return res


@app.delete("/v1/corrections/{what}")
def clear_corrections(what: str, user=Depends(current_user)):
    store.clear_feedback(user["id"], what)
    return {"ok": True}


@app.post("/v1/corrections/{what}/retune")
def retune(what: str, user=Depends(current_user)):
    """Tune a type again using the emails you flagged. Returns a draft to review; nothing changes until PUT /v1/types/{what}."""
    limiter.hit("tune", user["id"], 12, 3600)
    uid = user["id"]
    if what in presets.PRESETS:
        base = presets.PRESETS[what]
        cur = store.type_("override:" + what, uid)
        label, desc = base["label"], (cur["description"] if cur else base["description"])
        inputs = (cur["spec"].get("inputs") if cur else {}) or {}
        kinds = inputs.get("treat_as_yes") or tuner.PRESET_KINDS.get(what, [])
        carry = {"excludes": base.get("excludes", []), "ignore_subject": base.get("ignore_subject", [])}
    else:
        t = store.type_(what, uid)
        if not t:
            raise HTTPException(404, "No such type.")
        label, desc, inputs, kinds, carry = t["label"], t["description"], t["spec"].get("inputs") or {}, (t["spec"].get("inputs") or {}).get("treat_as_yes", []), {}
    fb = store.feedback(uid, what)
    if not fb:
        raise HTTPException(400, "Nothing flagged yet for this type. Use \"Wrong catch\" or \"Should have been caught\" on an email first.")
    pos = _pairs(inputs.get("positives", [])) + [(f["subject"], "", f["body"]) for f in fb if f["kind"] == "positive"]
    neg = _pairs(inputs.get("negatives", [])) + [(f["subject"], "", f["body"]) for f in fb if f["kind"] == "negative"]
    skip = inputs.get("skip_kinds", [])
    try:
        out = tuner.tune(label, desc, pos, neg, kinds, skip_kinds=skip)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError:
        raise HTTPException(503, "WatchFox is warming up. Try again in a moment.", headers={"Retry-After": "15"})
    seen = {e["question"] for e in out["excludes"]}
    out["excludes"] += [e for e in carry.get("excludes", []) if e["question"] not in seen]      # keep the hand-made rules of a default type
    out["ignore_subject"] = carry.get("ignore_subject", [])
    bad = {f["subject"] for f in fb}
    out["report"]["corrections"] = {"wrong": sum(f["kind"] == "negative" for f in fb), "missed": sum(f["kind"] == "positive" for f in fb),
                                    "still_wrong": sum(m["subject"] in bad for m in out["report"]["mistakes"])}
    out["report"]["notes"] = ["Tuned again with the emails you flagged added as examples."]
    return out


# ---------------------------------------------------------------- classify (test any email text) and the website demo
class ClassifyIn(BaseModel):
    subject: str = ""
    body: str
    sender: str = ""
    what: Union[str, List[Union[str, Dict[str, Any]]], Dict[str, Any]] = "enquiry"


def _score(subject: str, body: str, sender: str, what, uid: str = "") -> Dict[str, Any]:
    try:
        whats = presets.resolve(what, uid)
        item = Item(id="adhoc", title=subject, text=body, sender=sender, kind="email")
        return engine.score(item, whats)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError:
        raise HTTPException(503, "WatchFox is warming up. Try again in a moment.", headers={"Retry-After": "15"})


@app.post("/v1/classify")
def classify(body: ClassifyIn, request: Request, user=Depends(current_user)):
    require_verified(user)
    abuse.free_gate(user, request)
    limiter.hit("classify", user["id"], config.CLASSIFY_PER_MIN, 60)
    if not (body.subject or body.body):
        raise HTTPException(400, "Send a subject and/or body.")
    require_run(user)
    results = _score(body.subject, body.body, body.sender, body.what, user["id"])
    billing.add_run(user["id"])
    return {"matched": [r["label"] for r in results.values() if r["matched"]], "results": results}


class DemoIn(BaseModel):
    text: str
    what: str = "enquiry"


@app.post("/v1/demo")
def demo(body: DemoIn, request: Request):
    """Public, rate-limited. Powers the 'try it' box on the website."""
    limiter.hit("demo", client_ip(request), 10, 60)
    limiter.hit("demo-day", client_ip(request), 100, 86400)
    limiter.hit("demo-all", "all", 240, 60)              # the public box can never take more than this much of the agent, however many callers
    text = body.text.strip()[:1500]
    if not text:
        raise HTTPException(400, "Paste some email text.")
    if body.what not in presets.PRESETS:
        raise HTTPException(400, "Unknown what.")
    r = next(iter(_score("", text, "", body.what).values()))
    return {"label": r["label"], "matched": r["matched"], "excluded": r["excluded"], "score": r["score"], "threshold": r["threshold"]}


app.include_router(accounts.router)
app.include_router(admin.router)
app.include_router(payments.router)


# ---------------------------------------------------------------- website
if config.WEB_DIST.exists():
    if (config.WEB_DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=config.WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        root = config.WEB_DIST.resolve()
        f = (root / path).resolve()
        if path and f.is_file() and f.is_relative_to(root):          # never serve anything outside the website folder
            return FileResponse(f)
        return FileResponse(root / "index.html")
