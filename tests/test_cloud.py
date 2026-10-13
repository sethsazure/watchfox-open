"""Tests for the connect flow, reconnect alerts, metadata and Microsoft push. They use a fake scorer and a mock
Microsoft Graph, so no model, mailbox or internet is needed."""
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

os.environ["WATCHFOX_HOME"] = tempfile.mkdtemp()
os.environ["WATCHFOX_ALLOW_INSECURE_WEBHOOKS"] = "1"

import httpx  # noqa: E402
import pytest  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import PlainTextResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from watchfox import config, graph, presets, push, store, vault  # noqa: E402
from watchfox.engine import engine  # noqa: E402
from watchfox.runner import runner  # noqa: E402
from watchfox.sources import AuthError, imap as imap_mod  # noqa: E402
from watchfox.server import app  # noqa: E402

RECEIVED = []


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        RECEIVED.append((dict(self.headers), json.loads(body), body))
        self.send_response(500 if self.path.startswith("/fail") else 204); self.end_headers()

    def log_message(self, *a): pass


def fake_score(item, whats, wait=False):
    t = (item.title + " " + item.text).lower()
    hit = "quote" in t
    out = {}
    for w in whats:
        score = 0.9 if hit else 0.05
        pct = presets.confidence(score, w["threshold"])
        out[w["id"]] = {"label": w["label"], "score": score, "threshold": w["threshold"], "exclude_score": None,
                        "matched": hit, "excluded": False, "reason": None, "confidence": pct,
                        "confidence_level": presets.confidence_level(pct)}
    return out


def signup(client, body):
    """Create an account and mark its email verified, as clicking the emailed link would."""
    r = client.post("/web/signup", json=body)
    if r.status_code == 200:
        store.set_verified(r.json()["id"])
    return r


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture(scope="module")
def env():
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "ALLOW_INSECURE_WEBHOOKS", True)      # lets the tests use a local http:// receiver
    mp.setattr(config, "ENFORCE_PLANS", False)               # plan limits have their own tests
    mp.setattr(config, "MAX_CONNECTIONS", 100)               # the tests share one account
    mp.setattr(config, "MAX_WATCHES", 100)
    mp.setattr(engine, "load", lambda: None)
    mp.setattr(engine, "score", fake_score)
    mp.setattr(imap_mod.Imap, "poll", lambda self, seen, limit: iter(()))      # never touch a real mail server
    threading.Thread(target=HTTPServer(("127.0.0.1", 9102), Hook).serve_forever, daemon=True).start()
    with TestClient(app, base_url="http://localhost:8797") as client:
        signup(client, {"email": "dev@example.com", "password": "correct horse battery"})
        key = client.post("/v1/keys", json={"name": "t"}).json()["key"]
        yield client, {"Authorization": f"Bearer {key}"}
    mp.undo()


def ok_check(mp):
    mp.setattr(imap_mod.Imap, "check", lambda self: None)


# ------------------------------------------------------------------ the Gmail connect page
def test_gmail_connect_flow_with_state_and_metadata(env, monkeypatch):
    c, H = env
    ok_check(monkeypatch)
    link = c.post("/v1/connect-links", headers=H, json={
        "provider": "gmail", "redirect_url": "https://saas.example.com/done", "state": "cust_42",
        "metadata": {"customer_id": "42"}, "app_name": "ExampleCorp"}).json()
    t = parse_qs(urlparse(link["url"]).query)["t"][0]
    s = c.get("/v1/connect/session", params={"t": t}).json()
    assert s["provider"] == "gmail" and s["app_name"] == "ExampleCorp" and s["return_to"] == "saas.example.com"
    r = c.post("/v1/connect/gmail", json={"t": t, "email": "Me@Gmail.com", "app_password": "abcd efgh ijkl mnop"})
    assert r.status_code == 200
    q = parse_qs(urlparse(r.json()["redirect"]).query)
    assert q["status"] == ["connected"] and q["state"] == ["cust_42"] and q["email"] == ["me@gmail.com"]
    conn = c.get("/v1/connections", headers=H).json()[-1]
    assert conn["provider"] == "gmail" and conn["method"] == "app_password" and conn["metadata"] == {"customer_id": "42"}
    assert conn["status"] == "active" and "abcdefghijklmnop" not in json.dumps(conn) and "secret" not in conn


def test_gmail_connect_rejects_bad_input_and_bad_links(env, monkeypatch):
    c, H = env
    t = parse_qs(urlparse(c.post("/v1/connect-links", headers=H, json={"provider": "gmail"}).json()["url"]).query)["t"][0]
    assert c.post("/v1/connect/gmail", json={"t": t, "email": "nope", "app_password": "abcdefghijklmnop"}).status_code == 400
    r = c.post("/v1/connect/gmail", json={"t": t, "email": "a@gmail.com", "app_password": "my normal password 1"})
    assert r.status_code == 400 and "16 letters" in r.json()["detail"]

    def reject(self): raise ValueError("Google did not accept this email address and app password.")
    monkeypatch.setattr(imap_mod.Imap, "check", reject)
    r = c.post("/v1/connect/gmail", json={"t": t, "email": "a@gmail.com", "app_password": "abcdefghijklmnop"})
    assert r.status_code == 400 and "did not accept" in r.json()["detail"]
    assert c.get("/v1/connect/session", params={"t": "garbage"}).status_code == 400
    assert c.post("/v1/connect-links", headers=H, json={"provider": "gmail", "redirect_url": "http://evil.example.com"}).status_code == 400
    assert c.post("/v1/connect-links", headers=H, json={"provider": "gmail", "state": "x" * 300}).status_code == 400
    monkeypatch.setattr(config, "MS_CLIENT_ID", "")          # the tests must not depend on a real .env
    assert c.post("/v1/connect-links", headers=H, json={"provider": "m365"}).status_code == 503   # Microsoft not set up


def test_wrong_secret_never_logs_in_twice_after_flag(env, monkeypatch):
    """A rejected sign-in must stop the checks: repeated bad logins can lock a Google account."""
    c, H = env
    ok_check(monkeypatch)
    cn = c.post("/v1/connections", headers=H, json={"provider": "gmail", "user": "lock@gmail.com", "password": "abcd efgh ijkl mnop"}).json()
    w = c.post("/v1/watches", headers=H, json={"connection_id": cn["id"], "what": "enquiry"}).json()
    calls = []
    monkeypatch.setattr(imap_mod.Imap, "poll", lambda self, seen, limit: (calls.append(1), (_ for _ in ()).throw(AuthError("rejected")))[1])
    runner.stop_connection(cn["id"]); runner.start_connection(cn["id"])
    assert wait_for(lambda: c.get(f"/v1/connections/{cn['id']}", headers=H).json()["status"] == "needs_attention")
    n = len(calls)
    time.sleep(1.5)
    assert len(calls) == n          # no more attempts while the mailbox needs the person


# ------------------------------------------------------------------ alerts, reconnect, metadata in webhooks
def test_needs_attention_webhook_then_reconnect(env, monkeypatch):
    c, H = env
    ok_check(monkeypatch)
    RECEIVED.clear()
    cn = c.post("/v1/connections", headers=H, json={"provider": "gmail", "user": "alert@gmail.com", "password": "abcdefghijklmnop",
                                                    "metadata": {"customer_id": "7"}}).json()
    w = c.post("/v1/watches", headers=H, json={"connection_id": cn["id"], "what": "enquiry",
                                               "webhook_url": "http://127.0.0.1:9102/hook", "metadata": {"plan": "pro"}}).json()
    conn = store.connection(cn["id"])
    runner.flag(conn, "Google rejected the app password.")
    assert wait_for(lambda: any(r[1]["type"] == "connection.needs_attention" for r in RECEIVED))
    hdrs, ev, raw = next(r for r in RECEIVED if r[1]["type"] == "connection.needs_attention")
    assert ev["connection_id"] == cn["id"] and ev["email"] == "alert@gmail.com" and ev["metadata"] == {"customer_id": "7"}
    ts, sig = [p.split("=", 1)[1] for p in hdrs["X-WatchFox-Signature"].split(",")]
    assert hmac.compare_digest(sig, hmac.new(w["webhook_secret"].encode(), f"{ts}.{raw}".encode(), hashlib.sha256).hexdigest())
    n = len(RECEIVED); runner.flag(store.connection(cn["id"]), "again"); time.sleep(0.5)
    assert len(RECEIVED) == n                                   # flagged once, not repeatedly
    assert c.get(f"/v1/connections/{cn['id']}", headers=H).json()["status"] == "needs_attention"

    # the person reconnects through a reconnect link: same mailbox, status cleared, watches kept
    link = c.post("/v1/connect-links", headers=H, json={"connection_id": cn["id"]}).json()
    t = parse_qs(urlparse(link["url"]).query)["t"][0]
    assert c.get("/v1/connect/session", params={"t": t}).json()["reconnect_email"] == "alert@gmail.com"
    bad = c.post("/v1/connect/gmail", json={"t": t, "email": "someone.else@gmail.com", "app_password": "abcdefghijklmnop"})
    assert bad.status_code == 400 and "alert@gmail.com" in bad.json()["detail"]
    good = c.post("/v1/connect/gmail", json={"t": t, "email": "alert@gmail.com", "app_password": "zzzzyyyyxxxxwwww"})
    assert good.status_code == 200 and good.json()["connection_id"] == cn["id"]
    after = c.get(f"/v1/connections/{cn['id']}", headers=H).json()
    assert after["status"] == "active" and after["metadata"] == {"customer_id": "7"}
    assert [x["id"] for x in c.get("/v1/watches", headers=H).json() if x["connection_id"] == cn["id"]] == [w["id"]]


def test_metadata_is_echoed_in_match_webhooks(env):
    c, H = env
    RECEIVED.clear()
    cn = c.post("/v1/connections", headers=H, json={"provider": "sandbox", "metadata": {"customer_id": "9"}}).json()
    c.post("/v1/watches", headers=H, json={"connection_id": cn["id"], "what": "enquiry", "metadata": {"team": "sales"},
                                           "webhook_url": "http://127.0.0.1:9102/hook"})
    c.post(f"/v1/connections/{cn['id']}/sandbox-emails", headers=H, json={"subject": "Please quote", "body": "Quote for 5 units?"})
    assert wait_for(lambda: any(r[1]["type"] == "email.matched" for r in RECEIVED), 15)
    ev = next(r[1] for r in RECEIVED if r[1]["type"] == "email.matched")
    assert ev["metadata"] == {"customer_id": "9", "team": "sales"}


# ------------------------------------------------------------------ Microsoft push against a mock Graph
class MockGraph:
    def __init__(self):
        self.subs, self.calls, self.messages, self.counter, self.mime = {}, [], {}, 0, {}
        g = FastAPI()
        self.app = g

        @g.post("/subscriptions")
        async def create(request: Request):
            body = await request.json()
            self.calls.append(("POST", body))
            self.counter += 1
            tok = f"validate-{self.counter}"
            r = httpx.post(body["notificationUrl"], params={"validationToken": tok}, timeout=10)      # Graph checks our address first
            if r.text != tok:
                return PlainTextResponse("validation failed", status_code=400)
            sid = f"sub-{self.counter}"
            self.subs[sid] = body
            return {"id": sid, "expirationDateTime": body["expirationDateTime"], "clientState": body["clientState"]}

        @g.patch("/subscriptions/{sid}")
        async def renew(sid: str, request: Request):
            body = await request.json()
            self.calls.append(("PATCH", sid))
            if sid not in self.subs:
                return PlainTextResponse("gone", status_code=404)
            return {"id": sid, "expirationDateTime": body["expirationDateTime"]}

        @g.delete("/subscriptions/{sid}")
        async def delete(sid: str):
            self.calls.append(("DELETE", sid)); self.subs.pop(sid, None); return {}

        @g.get("/me/messages/{mid}/$value")
        async def mime(mid: str):
            from fastapi.responses import Response as R
            return R(content=self.mime.get(mid, b"From: a@b.c\nSubject: raw\n\nraw body"), media_type="message/rfc822")

        @g.get("/me/messages/{mid}")
        async def message(mid: str):
            return self.messages[mid]


def test_microsoft_push_end_to_end(env, monkeypatch):
    c, H = env
    mock = MockGraph()
    threading.Thread(target=uvicorn.Server(uvicorn.Config(mock.app, port=9301, log_level="error")).run, daemon=True).start()
    ours = uvicorn.Server(uvicorn.Config(app, port=8797, log_level="error"))
    threading.Thread(target=ours.run, daemon=True).start()
    assert wait_for(lambda: ours.started, 10)
    monkeypatch.setattr(config, "GRAPH_URL", "http://localhost:9301")
    monkeypatch.setattr(config, "BASE_URL", "http://localhost:8797")
    monkeypatch.setattr(config, "PUSH_ALLOW_HTTP", True)
    monkeypatch.setattr(graph, "token_for", lambda conn: "fake-token")
    RECEIVED.clear()

    uid = c.get("/web/me", headers=H).json()["id"]
    cid = store.upsert_connection(uid, "m365", "boss@contoso.com", "cache", {"customer_id": "55"})
    w = c.post("/v1/watches", headers=H, json={"connection_id": cid, "what": "enquiry", "webhook_url": "http://127.0.0.1:9102/hook"}).json()

    # 1. subscribing: Graph validates our address, we answer, and the mailbox is marked as push
    push.ensure(store.connection(cid))
    sub = store.subscription_for(cid)
    assert sub and store.connection(cid)["delivery"] == "push"
    body = mock.calls[0][1]
    assert body["resource"] == "me/mailFolders('inbox')/messages" and body["changeType"] == "created"
    assert body["notificationUrl"].endswith("/hooks/m365/notify") and body["lifecycleNotificationUrl"].endswith("/hooks/m365/lifecycle")
    assert c.get(f"/v1/connections/{cid}", headers=H).json()["delivery"] == "push"

    # 2. a notification with the right clientState becomes an email, a check, and a signed webhook
    mock.messages["AAA"] = {"id": "AAA", "subject": "Please quote", "receivedDateTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 5)),
                            "from": {"emailAddress": {"name": "Priya", "address": "priya@acme.com"}}, "body": {"contentType": "text", "content": "Quote for 40 licences?"}, "hasAttachments": False}
    note = {"value": [{"subscriptionId": sub["id"], "clientState": sub["client_state"], "changeType": "created", "resourceData": {"id": "AAA"}}]}
    assert httpx.post("http://localhost:8797/hooks/m365/notify", json=note).status_code == 202
    assert wait_for(lambda: any(r[1]["type"] == "email.matched" for r in RECEIVED), 15)
    ev = next(r[1] for r in RECEIVED if r[1]["type"] == "email.matched")
    assert ev["email"]["subject"] == "Please quote" and ev["metadata"] == {"customer_id": "55"} and ev["email"]["from"].endswith("<priya@acme.com>")

    # 2b. the matched email can be downloaded, read live from Microsoft; an email that never matched cannot
    mock.mime["AAA"] = b"From: priya@acme.com\nSubject: Please quote\n\nQuote for 40 licences?"
    got = c.get(f"/v1/connections/{cid}/emails/AAA/raw", headers=H)
    assert got.status_code == 200 and got.content == mock.mime["AAA"] and got.headers["cache-control"] == "no-store"
    assert c.get(f"/v1/connections/{cid}/emails/NOT-SEEN/raw", headers=H).status_code == 404
    with monkeypatch.context() as limit:                      # over the download limit: refused with 413, not read in full
        limit.setattr(config, "MAX_DOWNLOAD_BYTES", 10)
        big = c.get(f"/v1/connections/{cid}/emails/AAA/raw", headers=H)
        assert big.status_code == 413 and "download limit" in big.json()["detail"]

    # 3. a notification with the wrong clientState is ignored; a repeat of the same email is not processed twice
    n = len(RECEIVED)
    forged = {"value": [{"subscriptionId": sub["id"], "clientState": "wrong", "changeType": "created", "resourceData": {"id": "AAA"}}]}
    httpx.post("http://localhost:8797/hooks/m365/notify", json=forged)
    httpx.post("http://localhost:8797/hooks/m365/notify", json=note)
    time.sleep(1.5)
    assert len(RECEIVED) == n

    # 4. the validation handshake works on both endpoints
    for path in ("notify", "lifecycle"):
        r = httpx.post(f"http://localhost:8797/hooks/m365/{path}", params={"validationToken": "abc 123"})
        assert r.status_code == 200 and r.text == "abc 123" and r.headers["content-type"].startswith("text/plain")

    # 5. renewal: not yet due -> untouched; past half its life -> renewed with PATCH; expired and gone -> recreated
    patches = lambda: [x for x in mock.calls if x[0] == "PATCH"]  # noqa: E731
    push.ensure(store.connection(cid)); assert not patches()
    store.x("UPDATE subscriptions SET created=?, expires=? WHERE id=?", (time.time() - 3000, time.time() + 1000, sub["id"]))
    push.ensure(store.connection(cid)); assert len(patches()) == 1
    assert store.subscription_for(cid)["expires"] > time.time() + 3600
    mock.subs.clear()                                         # Graph forgets the subscription
    store.x("UPDATE subscriptions SET created=?, expires=? WHERE id=?", (time.time() - 3000, time.time() + 1000, sub["id"]))
    push.ensure(store.connection(cid))
    assert store.subscription_for(cid)["id"] != sub["id"] and store.connection(cid)["delivery"] == "push"

    # 6. lifecycle messages: removal recreates the subscription
    sub2 = store.subscription_for(cid)
    life = {"value": [{"subscriptionId": sub2["id"], "clientState": sub2["client_state"], "lifecycleEvent": "subscriptionRemoved"}]}
    httpx.post("http://localhost:8797/hooks/m365/lifecycle", json=life)
    assert wait_for(lambda: (store.subscription_for(cid) or {}).get("id") not in (None, sub2["id"]), 10)

    # 7. a revoked Microsoft sign-in raises the needs-attention alert instead of failing quietly
    def revoked(conn): raise AuthError("The Microsoft sign-in expired or was revoked.")
    monkeypatch.setattr(graph, "token_for", revoked)
    push.ensure(store.connection(cid))
    assert store.connection(cid)["status"] == "needs_attention"
    assert wait_for(lambda: any(r[1]["type"] == "connection.needs_attention" for r in RECEIVED), 10)

    # 8. without a public https address push is not attempted and the mailbox is simply polled
    monkeypatch.setattr(config, "BASE_URL", "http://localhost:8797"); monkeypatch.setattr(config, "PUSH_ALLOW_HTTP", False)
    store.set_connection_status(cid, "active", None)
    monkeypatch.setattr(graph, "token_for", lambda conn: "fake-token")
    store.drop_subscription(cid)
    push.ensure(store.connection(cid))
    c2 = store.connection(cid)
    assert c2["delivery"] == "polling" and "https" in c2["delivery_note"]
    ours.should_exit = True


# ------------------------------------------------------------------ full email and attachments, zero retention
def test_include_body_eml_and_attachments_and_zero_retention(env):
    import base64
    c, H = env
    RECEIVED.clear()
    cn = c.post("/v1/connections", headers=H, json={"provider": "sandbox"}).json()
    bad = c.post("/v1/watches", headers=H, json={"connection_id": cn["id"], "what": "enquiry", "include": ["everything"], "webhook_url": "http://127.0.0.1:9102/h"})
    assert bad.status_code == 400
    assert c.post("/v1/watches", headers=H, json={"connection_id": cn["id"], "what": "enquiry", "include": ["eml"]}).status_code == 400   # no webhook
    w = c.post("/v1/watches", headers=H, json={"connection_id": cn["id"], "what": "enquiry", "webhook_url": "http://127.0.0.1:9102/h",
                                               "include": ["body", "eml", "attachments"]}).json()
    assert w["include"] == ["attachments", "body", "eml"]
    pdf = b"%PDF-1.4 SECRET-ATTACHMENT-BYTES " + bytes(range(256))
    long_body = "Please quote 40 licences. " + "filler text. " * 60 + "END-MARKER-BEYOND-300-CHARS"
    c.post(f"/v1/connections/{cn['id']}/sandbox-emails", headers=H, json={
        "subject": "Please quote", "body": long_body, "sender": "priya@acme.com",
        "attachments": [{"filename": "spec.pdf", "content_type": "application/pdf", "content_base64": base64.b64encode(pdf).decode()}]})
    assert wait_for(lambda: any(r[1]["type"] == "email.matched" for r in RECEIVED), 15)
    ev = next(r[1] for r in RECEIVED if r[1]["type"] == "email.matched")
    em = ev["email"]
    assert em["body"].endswith("END-MARKER-BEYOND-300-CHARS")                       # the full text, not the 300-character snippet
    assert b"Subject: Please quote" in base64.b64decode(em["eml_base64"])           # the whole .eml
    att = em["attachments"][0]
    assert att["filename"] == "spec.pdf" and att["content_type"] == "application/pdf" and att["size"] == len(pdf)
    assert base64.b64decode(att["content_base64"]) == pdf                           # the file itself
    assert em["links"]["raw"].endswith("/raw") and att["url"].endswith("/attachments/0")

    # the same things by download
    eid = em["id"]
    raw = c.get(f"/v1/connections/{cn['id']}/emails/{eid}/raw", headers=H)
    assert raw.status_code == 200 and raw.headers["content-type"].startswith("message/rfc822") and raw.headers["cache-control"] == "no-store"
    assert base64.b64decode(em["eml_base64"]) == raw.content
    lst = c.get(f"/v1/connections/{cn['id']}/emails/{eid}/attachments", headers=H).json()
    assert lst[0]["filename"] == "spec.pdf" and lst[0]["size"] == len(pdf) and "data" not in lst[0]
    one = c.get(f"/v1/connections/{cn['id']}/emails/{eid}/attachments/0", headers=H)
    assert one.content == pdf and one.headers["content-type"] == "application/pdf" and "spec.pdf" in one.headers["content-disposition"]
    assert c.get(f"/v1/connections/{cn['id']}/emails/{eid}/attachments/9", headers=H).status_code == 404

    # an email that did not match cannot be downloaded (a developer must not browse the rest of the mailbox)
    c.post(f"/v1/connections/{cn['id']}/sandbox-emails", headers=H, json={"subject": "Lunch", "body": "Lunch at noon?"})
    assert wait_for(lambda: any(i["email"]["subject"] == "Lunch" for i in c.get("/v1/emails", headers=H).json()), 15)
    lunch = next(i for i in c.get("/v1/emails", headers=H).json() if i["email"]["subject"] == "Lunch")
    assert c.get(f"/v1/connections/{cn['id']}/emails/{lunch['email']['id']}/raw", headers=H).status_code == 404

    # another account cannot download it
    c2 = TestClient(app, base_url="http://localhost:8797")
    signup(c2, {"email": "intruder@example.com", "password": "another long password"})
    k2 = {"Authorization": "Bearer " + c2.post("/v1/keys", json={"name": "x"}).json()["key"]}
    assert c2.get(f"/v1/connections/{cn['id']}/emails/{eid}/raw", headers=k2).status_code == 404

    # zero retention: nothing but the short snippet and scores is kept about the email, and no file bytes
    rows = store.q("SELECT * FROM items WHERE item_id=?", (eid,))
    blob = json.dumps(rows, default=str) + json.dumps(store.q("SELECT * FROM deliveries"), default=str)
    assert "END-MARKER-BEYOND-300-CHARS" not in blob and "SECRET-ATTACHMENT-BYTES" not in blob
    assert len(rows[0]["snippet"]) <= 300 and "spec.pdf" in rows[0]["attachments"]      # only the file's name is remembered


def test_imap_fetch_reads_live_and_checks_the_mailbox_id(monkeypatch):
    from watchfox import mailfetch, vault

    raw = b"From: a@b.c\nSubject: hello\n\nbody"

    class FakeIMAP:
        def response(self, name): return ("OK", [b"777"])
        def uid(self, cmd, uid, spec):
            if "SIZE" in spec:
                return ("OK", [b"1 (UID %s RFC822.SIZE %d)" % (uid.encode(), len(raw))] if uid == "45" else [None])
            return ("OK", [(b"1 (BODY[] {n})", raw), b")"] if uid == "45" else [None])
        def logout(self): pass

    monkeypatch.setattr(imap_mod.Imap, "_connect", lambda self: FakeIMAP())
    conn = {"id": "cn_x", "provider": "gmail", "secret": vault.encrypt({"host": "h", "user": "u", "password": "p"})}
    assert mailfetch.fetch_raw(conn, "777:45") == raw
    for gone in ("999:45", "777:46", "garbage"):          # a rebuilt mailbox, a deleted message, a bad id
        with pytest.raises(mailfetch.Gone):
            mailfetch.fetch_raw(conn, gone)
    monkeypatch.setattr(config, "MAX_DOWNLOAD_BYTES", 10)  # a message over the limit is refused before it is read
    with pytest.raises(mailfetch.TooLarge):
        mailfetch.fetch_raw(conn, "777:45")


def test_eml_without_include_still_lists_attachments_and_links(env):
    import base64
    c, H = env
    RECEIVED.clear()
    cn = c.post("/v1/connections", headers=H, json={"provider": "sandbox"}).json()
    c.post("/v1/watches", headers=H, json={"connection_id": cn["id"], "what": "enquiry", "webhook_url": "http://127.0.0.1:9102/h"})
    c.post(f"/v1/connections/{cn['id']}/sandbox-emails", headers=H, json={
        "subject": "Quote please", "body": "Please quote", "attachments": [{"filename": "a.txt", "content_type": "text/plain",
                                                                        "content_base64": base64.b64encode(b"hello").decode()}]})
    assert wait_for(lambda: any(r[1]["type"] == "email.matched" for r in RECEIVED), 15)
    em = next(r[1] for r in RECEIVED if r[1]["type"] == "email.matched")["email"]
    assert "body" not in em and "eml_base64" not in em                      # nothing inline unless asked
    assert em["attachments"][0]["size"] == 5 and "content_base64" not in em["attachments"][0] and em["attachments"][0]["url"]


def test_activity_log_shows_decisions_and_webhook_results(env, monkeypatch):
    import watchfox.runner as runner_mod
    c, H = env
    monkeypatch.setattr(runner_mod, "RETRY_DELAYS", [0, 0])            # let the failing webhook give up quickly
    RECEIVED.clear()
    cn = c.post("/v1/connections", headers=H, json={"provider": "sandbox"}).json()
    good = c.post("/v1/watches", headers=H, json={"connection_id": cn["id"], "what": "enquiry", "webhook_url": "http://127.0.0.1:9102/ok"}).json()
    bad = c.post("/v1/watches", headers=H, json={"connection_id": cn["id"], "what": "enquiry", "webhook_url": "http://127.0.0.1:9102/fail"}).json()
    c.post(f"/v1/connections/{cn['id']}/sandbox-emails", headers=H, json={"subject": "Please quote 40 units", "body": "Quote for 40 units?"})
    c.post(f"/v1/connections/{cn['id']}/sandbox-emails", headers=H, json={"subject": "Lunch on Friday", "body": "Are you free for lunch?"})
    log = lambda **q: c.get("/v1/log", headers=H, params={"connection_id": cn["id"], **q}).json()   # noqa: E731
    assert wait_for(lambda: len(log()["items"]) == 4 and all(i["delivery"] and i["delivery"]["status"] in ("delivered", "failed")
                                                           for i in log(matched=True)["items"]), 20)
    items = log()["items"]
    assert [i["checked_at"] for i in items] == sorted((i["checked_at"] for i in items), reverse=True)
    hits = [i for i in items if i["matched"]]
    assert len(hits) == 2 and all(i["email"]["subject"] == "Please quote 40 units" for i in hits)
    m = hits[0]["matches"][0]
    assert m["what"] == "enquiry" and 0 < m["confidence"] <= 99 and m["confidence_level"] in ("high", "medium", "low")
    by_watch = {i["watch_id"]: i["delivery"] for i in hits}
    assert by_watch[good["id"]]["status"] == "delivered" and by_watch[good["id"]]["http_status"] == 204 and by_watch[good["id"]]["delivered_at"]
    assert by_watch[bad["id"]]["status"] == "failed" and by_watch[bad["id"]]["http_status"] == 500 and by_watch[bad["id"]]["attempts"] == 2
    assert by_watch[bad["id"]]["webhook_url"].endswith("/fail") and by_watch[bad["id"]]["error"] == "HTTP 500"
    assert all(i["delivery"] is None and not i["matches"] for i in items if not i["matched"])        # not matched: no webhook
    assert hits[0]["links"]["raw"].endswith("/raw") and "body" not in json.dumps(items)               # no content in the log
    # filters
    assert len(log(matched=True)["items"]) == 2 and len(log(matched=False)["items"]) == 2
    assert [i["watch_id"] for i in log(delivery="failed")["items"]] == [bad["id"]]
    assert len(log(delivery="delivered")["items"]) == 1 and len(log(delivery="none")["items"]) == 2
    assert {i["email"]["subject"] for i in log(q="lunch")["items"]} == {"Lunch on Friday"}
    assert len(log(watch_id=good["id"])["items"]) == 2 and len(log(what="enquiry")["items"]) == 2 and not log(what="invoice")["items"]
    assert not log(since=time.time() + 60)["items"]
    assert c.get("/v1/log", headers=H, params={"delivery": "weird"}).status_code == 400
    # paging: one at a time, newest first, nothing repeated or skipped
    seen, before = [], 0
    for _ in range(10):
        page = log(limit=1, **({"before": before} if before else {}))
        seen += [i["id"] for i in page["items"]]
        before = page["next_before"]
        if not before:
            break
    assert seen == [i["id"] for i in items] and len(set(seen)) == 4
    # matches carry confidence, the webhook payload does too, and another account sees nothing
    assert all("confidence" in r for mt in c.get("/v1/matches", headers=H, params={"watch_id": good["id"]}).json() for r in mt["results"].values())
    assert RECEIVED and all(m["confidence"] for r in RECEIVED if r[1]["type"] == "email.matched" for m in r[1]["matches"])
    c3 = TestClient(app, base_url="http://localhost:8797")
    signup(c3, {"email": "log-intruder@example.com", "password": "another long password"})
    k3 = {"Authorization": "Bearer " + c3.post("/v1/keys", json={"name": "x"}).json()["key"]}
    assert c3.get("/v1/log", headers=k3).json()["items"] == []


# ------------------------------------------------------------------ your own types
def test_custom_type_create_use_and_delete(env, monkeypatch):
    c, h = env
    from watchfox.tuning import tuner
    fake = {"label": "Quotes", "description": "x", "question": "Is it a quote?", "threshold": 0.4,
            "excludes": [{"question": "Is it spam?", "threshold": 0.6}], "skip_bulk": True,
            "report": {"samples": {"should_catch": 3, "should_ignore": 100}, "caught": 3, "missed": 0, "mistakes": []}}
    from watchfox.tuning import llm
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")           # the tests must never call the real assistant
    monkeypatch.setattr(tuner, "tune", lambda *a, **k: dict(fake, report=dict(fake["report"])))
    assert c.post("/v1/types/suggest", headers=h, json={"sentence": "late shipments from suppliers"}).status_code == 501
    assert c.post("/v1/types/tune", headers=h, json={"label": "Q", "description": "short"}).status_code == 400
    assert c.post("/v1/types/tune", headers=h, json={"label": "Q", "description": "A long enough description"}).status_code == 400   # no examples
    body = {"label": "Quotes", "description": "A long enough description",
            "positives": [{"subject": f"s{i}", "body": "please quote"} for i in range(3)]}
    drafted = c.post("/v1/types/tune", headers=h, json=body).json()
    assert drafted["question"] == "Is it a quote?" and c.get("/v1/types/kinds", headers=h).json()["kinds"]
    tid = c.post("/v1/types", headers=h, json=drafted).json()["id"]
    assert [t["id"] for t in c.get("/v1/types", headers=h).json()] == [tid]
    r = c.post("/v1/classify", headers=h, json={"subject": "quote please", "body": "need a quote", "what": tid}).json()
    assert r["matched"] == ["Quotes"]
    assert c.post("/v1/classify", headers=h, json={"body": "x", "what": "type_nope"}).status_code == 400
    assert c.delete(f"/v1/types/{tid}", headers=h).status_code == 200
    assert c.delete(f"/v1/types/{tid}", headers=h).status_code == 404


def test_assistant_only_on_setup_routes_and_corrections(env, monkeypatch):
    c, h = env
    from watchfox.tuning import llm, tuner
    import inspect
    from watchfox import engine as eng_mod, runner as run_mod
    assert "llm" not in inspect.getsource(eng_mod) and "llm" not in inspect.getsource(run_mod)     # catching never uses the assistant
    monkeypatch.setattr(config, "OPENAI_API_KEY", "x")
    monkeypatch.setattr(llm, "suggest", lambda s, kinds: {"label": "Late shipments", "description": "A supplier saying an order is late.",
                        "positives": [{"subject": "a", "body": "late"}], "negatives": [{"subject": "b", "body": "on time"}], "similar_kinds": ["shipping"]})
    r = c.post("/v1/types/suggest", headers=h, json={"sentence": "late shipments from suppliers"}).json()
    assert r["label"] == "Late shipments" and r["similar_kinds"] == ["shipping"]
    monkeypatch.setattr(llm, "examples", lambda *a: {"positives": [{"subject": "p", "body": "x"}], "negatives": []})
    assert c.post("/v1/types/examples", headers=h, json={"label": "L", "description": "A long enough description"}).json()["positives"]
    # a wrong catch needs a checked email; without one it is refused
    assert c.post("/v1/feedback", headers=h, json={"what": "enquiry", "watch_id": "wt_none", "email_id": "x", "kind": "wrong"}).status_code == 404
    assert c.post("/v1/feedback", headers=h, json={"what": "enquiry", "watch_id": "w", "email_id": "x", "kind": "bad"}).status_code == 400
    assert c.get("/v1/corrections", headers=h).json() == []


def test_edit_watch(env):
    c, h = env
    cid = c.post("/v1/connections", headers=h, json={"provider": "sandbox"}).json()["id"]
    w = c.post("/v1/watches", headers=h, json={"connection_id": cid, "what": "invoice"}).json()
    r = c.patch(f"/v1/watches/{w['id']}", headers=h, json={"what": ["invoice", "claim"], "webhook_url": "http://127.0.0.1:9102/ok"}).json()
    assert r["labels"] == ["Invoices", "Claims"] and r["webhook_url"].endswith("/ok") and r["webhook_secret"].startswith("whsec_")
    assert c.patch(f"/v1/watches/{w['id']}", headers=h, json={"what": []}).status_code == 400
    assert c.patch(f"/v1/watches/{w['id']}", headers=h, json={"what": ["nope"]}).status_code == 400
    r = c.patch(f"/v1/watches/{w['id']}", headers=h, json={"webhook_url": ""}).json()
    assert r["webhook_url"] is None and "webhook_secret" not in r
    assert c.patch("/v1/watches/wt_none", headers=h, json={"what": ["claim"]}).status_code == 404


def test_log_export_csv_and_retention(env):
    c, h = env
    cid = c.post("/v1/connections", headers=h, json={"provider": "sandbox"}).json()["id"]
    wid = c.post("/v1/watches", headers=h, json={"connection_id": cid, "what": "enquiry"}).json()["id"]
    for subj in ("Please quote this", "=HYPERLINK(\"x\")"):
        c.post(f"/v1/connections/{cid}/sandbox-emails", headers=h, json={"sender": "a@b.com", "subject": subj, "body": "body"})
    assert wait_for(lambda: len(c.get("/v1/log", headers=h, params={"watch_id": wid}).json()["items"]) == 2)
    assert c.get("/v1/log", headers=h).json()["retention_days"] == 90
    r = c.get("/v1/log/export", headers=h, params={"watch_id": wid})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("checked_at,") and len(lines) == 3
    assert "Please quote this" in r.text and "'=HYPERLINK" in r.text            # formulas are defused
    assert c.get("/v1/log/export", headers=h, params={"watch_id": wid, "matched": "true"}).text.count("\n") == 2     # header + the one caught
    removed = store.prune(time.time() + 10)                                       # everything is older than the cutoff
    assert removed["items"] >= 2
    assert c.get("/v1/log", headers=h, params={"watch_id": wid}).json()["items"] == []


def test_website_cannot_be_used_to_read_other_files(env):
    c, _ = env
    for path in ("/..%2f..%2f.env", "/%2e%2e/%2e%2e/.env", "/..%2f..%2fwatchfox/vault.py", "/....//....//.env"):
        r = c.get(path)
        assert "MS_CLIENT" not in r.text and "def " not in r.text and "OPENAI" not in r.text


# ------------------------------------------------------------------ security: no access without a key, no access to other accounts
PUBLIC = {"/v1/health", "/v1/presets", "/v1/providers", "/v1/demo", "/v1/connect/session", "/v1/connect/gmail", "/v1/openapi.json", "/v1/plans"}


def test_every_api_route_needs_a_key(env):
    c, _ = env
    anon = TestClient(app, base_url="http://localhost:8797")
    bad, checked = [], 0
    for path, ops in app.openapi_orig()["paths"].items():
        if not path.startswith("/v1") or path in PUBLIC or path.startswith("/v1/admin"):      # the admin console answers "not found" to everyone else (its own tests)
            continue
        for m in ops:
            if m.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            url = re.sub(r"\{[^}]+\}", "x", path)
            checked += 1
            code = anon.request(m.upper(), url, json={}).status_code
            if code != 401:
                bad.append((m, path, code))
            if m.upper() == "GET" and anon.get(url, headers={"Authorization": "Bearer wf_live_nope"}).status_code != 401:
                bad.append((m, path, "bad key"))
    assert checked > 40 and not bad, bad


def test_another_account_sees_and_changes_nothing(env, monkeypatch):
    c, h = env
    cid = c.post("/v1/connections", headers=h, json={"provider": "sandbox"}).json()["id"]
    wid = c.post("/v1/watches", headers=h, json={"connection_id": cid, "what": "enquiry"}).json()["id"]
    eid = c.post(f"/v1/connections/{cid}/sandbox-emails", headers=h, json={"sender": "a@b.com", "subject": "secret quote", "body": "need a quote"}).json()
    from watchfox.tuning import tuner
    monkeypatch.setattr(tuner, "tune", lambda *a, **k: {"label": "T", "description": "d", "question": "Is it?", "threshold": 0.4, "excludes": [], "report": {}})
    tid = c.post("/v1/types", headers=h, json={"label": "Mine", "description": "A long enough description", "question": "Is it mine?", "threshold": 0.4}).json()["id"]
    assert wait_for(lambda: c.get("/v1/log", headers=h, params={"watch_id": wid}).json()["items"])
    entry = c.get("/v1/log", headers=h, params={"watch_id": wid}).json()["items"][0]
    other = TestClient(app, base_url="http://localhost:8797")
    signup(other, {"email": "other@example.com", "password": "another long password"})
    oh = {"Authorization": "Bearer " + other.post("/v1/keys", json={"name": "o"}).json()["key"]}
    eml = entry["email"]["id"]
    for method, url, body in [
        ("GET", f"/v1/connections/{cid}", None), ("DELETE", f"/v1/connections/{cid}", None),
        ("POST", f"/v1/connections/{cid}/sandbox-emails", {"sender": "x@y.com", "subject": "s", "body": "b"}),
        ("GET", f"/v1/watches/{wid}", None), ("DELETE", f"/v1/watches/{wid}", None), ("PATCH", f"/v1/watches/{wid}", {"what": ["claim"]}),
        ("POST", f"/v1/watches/{wid}/test", None), ("GET", f"/v1/watches/{wid}/deliveries", None),
        ("GET", f"/v1/connections/{cid}/emails/{eml}/raw", None), ("GET", f"/v1/connections/{cid}/emails/{eml}/attachments", None),
        ("GET", f"/v1/types/{tid}", None), ("PUT", f"/v1/types/{tid}", {"label": "x", "description": "y", "question": "q", "threshold": 0.5}),
        ("DELETE", f"/v1/types/{tid}", None),
        ("POST", "/v1/feedback", {"what": "enquiry", "watch_id": wid, "email_id": eml, "kind": "wrong"}),
        ("POST", f"/v1/watches", {"connection_id": cid, "what": "enquiry"}),
    ]:
        r = other.request(method, url, headers=oh, json=body)
        assert r.status_code in (400, 404), (method, url, r.status_code, r.text[:100])
    assert other.get("/v1/watches", headers=oh).json() == [] and other.get("/v1/connections", headers=oh).json() == []
    assert other.get("/v1/types", headers=oh).json() == [] and other.get("/v1/log", headers=oh).json()["items"] == []
    assert "secret quote" not in other.get("/v1/log/export", headers=oh).text and other.get("/v1/matches", headers=oh).json() == []
    assert other.post("/v1/classify", headers=oh, json={"body": "x", "what": tid}).status_code == 400      # cannot use someone else's type
    assert other.post("/v1/watches", headers=oh, json={"connection_id": "x", "what": tid}).status_code in (400, 404)
    assert c.get(f"/v1/watches/{wid}", headers=h).status_code == 200                                      # still intact for the owner


def test_two_accounts_can_each_tune_a_default_type(env):
    c, h = env
    body = {"label": "Enquiries", "description": "A long enough description", "question": "Is it a quote request?", "threshold": 0.44}
    other = TestClient(app, base_url="http://localhost:8797")
    signup(other, {"email": "second@example.com", "password": "second long password"})
    oh = {"Authorization": "Bearer " + other.post("/v1/keys", json={"name": "o"}).json()["key"]}
    assert c.put("/v1/types/enquiry", headers=h, json=body).status_code == 200
    assert other.put("/v1/types/enquiry", headers=oh, json=dict(body, threshold=0.7)).status_code == 200      # used to collide
    assert presets.resolve(["enquiry"], store.user_by_email("dev@example.com")["id"])[0]["threshold"] == 0.44
    assert presets.resolve(["enquiry"], store.user_by_email("second@example.com")["id"])[0]["threshold"] == 0.7
    assert c.delete("/v1/types/enquiry", headers=h).status_code == 200                                         # reset to built-in
    assert presets.resolve(["enquiry"], store.user_by_email("dev@example.com")["id"])[0]["threshold"] == presets.PRESETS["enquiry"]["threshold"]
    assert presets.resolve(["enquiry"], store.user_by_email("second@example.com")["id"])[0]["threshold"] == 0.7
    # bad shapes are refused, not crashed on
    for bad in ({"excludes": [1]}, {"excludes": [{"question": "x", "threshold": "no"}]}, {"inputs": [1]}, {"inputs": {"positives": "x"}}):
        assert c.put("/v1/types/enquiry", headers=h, json=dict(body, **bad)).status_code == 400
    assert c.put("/v1/types/enquiry", headers=h, json=dict(body, report="x", ignore_subject=["(a+)+$"])).status_code == 200
    for w in ({"question": 5}, {"question": "q", "threshold": "x"}, {"question": "q", "excludes": ["x"]}, {"question": "q", "ignore_subject": ["(a+)+$"]}):
        assert c.post("/v1/classify", headers=h, json={"body": "hi", "what": w}).status_code == 400


# ------------------------------------------------------------------ accounts: verification, reset, team, plans, sign-in providers
def fresh_client():
    return TestClient(app, base_url="http://localhost:8797", follow_redirects=False)


def token_of(link):
    return link.split("token=")[1]


def test_email_must_be_verified_before_using_the_api(env):
    c, _ = env
    k = fresh_client()
    r = k.post("/web/signup", json={"email": "newbie@example.com", "password": "newbie long password"}).json()
    assert r["verified"] is False and "dev_link" in r
    assert k.post("/v1/keys", json={"name": "x"}).status_code == 403
    assert k.get("/web/me").json()["verified"] is False
    assert k.get("/web/verify", params={"token": "garbage"}).headers["location"].startswith("/#/login")
    assert k.get(r["dev_link"].replace("http://localhost:8797", "")).status_code in (302, 307)
    assert k.get("/web/me").json()["verified"] is True
    assert k.post("/v1/keys", json={"name": "x"}).status_code == 200


def test_forgot_and_reset_password(env):
    c, _ = env
    k = fresh_client()
    signup(k, {"email": "forgetful@example.com", "password": "first long password"})
    anon = fresh_client()
    unknown = anon.post("/web/forgot", json={"email": "nobody-here@example.com"}).json()
    assert unknown["ok"] and "dev_link" not in unknown                                   # does not reveal who has an account
    link = anon.post("/web/forgot", json={"email": "forgetful@example.com"}).json()["dev_link"]
    tok = link.split("token=")[1]
    assert anon.post("/web/reset", json={"token": tok, "password": "short"}).status_code == 400
    assert anon.post("/web/reset", json={"token": tok, "password": "second long password"}).status_code == 200
    assert anon.post("/web/reset", json={"token": tok, "password": "third long password"}).status_code == 400    # single use
    assert anon.post("/web/login", json={"email": "forgetful@example.com", "password": "first long password"}).status_code == 401
    assert anon.post("/web/login", json={"email": "forgetful@example.com", "password": "second long password"}).status_code == 200
    assert anon.post("/web/reset", json={"token": "x.y", "password": "whatever long enough"}).status_code == 400
    assert anon.post("/web/password", json={"current": "wrong", "password": "brand new password"}).status_code in (400, 401)
    assert anon.post("/web/password", json={"current": "wrong one", "password": "brand new password"}).status_code == 400
    assert anon.post("/web/password", json={"current": "second long password", "password": "brand new password"}).status_code == 200


def test_team_one_organization_shared_by_developers(env):
    c, h = env
    owner = fresh_client()
    signup(owner, {"email": "boss@example.com", "password": "boss long password"})
    oh = {"Authorization": "Bearer " + owner.post("/v1/keys", json={"name": "k"}).json()["key"]}
    cid = owner.post("/v1/connections", json={"provider": "sandbox"}).json()["id"]
    assert owner.get("/v1/team").json()["can_manage"] is True
    assert owner.post("/v1/team/invites", json={"email": "not an email"}).status_code == 400
    assert owner.post("/v1/team/invites", json={"email": "boss@example.com"}).status_code == 409           # already an account
    inv = owner.post("/v1/team/invites", json={"email": "dev2@example.com"})
    assert inv.status_code == 201 and owner.post("/v1/team/invites", json={"email": "dev2@example.com"}).status_code == 409
    token = token_of(inv.json()["link"])
    info = fresh_client().get("/web/invite", params={"token": token}).json()
    assert info["email"] == "dev2@example.com"
    assert fresh_client().get("/web/invite", params={"token": "nope"}).status_code == 404
    dev = fresh_client()
    assert dev.post("/web/join", json={"token": token, "password": "short"}).status_code == 400
    assert dev.post("/web/join", json={"token": token, "password": "dev two long password"}).status_code == 200
    assert dev.post("/web/join", json={"token": token, "password": "dev two long password"}).status_code == 404   # used
    me = dev.get("/web/me").json()
    assert me["role"] == "member" and me["verified"] is True and me["id"] == owner.get("/web/me").json()["id"]
    # same organization, same data, all features
    assert [x["id"] for x in dev.get("/v1/connections").json()] == [cid]
    mine = dev.post("/v1/keys", json={"name": "from dev"}).json()
    assert any(k["created_by"] == "dev2@example.com" for k in owner.get("/v1/keys").json())
    wid = dev.post("/v1/watches", json={"connection_id": cid, "what": "invoice"}).json()["id"]
    assert owner.get(f"/v1/watches/{wid}").status_code == 200
    # only the owner manages the team and the organization
    assert dev.post("/v1/team/invites", json={"email": "dev3@example.com"}).status_code == 403
    assert dev.patch("/v1/org", json={"name": "x"}).status_code == 403
    assert dev.post("/v1/plan/upgrade", json={"plan": "tracker"}).status_code == 403
    assert owner.patch("/v1/org", json={"name": "Acme Dev"}).json()["name"] == "Acme Dev" and dev.get("/v1/org").json()["name"] == "Acme Dev"
    team = owner.get("/v1/team").json()
    assert [m["role"] for m in team["members"]] == ["owner", "member"]
    dev_id = [m["id"] for m in team["members"] if m["role"] == "member"][0]
    assert owner.delete(f"/v1/team/members/{owner.get('/web/me').json()['id']}").status_code in (400, 404)
    assert owner.delete(f"/v1/team/members/{dev_id}").status_code == 200
    assert dev.get("/web/me").status_code == 401                                                           # their session is gone
    assert owner.get("/v1/connections", headers={"Authorization": "Bearer " + mine["key"]}).status_code == 401     # keys the member made stop working


def test_free_plan_counts_every_check_and_stops_at_the_limit(env, monkeypatch):
    from watchfox import billing
    monkeypatch.setattr(config, "ENFORCE_PLANS", True)
    monkeypatch.setitem(billing.PLANS["kit"], "runs", 3)
    monkeypatch.setitem(billing.PLANS["kit"], "watches", 1)
    k = fresh_client()
    signup(k, {"email": "freebie@example.com", "password": "free long password"})
    assert k.get("/v1/plan").json()["plan"] == "kit" and k.get("/v1/plan").json()["runs_limit"] == 3
    cid = k.post("/v1/connections", json={"provider": "sandbox"}).json()["id"]
    assert k.post("/v1/watches", json={"connection_id": cid, "what": "invoice"}).status_code == 200
    assert "plan allows 1 watches" in k.post("/v1/watches", json={"connection_id": cid, "what": "claim"}).json()["detail"]
    for subj in ("Invoice one", "Hello", "Invoice two"):                # caught and not caught both count
        k.post(f"/v1/connections/{cid}/sandbox-emails", json={"sender": "a@b.com", "subject": subj, "body": "invoice please pay"})
    assert wait_for(lambda: k.get("/v1/usage").json()["runs_used"] == 3)
    k.post(f"/v1/connections/{cid}/sandbox-emails", json={"sender": "a@b.com", "subject": "Over the limit", "body": "x"})
    time.sleep(1.5)
    u = k.get("/v1/usage").json()
    assert u["runs_used"] == 3 and u["runs_remaining"] == 0
    assert [i["email"]["subject"] for i in k.get("/v1/log").json()["items"] if i["email"]["subject"] == "Over the limit"] == []   # not checked
    r = k.post("/v1/classify", json={"body": "hello", "what": "invoice"})
    assert r.status_code == 429 and "Upgrade" in r.json()["detail"]
    # upgrade request is recorded, and only a paid plan can be asked for
    assert k.post("/v1/plan/upgrade", json={"plan": "kit"}).status_code == 400
    assert k.post("/v1/plan/upgrade", json={"plan": "tracker", "billing": "annual"}).status_code == 200
    # an operator switches the plan: runs and limits grow at once
    store.set_plan(k.get("/web/me").json()["id"], "tracker", "annual")
    assert k.get("/v1/plan").json()["runs_limit"] == 5000 and k.post("/v1/classify", json={"body": "hello", "what": "invoice"}).status_code == 200


def test_plan_catalogue(env):
    c, _ = env
    p = {x["id"]: x for x in fresh_client().get("/v1/plans").json()["plans"]}
    assert list(p) == ["kit", "tracker", "alpha"] and p["kit"]["runs"] == 50 and p["kit"]["monthly"] == 0
    assert p["tracker"]["annual"] < p["tracker"]["monthly"] * 12 and p["alpha"]["annual_saving_percent"] > 0


def test_sign_in_with_a_provider(env, monkeypatch):
    from watchfox import oauth
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "gid"); monkeypatch.setattr(config, "GITHUB_CLIENT_SECRET", "gsec")
    k = fresh_client()
    assert any(p["id"] == "github" for p in k.get("/web/providers").json()["oauth"])
    r = k.get("/auth/login/github/start")
    assert r.status_code in (302, 307) and r.headers["location"].startswith("https://github.com/login/oauth/authorize?") and "code_challenge=" in r.headers["location"]
    state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    monkeypatch.setattr(oauth, "fetch_profile", lambda p, code, v: {"subject": "42", "email": "octo@example.com", "verified": True, "name": "Octo"})
    assert k.get("/auth/login/github/callback", params={"code": "c", "state": "WRONG"}).headers["location"].startswith("/#/login?error=")
    r = k.get("/auth/login/github/start"); state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    ok = k.get("/auth/login/github/callback", params={"code": "c", "state": state})
    assert ok.headers["location"] == "/#/dashboard"
    me = k.get("/web/me").json()
    assert me["email"] == "octo@example.com" and me["verified"] is True and me["role"] == "owner"
    again = fresh_client(); r = again.get("/auth/login/github/start"); state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    again.get("/auth/login/github/callback", params={"code": "c", "state": state})
    assert again.get("/web/me").json()["id"] == me["id"]                      # same person, same account
    # a provider that cannot vouch for the address must not take over an existing account with that email
    monkeypatch.setattr(config, "MS_CLIENT_ID", "mid"); monkeypatch.setattr(config, "MS_CLIENT_SECRET", "msec")
    monkeypatch.setattr(oauth, "fetch_profile", lambda p, code, v: {"subject": "m1", "email": "octo@example.com", "verified": False, "name": ""})
    ms = fresh_client(); r = ms.get("/auth/login/microsoft/start"); state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    assert "error=" in ms.get("/auth/login/microsoft/callback", params={"code": "c", "state": state}).headers["location"]
    assert ms.get("/web/me").status_code == 401
    # an unconfigured provider is refused politely
    assert "error=" in fresh_client().get("/auth/login/google/start").headers["location"] or config.GOOGLE_CLIENT_ID


def test_every_plan_limit_is_enforced(env, monkeypatch):
    from watchfox import billing
    monkeypatch.setattr(config, "ENFORCE_PLANS", True)
    for k, v in (("mailboxes", 1), ("types", 1), ("members", 2)):
        monkeypatch.setitem(billing.PLANS["kit"], k, v)
    k = fresh_client()
    signup(k, {"email": "limits@example.com", "password": "limits long password"})
    assert k.post("/v1/connections", json={"provider": "sandbox"}).status_code == 200
    r = k.post("/v1/connections", json={"provider": "sandbox"})
    assert r.status_code == 400 and "plan allows 1 mailboxes" in r.json()["detail"]
    assert k.post("/v1/connect-links", json={}).status_code in (200, 400)
    body = {"label": "One", "description": "A long enough description", "question": "Is it one?", "threshold": 0.4}
    assert k.post("/v1/types", json=body).status_code == 201
    r = k.post("/v1/types", json=body)
    assert r.status_code == 400 and "plan allows 1 custom types" in r.json()["detail"]
    assert k.post("/v1/team/invites", json={"email": "limit-a@example.com"}).status_code == 201       # 2nd seat
    r = k.post("/v1/team/invites", json={"email": "limit-b@example.com"})
    assert r.status_code == 400 and "plan allows 2 team members" in r.json()["detail"]
    # upgrading lifts the limits at once
    store.set_plan(k.get("/web/me").json()["id"], "tracker")
    assert k.post("/v1/connections", json={"provider": "sandbox"}).status_code == 200
    assert k.post("/v1/types", json=dict(body, label="Two")).status_code == 201
    assert k.post("/v1/team/invites", json={"email": "limit-b@example.com"}).status_code == 201


FORBIDDEN = re.compile(r"gpt|openai|laya|convai|vercel|boundless|typed-decisions|modernbert|typesafe|ai-gateway|laya-serve", re.I)


def test_nothing_users_can_see_names_the_model_or_its_hosts(env):
    import pathlib
    c, h = env
    # the website bundle
    dist = pathlib.Path(config.WEB_DIST)
    for f in [*dist.rglob("*.js"), *dist.rglob("*.html"), *dist.rglob("*.css")]:
        assert not FORBIDDEN.search(f.read_text(errors="ignore")), f.name
    # API answers: health, plans, presets, classify, errors, log, export
    texts = [c.get("/v1/health").text, c.get("/v1/plans").text, c.get("/v1/presets").text, c.get("/v1/providers", headers=h).text,
             c.post("/v1/classify", headers=h, json={"subject": "Quote", "body": "please quote", "what": "enquiry"}).text,
             c.post("/v1/classify", headers=h, json={"body": "x", "what": "nope"}).text, c.get("/v1/log", headers=h).text,
             c.get("/v1/log/export", headers=h).text, c.get("/v1/openapi.json").text]
    for t in texts:
        assert not FORBIDDEN.search(t), FORBIDDEN.search(t).group(0)


def test_model_outage_messages_are_neutral(monkeypatch):
    from watchfox import engine as eng
    from watchfox.items import Item
    e = eng.Engine()
    monkeypatch.setattr(config, "LAYA_URL", "")
    monkeypatch.setattr(config, "LAYA_FALLBACK_KEY", "")      # never reach the real backup from a test
    with pytest.raises(RuntimeError) as err:
        e.score(Item(id="1", title="t", text="x", kind="email"), [dict(presets.all_presets()["invoice"])])
    assert not FORBIDDEN.search(str(err.value)) and "agent" in str(err.value)


def test_stray_nul_characters_in_email_text_are_harmless(env):
    c, h = env
    cid = c.post("/v1/connections", headers=h, json={"provider": "sandbox"}).json()["id"]
    r = c.post(f"/v1/connections/{cid}/sandbox-emails", headers=h, json={"sender": "a\x00@b.com", "subject": "Quote\x00 please", "body": "need\x00 a quote"})
    assert r.status_code == 200
    assert wait_for(lambda: any("Quote" in i["email"]["subject"] for i in c.get("/v1/log", headers=h).json()["items"]))


def test_www_redirects_to_the_main_address(env, monkeypatch):
    monkeypatch.setattr(config, "BASE_URL", "https://example.test")
    r = TestClient(app, base_url="https://www.example.test", follow_redirects=False).get("/v1/health?x=1")
    assert r.status_code == 301 and r.headers["location"] == "https://example.test/v1/health?x=1"
    assert TestClient(app, base_url="https://example.test", follow_redirects=False).get("/v1/health").status_code == 200


def test_provider_only_mode_has_no_passwords_and_invites_use_providers(env, monkeypatch):
    from watchfox import oauth
    monkeypatch.setattr(config, "PASSWORD_LOGIN", False)
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "gid"); monkeypatch.setattr(config, "GITHUB_CLIENT_SECRET", "gsec")
    monkeypatch.setattr(config, "MS_CLIENT_ID", "mid"); monkeypatch.setattr(config, "MS_CLIENT_SECRET", "msec")
    anon = fresh_client()
    opts = anon.get("/web/providers").json()
    assert opts["password"] is False and {o["id"] for o in opts["oauth"]} >= {"github", "microsoft"}
    for method, url, body in (("POST", "/web/signup", {"email": "a@b.co", "password": "long enough pw"}), ("POST", "/web/login", {"email": "a@b.co", "password": "x"}),
                              ("POST", "/web/forgot", {"email": "a@b.co"}), ("POST", "/web/reset", {"token": "x", "password": "long enough pw"}),
                              ("POST", "/web/join", {"token": "x", "password": "long enough pw"})):
        assert anon.request(method, url, json=body).status_code == 404, url

    def sign_in(client, provider, prof):
        monkeypatch.setattr(oauth, "fetch_profile", lambda p, code, v: prof)
        r = client.get(f"/auth/login/{provider}/start", params={"invite": client.__dict__.get("invite", "")} if client.__dict__.get("invite") else {})
        state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
        return client.get(f"/auth/login/{provider}/callback", params={"code": "c", "state": state})

    # a Microsoft account is real enough to use the API at once (no email to confirm), but never takes over an existing email
    owner = fresh_client()
    assert sign_in(owner, "microsoft", {"subject": "ms-owner", "email": "boss-ms@example.com", "verified": False, "name": ""}).headers["location"] == "/#/dashboard"
    me = owner.get("/web/me").json()
    assert me["verified"] is True and me["role"] == "owner"
    assert owner.post("/v1/keys", json={"name": "k"}).status_code == 200
    # invitation: the owner gets a link to share (no mail server), the invitee signs in with a provider and joins that organization
    inv = owner.post("/v1/team/invites", json={"email": "colleague@example.com"}).json()
    assert inv["link"].startswith(config.BASE_URL + "/#/join?token=") and inv["emailed"] is False
    token = token_of(inv["link"])
    assert fresh_client().get("/web/invite", params={"token": token}).json()["org"]
    guest = fresh_client(); guest.__dict__["invite"] = token
    assert sign_in(guest, "github", {"subject": "gh-guest", "email": "someone-else@example.com", "verified": True, "name": ""}).headers["location"] == "/#/dashboard"
    g = guest.get("/web/me").json()
    assert g["role"] == "member" and g["id"] == me["id"]                       # joined the owner's organization
    assert [m["role"] for m in owner.get("/v1/team").json()["members"]] == ["owner", "member"]
    # the link works once; someone who already has their own account cannot use it to join a second organization
    other = fresh_client(); other.__dict__["invite"] = token
    assert "error=" in sign_in(other, "github", {"subject": "gh-late", "email": "late@example.com", "verified": True, "name": ""}).headers["location"]
    solo = fresh_client()
    sign_in(solo, "github", {"subject": "gh-solo", "email": "solo@example.com", "verified": True, "name": ""})
    inv2 = owner.post("/v1/team/invites", json={"email": "another@example.com"}).json()
    solo.__dict__["invite"] = token_of(inv2["link"])
    assert "error=" in sign_in(solo, "github", {"subject": "gh-solo", "email": "solo@example.com", "verified": True, "name": ""}).headers["location"]


def test_invitation_links_can_be_fetched_again_and_email_goes_through_cloudflare(env, monkeypatch):
    from watchfox import mailer
    owner = fresh_client()
    signup(owner, {"email": "link-owner@example.com", "password": "link owner password"})
    inv = owner.post("/v1/team/invites", json={"email": "later@example.com"}).json()
    again = owner.get(f"/v1/team/invites/{inv['id']}/link").json()["link"]
    assert token_of(again) and fresh_client().get("/web/invite", params={"token": token_of(again)}).json()["email"] == "later@example.com"
    stranger = fresh_client()
    signup(stranger, {"email": "link-stranger@example.com", "password": "stranger password 123"})
    assert stranger.get(f"/v1/team/invites/{inv['id']}/link").status_code in (403, 404)       # only the owner of that organization
    # sending: Cloudflare's API with the right address, key and body
    seen = {}

    class R:
        status_code = 200

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, auth=headers["Authorization"], body=json)
        return R()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(config, "CF_ACCOUNT_ID", "acct123"); monkeypatch.setattr(config, "CF_EMAIL_TOKEN", "tok-secret")
    assert mailer.deliver("a@b.com", "Hi", "Body") is True
    assert seen["url"].endswith("/accounts/acct123/email/sending/send") and seen["auth"] == "Bearer tok-secret"
    assert seen["body"]["to"] == "a@b.com" and seen["body"]["from"] == config.EMAIL_FROM_ADDRESS and seen["body"]["text"] == "Body"
    assert mailer.deliver("a@b.com", "Hi", "Body", from_name="Sam via WatchFox", reply_to="owner@example.com") is True
    assert seen["body"]["from"] == {"address": config.EMAIL_FROM_ADDRESS, "name": "Sam via WatchFox"} and seen["body"]["reply_to"] == "owner@example.com"
    from watchfox import accounts
    assert accounts.sender_name("", "jane.doe@example.com") == "Jane Doe via WatchFox"
    uid = store.create_user("sender@example.com", "x", verified=True)
    store.set_display_name(uid, 'Sam <script>"\\ Smith'); assert accounts.sender_name(uid, "sender@example.com") == "Sam script Smith via WatchFox"
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    assert owner.post("/v1/team/invites", json={"email": "emailed@example.com"}).json()["emailed"] is True
    monkeypatch.setattr(config, "CF_ACCOUNT_ID", ""); monkeypatch.setattr(config, "CF_EMAIL_TOKEN", ""); monkeypatch.setattr(config, "DEV_LINKS", False)
    assert mailer.deliver("a@b.com", "Hi", "Body") is False                                      # nothing configured: not sent, no crash


# ------------------------------------------------------------------ billing with Helcim (the payment service is simulated)
class FakeHelcim:
    """Stands in for Helcim's API so the whole checkout can be tested without a card or a charge."""
    def __init__(self):
        self.subs, self.deleted, self.next_id, self.decline = {}, [], 100, False
        self.approved_code = None
        self.no_recent = False

    def install(self, monkeypatch):
        from watchfox import helcim
        monkeypatch.setattr(config, "HELCIM_API_TOKEN", "test-token")
        monkeypatch.setattr(helcim, "terminal_id", lambda: 7)
        monkeypatch.setattr(helcim, "plans", lambda: [{"id": 11 + i, "name": n, "status": "active"} for i, n in enumerate(
            ["WatchFox Tracker monthly", "WatchFox Tracker yearly", "WatchFox Alpha monthly", "WatchFox Alpha yearly"])])
        self.last_amount, self.inits = 0, []

        def init(code, existing, name, biz, amount=0):
            self.approved_code, self.last_amount = code, amount
            self.inits.append(amount)
            return {"checkoutToken": "tok" + "x" * 19, "secretToken": "sec123"}
        monkeypatch.setattr(helcim, "initialize", init)
        monkeypatch.setattr(helcim, "card_transaction", lambda tid: {"status": "APPROVED", "type": "purchase" if self.last_amount else "verify", "amount": self.last_amount,
                                                                       "customerCode": self.approved_code if tid != 999 else "SOMEONE-ELSE"})
        self.saved_card = False
        monkeypatch.setattr(helcim, "find_customer", lambda code: {"id": 5, "customerCode": code} if self.saved_card else None)
        monkeypatch.setattr(helcim, "default_card", lambda cid: {"last4": "5589", "expiry": "1228"} if self.saved_card else None)
        monkeypatch.setattr(helcim, "latest_transaction", lambda code, since, kind="verify": {"status": "APPROVED", "type": kind, "amount": self.last_amount, "customerCode": code}
                            if code == self.approved_code and not self.no_recent and ((kind == "purchase") == bool(self.last_amount)) else {})

        def create(plan_id, code, amount, activate):
            self.next_id += 1
            self.subs[self.next_id] = {"plan": plan_id, "amount": amount, "activate": activate}
            return {"id": self.next_id, "dateBilling": "2026-11-12", "hasFailedPayments": "true" if self.decline else "false",
                    "payments": [{"status": "declined" if self.decline else "waiting"}]}
        monkeypatch.setattr(helcim, "create_subscription", create)
        monkeypatch.setattr(helcim, "cancel_subscription", lambda sid: self.deleted.append(int(sid)))
        monkeypatch.setattr(helcim, "subscription", lambda sid: {"id": int(sid), "hasFailedPayments": "true" if self.decline else "false", "dateBilling": "2026-12-12"})


def test_helcim_checkout_subscribes_changes_plan_and_cancels(env, monkeypatch):
    from watchfox import payments
    fh = FakeHelcim(); fh.install(monkeypatch)
    k = fresh_client()
    signup(k, {"email": "payer@example.com", "password": "payer long password"})
    wid = k.get("/web/me").json()["id"]
    assert k.get("/v1/billing").json()["configured"] is True and k.get("/v1/billing").json()["status"] == "none"
    assert k.post("/v1/billing/checkout", json={"plan": "kit"}).status_code == 400
    assert k.post("/v1/billing/confirm", json={"checkoutToken": "nope", "eventMessage": {}}).status_code == 400
    co = k.post("/v1/billing/checkout", json={"plan": "tracker", "billing": "annual"}).json()
    assert co["checkoutToken"].startswith("tok") and co["amount"] == 290 and co["currency"] == "USD"
    ok = {"data": {"transactionId": 5, "status": "APPROVED"}, "hash": "whatever"}
    # a card result that belongs to somebody else's customer is refused
    fh.no_recent = True
    assert k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"], "eventMessage": {"data": {"transactionId": 999}, "hash": "x"}}).status_code == 402
    fh.no_recent = False
    r = k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"], "eventMessage": ok})
    assert r.status_code == 200 and r.json()["plan"] == "tracker" and r.json()["billing"] == "annual" and r.json()["status"] == "active"
    assert list(fh.subs.values())[0]["amount"] == 290 and list(fh.subs.values())[0]["plan"] == 12      # the yearly Tracker plan
    assert fh.inits == [290] and r.json()["charged"] == 290                                            # paid in the card form
    assert list(fh.subs.values())[0]["activate"] > time.strftime("%Y-%m-%d")                         # and the subscription starts at the first renewal, not today
    assert k.get("/v1/plan").json()["plan"] == "tracker" and k.get("/v1/plan").json()["runs_limit"] == 5000
    assert k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"], "eventMessage": ok}).status_code == 400      # a form is used once
    # moving up to Alpha monthly stops the old subscription and starts a new one
    co2 = k.post("/v1/billing/checkout", json={"plan": "alpha", "billing": "monthly"}).json()
    assert k.post("/v1/billing/confirm", json={"checkoutToken": co2["checkoutToken"], "eventMessage": json.dumps(ok)}).status_code == 200
    assert k.get("/v1/billing").json()["plan"] == "alpha" and fh.deleted == [101]
    assert k.post("/v1/billing/checkout", json={"plan": "alpha", "billing": "monthly"}).status_code == 400                        # already on it
    # cancelling keeps the plan until the end of the paid period
    r = k.post("/v1/billing/cancel").json()
    assert r["status"] == "cancelling" and r["plan"] == "alpha" and fh.deleted == [101, 102]
    store.set_billing(wid, cancel_at=time.time() - 5)
    payments.sync_all()
    assert k.get("/v1/billing").json()["plan"] == "kit" and k.get("/v1/billing").json()["status"] == "none"


def test_payments_list_and_pdf_receipt_only_for_own_payments(env, monkeypatch):
    from watchfox import helcim, payments
    fh = FakeHelcim(); fh.install(monkeypatch)
    k = fresh_client()
    signup(k, {"email": "rcpt@example.com", "password": "payer long password"})
    code = payments.customer_code(k.get("/web/me").json()["id"])
    mine = {"transactionId": 41, "status": "APPROVED", "type": "purchase", "amount": 29, "currency": "USD", "customerCode": code,
            "dateCreated": "2026-10-01 10:00:00", "cardNumber": "5453XXXXXXXX5589", "cardType": "MC", "approvalCode": "A1"}
    monkeypatch.setattr(helcim, "customer_transactions", lambda c: [mine] if c == code else [])
    monkeypatch.setattr(helcim, "card_transaction", lambda tid: mine if tid == 41 else {**mine, "transactionId": tid, "customerCode": "SOMEONE-ELSE"})
    rows = k.get("/v1/billing/payments").json()["payments"]
    assert rows[0]["amount"] == 29 and rows[0]["card"] == "5589" and rows[0]["kind"] == "payment"
    r = k.get("/v1/billing/payments/41/receipt")
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and "attachment" in r.headers["content-disposition"]
    assert k.get("/v1/billing/payments/42/receipt").status_code == 404          # another customer's payment
    assert fresh_client().get("/v1/billing/payments").status_code == 401


def test_helcim_declined_first_payment_changes_nothing_and_failed_renewals_get_a_grace_period(env, monkeypatch):
    from watchfox import payments
    fh = FakeHelcim(); fh.install(monkeypatch)
    k = fresh_client()
    signup(k, {"email": "decline@example.com", "password": "decline long password"})
    wid = k.get("/web/me").json()["id"]
    fh.decline = True
    co = k.post("/v1/billing/checkout", json={"plan": "tracker", "billing": "monthly"}).json()
    r = k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"], "eventMessage": {"data": {"transactionId": 5}, "hash": "x"}})
    assert r.status_code == 402 and "declined" in r.json()["detail"] and k.get("/v1/plan").json()["plan"] == "kit" and fh.deleted
    # an active subscription whose renewal fails: past due, then downgraded after the grace period
    fh.decline = False
    co = k.post("/v1/billing/checkout", json={"plan": "tracker", "billing": "monthly"}).json()
    assert k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"], "eventMessage": {"data": {"transactionId": 5}, "hash": "x"}}).status_code == 200
    fh.decline = True
    payments.sync_all()
    assert k.get("/v1/billing").json()["past_due"] is True and k.get("/v1/plan").json()["plan"] == "tracker"       # still on the plan during the grace period
    store.set_billing(wid, past_due_since=time.time() - 8 * 86400)
    payments.sync_all()
    assert k.get("/v1/plan").json()["plan"] == "kit"
    # only the owner can pay, and nothing works until Helcim is configured
    monkeypatch.setattr(config, "HELCIM_API_TOKEN", "")
    assert k.post("/v1/billing/checkout", json={"plan": "tracker", "billing": "monthly"}).status_code == 503


def test_helcim_answers_in_both_shapes_are_understood(monkeypatch):
    from watchfox import helcim
    monkeypatch.setattr(config, "HELCIM_API_TOKEN", "t")
    wrapped = {"timestamp": "2026-10-11 00:00:00", "status": "ok", "data": [{"id": 1, "name": "A", "status": "active"}]}
    for answer in (wrapped, [{"id": 1, "name": "A", "status": "active"}]):
        monkeypatch.setattr(helcim, "_call", lambda method, path, body=None, idempotency=False, a=answer: a)
        assert helcim.plans() == [{"id": 1, "name": "A", "status": "active"}]
    monkeypatch.setattr(helcim, "_call", lambda *a, **k: {"timestamp": "x", "status": "ok", "data": {"status": "APPROVED", "type": "verify", "customerCode": "C1"}})
    assert helcim.card_transaction(5)["customerCode"] == "C1"
    monkeypatch.setattr(helcim, "_call", lambda *a, **k: None)
    assert helcim.plans() == [] and helcim.subscription(5) is None


def test_helcim_plain_text_success_message_is_confirmed_with_helcim_itself(env, monkeypatch):
    fh = FakeHelcim(); fh.install(monkeypatch)
    k = fresh_client()
    signup(k, {"email": "plain@example.com", "password": "plain long password"})
    co = k.post("/v1/billing/checkout", json={"plan": "tracker", "billing": "monthly"}).json()
    r = k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"], "eventMessage": "Payment information saved"})
    assert r.status_code == 200 and r.json()["plan"] == "tracker"
    co = k.post("/v1/billing/checkout", json={"plan": "alpha", "billing": "monthly"}).json()
    fh.no_recent = True                       # Helcim has no approved card check for this customer: nothing may change
    assert k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"], "eventMessage": "Payment information saved"}).status_code == 402
    assert k.get("/v1/billing").json()["plan"] == "tracker"


def test_helcim_initialize_falls_back_to_the_existing_customer(monkeypatch):
    from watchfox import helcim
    monkeypatch.setattr(config, "HELCIM_API_TOKEN", "t")
    monkeypatch.setattr(helcim, "terminal_id", lambda: 7)
    seen = []

    def call(method, path, body=None, idempotency=False):
        seen.append(body)
        if "customerRequest" in body:
            raise helcim.HelcimError("customerCode already exists")
        return {"checkoutToken": "c" * 22, "secretToken": "s" * 22}

    monkeypatch.setattr(helcim, "_call", call)
    t = helcim.initialize("WF1", False, "A", "B")
    assert t["checkoutToken"] == "c" * 22 and "customerRequest" in seen[0] and seen[1]["customerCode"] == "WF1" and "customerRequest" not in seen[1]


def test_a_saved_card_is_used_without_asking_for_it_again(env, monkeypatch):
    fh = FakeHelcim(); fh.install(monkeypatch)
    k = fresh_client()
    signup(k, {"email": "saved@example.com", "password": "saved long password"})
    fh.saved_card = True
    co = k.post("/v1/billing/checkout", json={"plan": "tracker", "billing": "monthly"}).json()
    assert co["mode"] == "saved_card" and co["card"] == "card ending 5589" and co["amount"] == 29
    r = k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"]})
    assert r.status_code == 200 and r.json()["plan"] == "tracker" and len(fh.subs) == 1
    assert k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"]}).status_code == 400          # used once
    assert k.post("/v1/billing/confirm", json={"checkoutToken": "someoneelsestoken22chr"}).status_code == 400
    # replacing the card still opens the secure form
    assert k.post("/v1/billing/checkout", json={"kind": "card"}).json().get("mode") != "saved_card"


def test_paid_in_the_form_but_subscription_not_created_is_retried_and_wrong_amounts_are_refused(env, monkeypatch):
    from watchfox import helcim, payments
    fh = FakeHelcim(); fh.install(monkeypatch)
    k = fresh_client()
    signup(k, {"email": "retry@example.com", "password": "retry long password"})
    wid = k.get("/web/me").json()["id"]
    # the card form charged a different amount than the plan costs: the plan does not change
    co = k.post("/v1/billing/checkout", json={"plan": "tracker", "billing": "monthly"}).json()
    fh.last_amount = 5
    assert k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"], "eventMessage": "saved"}).status_code == 402
    assert k.get("/v1/plan").json()["plan"] == "kit"
    # the customer paid, but Helcim cannot create the subscription right now: they keep the plan, and it is created later
    co = k.post("/v1/billing/checkout", json={"plan": "tracker", "billing": "monthly"}).json()
    real_create = helcim.create_subscription
    monkeypatch.setattr(helcim, "create_subscription", lambda *a: (_ for _ in ()).throw(helcim.HelcimError("down")))
    r = k.post("/v1/billing/confirm", json={"checkoutToken": co["checkoutToken"], "eventMessage": "saved"})
    assert r.status_code == 200 and r.json()["plan"] == "tracker" and r.json()["status"] == "pending_subscription"
    monkeypatch.setattr(helcim, "create_subscription", real_create)
    payments.sync_all()
    b = k.get("/v1/billing").json()
    assert b["status"] == "active" and b["subscribed"] is True and len(fh.subs) == 1


def test_identical_watch_is_refused_but_a_different_webhook_or_type_is_fine(env):
    c, H = env
    cn = c.post("/v1/connections", headers=H, json={"provider": "sandbox"}).json()
    body = {"connection_id": cn["id"], "what": ["enquiry", "invoice"]}
    assert c.post("/v1/watches", headers=H, json=body).status_code == 200
    assert c.post("/v1/watches", headers=H, json={**body, "what": ["invoice", "enquiry"]}).status_code == 409
    assert c.post("/v1/watches", headers=H, json={**body, "what": ["enquiry"]}).status_code == 200


def test_plan_limits_are_the_same_through_an_api_key(monkeypatch):
    """Every limit the dashboard shows must also stop a call made with an API key."""
    from watchfox import billing
    monkeypatch.setattr(config, "ENFORCE_PLANS", True)
    k = fresh_client()
    signup(k, {"email": "api-limits@example.com", "password": "limits long password"})
    wid = k.get("/web/me").json()["id"]
    H = {"Authorization": "Bearer " + k.post("/v1/keys", json={"name": "t"}).json()["key"]}
    kit = billing.PLANS["kit"]
    ids = [k.post("/v1/connections", headers=H, json={"provider": "sandbox"}) for _ in range(kit["mailboxes"] + 1)]
    assert [r.status_code for r in ids] == [200] * kit["mailboxes"] + [400]                     # mailboxes
    cn = ids[0].json()["id"]
    kinds = ["enquiry", "invoice", "support", "claim"]
    made = [k.post("/v1/watches", headers=H, json={"connection_id": cn, "what": w}) for w in kinds[: kit["watches"] + 1]]
    assert [r.status_code for r in made] == [200] * kit["watches"] + [400]                      # watches
    body = {"label": "Ship", "description": "Suppliers saying an order is late.", "question": "Is this about a late order?", "threshold": 0.5}
    types = [k.post("/v1/types", headers=H, json={**body, "label": f"T{i}"}).status_code for i in range(kit["types"] + 1)]
    assert types[-1] == 400 and 400 not in types[:-1]                                           # custom types
    keys = [k.post("/v1/keys", json={"name": f"k{i}"}).status_code for i in range(12)]
    assert 400 in keys                                                                           # API keys (10 at most)
    seats = [k.post("/v1/team/invites", json={"email": f"p{i}@example.org"}).status_code for i in range(billing.PLANS["kit"]["members"] + 1)]
    assert seats == [201, 201, 400, 400]          # the owner takes one of the three seats                                          # team seats
    store.add_runs(wid, billing.month(), kit["runs"])                                            # every run used
    assert k.post("/v1/classify", headers=H, json={"body": "hello"}).status_code == 429          # runs: test calls
    assert k.post(f"/v1/connections/{cn}/sandbox-emails", headers=H, json={"subject": "s", "body": "b"}).status_code == 429   # runs: sandbox mail


def test_agent_docs_are_served_and_cover_every_public_endpoint():
    from watchfox import agent_docs
    k = fresh_client()
    idx = k.get("/llms.txt")
    assert idx.status_code == 200 and "llms-full.txt" in idx.text and "openapi.json" in idx.text
    full = k.get("/llms-full.txt").text
    for needle in ("Authentication", "X-WatchFox-Signature", "sandbox-emails", "Rate limits", "Endpoint reference", "POST /v1/watches"):
        assert needle in full
    assert "{{BASE}}" not in full
    spec = k.get("/v1/openapi.json").json()
    assert k.get("/openapi.json").json()["info"]["title"] == "WatchFox API"
    paths = spec["paths"]
    assert not any(p.startswith(agent_docs.HIDDEN) for p in paths)
    ids = [op["operationId"] for ops in paths.values() for op in ops.values()]
    assert len(ids) == len(set(ids))
    # a new public endpoint must be described in agent_docs.SUMMARY, or the docs would silently miss it
    missing = [f"{m.upper()} {p}" for p, ops in paths.items() for m in ops if f"{m.upper()} {p}" not in agent_docs.SUMMARY]
    assert not missing, missing


def test_connect_page_branding_is_checked_and_reaches_the_page(monkeypatch):
    monkeypatch.setattr(config, "ENFORCE_PLANS", True)
    k = fresh_client()
    signup(k, {"email": "brand@example.com", "password": "brand long password"})
    base = {"provider": "gmail", "app_name": "ExampleCorp"}
    ok = k.post("/v1/connect-links", json={**base, "logo_url": "https://example.com/logo.svg", "accent_color": "#1A73E8", "support_email": "help@example.com"})
    assert ok.status_code == 200
    s = k.get("/v1/connect/session", params={"t": ok.json()["url"].split("t=")[1]}).json()
    assert s["brand"] == {"logo": "https://example.com/logo.svg", "color": "#1A73E8", "support": "help@example.com"} and s["app_name"] == "ExampleCorp"
    plain = k.post("/v1/connect-links", json=base).json()["url"].split("t=")[1]
    assert k.get("/v1/connect/session", params={"t": plain}).json()["brand"] == {}
    for bad in ({"logo_url": "http://example.com/a.png"}, {"logo_url": "https://x.com/a b.png"}, {"logo_url": 'https://x.com/"onerror=1'},
                {"accent_color": "red"}, {"accent_color": "#12345"}, {"support_email": "nope"}):
        assert k.post("/v1/connect-links", json={**base, **bad}).status_code == 400, bad
    assert k.post("/v1/connect-links", json={**base, "hide_powered_by": True}).status_code == 400           # Kit plan
    store.set_plan(k.get("/web/me").json()["id"], "tracker", "monthly")
    paid = k.post("/v1/connect-links", json={**base, "hide_powered_by": True})
    assert paid.status_code == 200 and k.get("/v1/connect/session", params={"t": paid.json()["url"].split("t=")[1]}).json()["brand"] == {"hide": True}


# ------------------------------------------------------------------ abuse: farming free accounts, bots, reaching inside our network
class _Req:
    def __init__(self, fwd="", peer="10.0.0.1"):
        self.headers = {"x-forwarded-for": fwd} if fwd else {}
        self.client = type("C", (), {"host": peer})()


def test_client_ip_cannot_be_spoofed_through_x_forwarded_for(monkeypatch):
    from watchfox import abuse
    monkeypatch.setenv("WATCHFOX_TRUST_PROXY", "1")
    # a caller who writes their own header gets the address the proxy added (the last one), not the one they invented
    assert abuse.client_ip(_Req("1.2.3.4, 198.51.100.7:51234")) == "198.51.100.7"
    assert abuse.client_ip(_Req("198.51.100.7")) == "198.51.100.7"
    assert abuse.client_ip(_Req("")) == "10.0.0.1"
    monkeypatch.setenv("WATCHFOX_PROXY_HOPS", "2")
    assert abuse.client_ip(_Req("9.9.9.9, 198.51.100.7, 172.16.0.5")) == "198.51.100.7"
    monkeypatch.delenv("WATCHFOX_TRUST_PROXY")
    assert abuse.client_ip(_Req("1.2.3.4")) == "10.0.0.1"                       # not behind a proxy: the header means nothing


def test_new_accounts_are_refused_for_disposable_alias_young_github_and_floods(monkeypatch):
    from watchfox import abuse
    monkeypatch.setattr(config, "NEW_ACCOUNTS_PER_IP_DAY", 2)
    monkeypatch.setattr(config, "NEW_ACCOUNTS_PER_HOUR", 1000)
    with pytest.raises(abuse.Refused):
        abuse.check_new_account("203.0.113.1", "bot@mailinator.com")
    with pytest.raises(abuse.Refused):
        abuse.check_new_account("203.0.113.1", "fresh@gmail.com", github_age_days=0.5)
    abuse.check_new_account("203.0.113.1", "real.person@gmail.com", github_age_days=400)
    store.create_user("realperson@gmail.com", "x", verified=True)
    for alias in ("real.person@gmail.com", "r.e.a.l.person+loop1@googlemail.com", "REALPERSON+x@gmail.com"):     # the same inbox however it is written
        with pytest.raises(abuse.Refused):
            abuse.check_new_account("203.0.113.9", alias)
    abuse.check_new_account("203.0.113.1", "second@example.org")
    with pytest.raises(abuse.Refused):                                           # a third new account from the same address in a day
        abuse.check_new_account("203.0.113.1", "third@example.org")
    monkeypatch.setattr(config, "NEW_ACCOUNTS_PER_HOUR", 1)
    with pytest.raises(abuse.Refused):                                           # and a brake on the whole service
        abuse.check_new_account("203.0.113.50", "another@example.org")


def test_sign_in_callback_applies_the_account_checks(monkeypatch):
    from watchfox import oauth
    monkeypatch.setattr(config, "NEW_ACCOUNTS_PER_IP_DAY", 1)
    monkeypatch.setattr(config, "NEW_ACCOUNTS_PER_HOUR", 1000)
    store.x("DELETE FROM signups")
    n = {"i": 0}

    def profile(provider, code, verifier):
        n["i"] += 1
        return {"subject": f"farm-{n['i']}", "email": f"farm{n['i']}@example.net", "verified": True, "name": "Farmer"}
    monkeypatch.setattr(oauth, "fetch_profile", profile)

    def sign_in():
        k = fresh_client()
        k.cookies.set("wf_oauth", vault.sign({"s": "st", "v": "v", "p": "github", "inv": ""}, 600))
        r = k.get("/auth/login/github/callback", params={"code": "c", "state": "st"}, follow_redirects=False)
        return k, r
    k1, r1 = sign_in()
    assert k1.get("/web/me").status_code == 200                                  # the first account is made
    k2, r2 = sign_in()
    assert k2.get("/web/me").status_code == 401                                  # the second from the same address is not
    assert "Too%20many%20accounts" in r2.headers["location"]


def test_a_real_mailbox_has_one_free_allowance_however_many_accounts_use_it(monkeypatch):
    from watchfox import abuse, billing
    monkeypatch.setattr(config, "ENFORCE_PLANS", True)
    a, b, paid = (store.create_user(f"{n}@farm.test", "x", verified=True) for n in ("a", "b", "p"))
    store.set_plan(paid, "tracker", "monthly")
    ca, cb, cp = (store.connection(store.upsert_connection(u, "imap", "Shared.Inbox@Example.com", {"host": "h", "user": "u", "password": "p"})) for u in (a, b, paid))
    for _ in range(billing.PLANS["kit"]["runs"]):
        assert abuse.mailbox_allows(a, ca)
        abuse.count_mailbox(a, ca)
    assert not abuse.mailbox_allows(a, ca)
    assert not abuse.mailbox_allows(b, cb)                                       # a second free account on the same inbox gets nothing more
    assert abuse.mailbox_allows(paid, cp)                                        # a paying account is not held to the free allowance


def test_mail_servers_cannot_point_inside_our_network_or_use_odd_ports(monkeypatch):
    from watchfox import abuse
    k = fresh_client()
    signup(k, {"email": "ssrf@example.com", "password": "ssrf long password"})
    H = {"Authorization": "Bearer " + k.post("/v1/keys", json={"name": "t"}).json()["key"]}
    for host in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "100.101.102.103", "192.168.1.1", "0.0.0.0", "[::1]", "localhost"):
        r = k.post("/v1/connections", headers=H, json={"provider": "imap", "host": host, "user": "a@b.c", "password": "x"})
        assert r.status_code == 400 and "public" in r.json()["detail"], host
    r = k.post("/v1/connections", headers=H, json={"provider": "imap", "host": "8.8.8.8", "port": 22, "user": "a@b.c", "password": "x"})
    assert r.status_code == 400 and "port 993" in r.json()["detail"]
    for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.9", "::1", "fd00::1"):
        assert not abuse.public_host(ip)
    assert abuse.public_host("8.8.8.8")


def test_free_accounts_share_a_daily_cap_per_network_address(monkeypatch):
    monkeypatch.setattr(config, "ENFORCE_PLANS", True)
    monkeypatch.setattr(config, "FREE_PER_IP_DAY", 3)
    from watchfox import limiter
    limiter._hits.clear()
    codes = []
    for i in range(3):                                                           # three free accounts, one address
        k = fresh_client()
        signup(k, {"email": f"loop{i}@example.com", "password": "loop long password"})
        for _ in range(2):
            codes.append(k.post("/v1/classify", json={"body": "hello there"}).status_code)
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]


def test_security_headers_and_a_request_cannot_hide_its_size(monkeypatch):
    k = fresh_client()
    r = k.get("/v1/health")
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "secure.helcim.app" in csp
    assert "permissions-policy" in r.headers
    assert "frame-ancestors" not in k.get("/connect").headers["content-security-policy"]       # the connect page may be opened inside another site
    monkeypatch.setattr("watchfox.server.MAX_BODY", 1000)
    app_mw = [m for m in app.user_middleware if m.cls.__name__ == "BodyLimit"][0]
    assert app_mw.kwargs["limit"] == 40 * 1024 * 1024
    # a body that is bigger than allowed, sent in pieces with no length up front, is cut off with 413
    from watchfox.server import BodyLimit
    import asyncio

    async def run():
        sent, chunks = [], [{"type": "http.request", "body": b"x" * 600, "more_body": True}, {"type": "http.request", "body": b"x" * 600, "more_body": False}]

        async def receive(): return chunks.pop(0)
        async def send(m): sent.append(m)
        async def inner(scope, rcv, snd):
            while True:
                m = await rcv()
                if not m.get("more_body"): break
            await snd({"type": "http.response.start", "status": 200, "headers": []})
        await BodyLimit(inner, 1000)({"type": "http"}, receive, send)
        return sent
    sent = asyncio.run(run())
    assert sent[0]["status"] == 413


def test_broken_text_never_causes_a_server_error(env):
    c, H = env
    for body in ({"name": "\ud800"}, {"email": "a\ud800@b.co"}, {"connection_id": "\ud800", "what": "enquiry"}, {"provider": "sandbox", "metadata": {"k": "\ud800"}}):
        for path, m in (("/v1/org", "PATCH"), ("/v1/watches", "POST"), ("/v1/connections", "POST"), ("/v1/team/invites", "POST"), ("/v1/types", "POST")):
            r = c.request(m, path, headers={**H, "Content-Type": "application/json"}, content=json.dumps(body))
            assert r.status_code < 500, (path, body, r.status_code)
    r = c.post("/v1/watches", headers={**H, "Content-Type": "application/json"}, content=json.dumps({"x": 1}))
    assert r.status_code == 422 and isinstance(r.json()["detail"], list) and "msg" in r.json()["detail"][0] and "input" not in r.json()["detail"][0]


def test_public_health_says_nothing_about_how_checking_is_served(env):
    c, H = env
    anon = c.get("/v1/health", headers={"Authorization": ""}).json()
    assert set(anon) == {"ok", "ready"}
    full = c.get("/v1/health", headers=H).json()
    assert {"version", "agent", "queued"} <= set(full)
    text = json.dumps([c.get(p).text for p in ("/v1/health", "/v1/presets", "/llms.txt", "/llms-full.txt", "/v1/openapi.json", "/v1/providers")]).lower()
    for word in ("laya", "typed-decisions", "tailscale", ".ts.net", "vercel", "typesafe", "qwen", "gpt-", "foundry", "openai", "cognitiveservices"):
        assert word not in text, word


def test_faq_is_in_the_agent_guide_and_every_answer_is_well_formed():
    import json as _json
    from pathlib import Path
    items = _json.loads((Path(__file__).resolve().parents[1] / "web" / "public" / "faq.json").read_text())
    ids = [f["id"] for f in items]
    assert len(ids) == len(set(ids)) and len(items) >= 30
    for f in items:
        assert f["q"].strip() and len(f["a"]) > 30 and set(f["where"]) <= {"home", "pricing", "docs"} and f["where"], f["id"]
    full = fresh_client().get("/llms-full.txt").text
    assert "Frequently asked questions" in full and "I sent one email, so why did my count go up by 2?" in full
    assert "](" not in full.split("Frequently asked questions")[1].split("Endpoint reference")[0]       # links are written out as plain text


# ------------------------------------------------------------------ the admin console
def _admin_client(monkeypatch, provider=True):
    email = f"boss-{os.urandom(3).hex()}@example.com"                         # the suite shares one database, so each test needs its own admin
    monkeypatch.setattr(config, "ADMIN_EMAILS", [email])
    monkeypatch.setattr(config, "ADMIN_PROVIDER", "github")
    k = fresh_client()
    signup(k, {"email": email, "password": "admin long password"})
    uid = k.get("/web/me").json()["id"]
    if provider:
        store.add_identity("github", f"gh-{email}", uid)
    return k, uid


def test_only_the_named_admin_signed_in_through_github_can_open_the_console(monkeypatch):
    k, uid = _admin_client(monkeypatch)
    assert k.get("/v1/admin/overview").status_code == 200 and k.get("/web/me").json()["is_admin"] is True
    other = fresh_client()
    assert other.get("/v1/admin/overview").status_code == 404                                    # not signed in: it does not even look like it exists
    signup(other, {"email": "someone@example.com", "password": "other long password"})
    assert other.get("/v1/admin/overview").status_code == 404 and other.get("/web/me").json()["is_admin"] is False
    key = other.post("/v1/keys", json={"name": "k"}).json()["key"]
    assert fresh_client().get("/v1/admin/overview", headers={"Authorization": f"Bearer {key}"}).status_code == 404     # a key is never an admin
    mine = list(config.ADMIN_EMAILS)
    monkeypatch.setattr(config, "ADMIN_EMAILS", [])
    assert k.get("/v1/admin/overview").status_code == 404                                         # no admins configured: switched off
    monkeypatch.setattr(config, "ADMIN_EMAILS", mine)
    monkeypatch.setattr(config, "ADMIN_PROVIDER", "google")
    assert k.get("/v1/admin/overview").status_code == 404                                         # signed in with the wrong provider
    monkeypatch.setattr(config, "ADMIN_PROVIDER", "github")
    k2 = fresh_client()                                                                           # on the list, but never signed in with GitHub
    signup(k2, {"email": mine[0].replace("boss-", "boss2-"), "password": "admin long password"})
    monkeypatch.setattr(config, "ADMIN_EMAILS", mine + [mine[0].replace("boss-", "boss2-")])
    assert k2.get("/v1/admin/overview").status_code == 404


def test_admin_changes_need_the_header_and_are_audited(monkeypatch):
    k, uid = _admin_client(monkeypatch)
    victim = fresh_client()
    signup(victim, {"email": "victim@example.com", "password": "victim long password"})
    wid = victim.get("/web/me").json()["id"]
    assert k.post(f"/v1/admin/orgs/{wid}/plan", json={"plan": "tracker"}).status_code == 403            # a page on another site cannot send the header
    H = {"X-WatchFox-Admin": "1"}
    assert k.post(f"/v1/admin/orgs/{wid}/plan", json={"plan": "tracker", "billing": "monthly"}, headers=H).status_code == 200
    assert victim.get("/v1/plan").json()["plan"] == "tracker"
    assert k.post(f"/v1/admin/orgs/{wid}/plan", json={"plan": "gold"}, headers=H).status_code == 400
    listed = k.get("/v1/admin/orgs", params={"q": "victim"}).json()["orgs"]
    assert len(listed) == 1 and listed[0]["plan"] == "tracker" and listed[0]["owner"] == "victim@example.com"
    d = k.get(f"/v1/admin/orgs/{wid}").json()
    blob = json.dumps(d)
    assert d["members"][0]["email"] == "victim@example.com" and "pw_hash" not in blob and "secret" not in blob.lower().replace("webhook_secret", "")
    assert [a["action"] for a in k.get("/v1/admin/audit").json()["audit"]][0] == "set plan"


def test_suspending_an_organization_stops_its_keys_and_sign_in_but_not_the_explanation(monkeypatch):
    k, uid = _admin_client(monkeypatch)
    v = fresh_client()
    signup(v, {"email": "pause@example.com", "password": "pause long password"})
    wid = v.get("/web/me").json()["id"]
    key = {"Authorization": "Bearer " + v.post("/v1/keys", json={"name": "k"}).json()["key"]}
    H = {"X-WatchFox-Admin": "1"}
    assert v.get("/v1/connections", headers=key).status_code == 200
    k.post(f"/v1/admin/orgs/{wid}/suspend", json={"suspended": True, "reason": "abuse"}, headers=H)
    assert v.get("/v1/connections", headers=key).status_code == 403 and v.get("/v1/watches").status_code == 403
    me = v.get("/web/me").json()
    assert me["suspended"] is True                                                                       # the person can still see why
    k.post(f"/v1/admin/orgs/{wid}/suspend", json={"suspended": False}, headers=H)
    assert v.get("/v1/connections", headers=key).status_code == 200


def test_deleting_an_organization_removes_everything_it_owned(monkeypatch):
    k, uid = _admin_client(monkeypatch)
    v = fresh_client()
    signup(v, {"email": "gone@example.com", "password": "gone long password"})
    wid = v.get("/web/me").json()["id"]
    key = {"Authorization": "Bearer " + v.post("/v1/keys", json={"name": "k"}).json()["key"]}
    cn = v.post("/v1/connections", headers=key, json={"provider": "sandbox"}).json()["id"]
    v.post("/v1/watches", headers=key, json={"connection_id": cn, "what": "enquiry"})
    H = {"X-WatchFox-Admin": "1"}
    assert k.delete(f"/v1/admin/orgs/{wid}", headers=H).status_code == 400                               # needs the typed id
    assert k.delete(f"/v1/admin/orgs/{wid}", params={"confirm": wid}, headers=H).status_code == 200
    assert v.get("/v1/connections", headers=key).status_code == 401                                      # its key is gone
    assert store.org(wid) is None and store.user_by_email("gone@example.com") is None
    assert store.one("SELECT COUNT(*) AS n FROM watches WHERE user_id=?", (wid,))["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM connections WHERE user_id=?", (wid,))["n"] == 0
    own = k.get("/web/me").json()["id"]
    assert k.delete(f"/v1/admin/orgs/{own}", params={"confirm": own}, headers=H).status_code == 400    # the admin cannot delete the organization they are in
    assert "delete organization" in [a["action"] for a in k.get("/v1/admin/audit").json()["audit"]]
