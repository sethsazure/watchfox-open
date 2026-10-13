"""End-to-end test of the cloud API against a running server (needs an engine, so it is not run by pytest).

    MS_CLIENT_ID= MS_CLIENT_SECRET= WATCHFOX_HOME=/tmp/wf WATCHFOX_PORT=8791 WATCHFOX_ALLOW_INSECURE_WEBHOOKS=1 watchfox serve &
    python tests/e2e_cloud.py
"""
import hashlib, hmac, json, os, subprocess, sys, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
B = "http://localhost:8791"
received = []
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        received.append((dict(self.headers), body)); self.send_response(204); self.end_headers()
    def log_message(self, *a): pass
threading.Thread(target=HTTPServer(("127.0.0.1", 9101), H).serve_forever, daemon=True).start()

def call(method, path, body=None, key=None, cookie=None, expect=200):
    h = {"Content-Type": "application/json"}
    if key: h["Authorization"] = f"Bearer {key}"
    if cookie: h["Cookie"] = cookie
    req = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req); data = json.loads(r.read() or b"{}"); sc = r.status; hd = r.headers
    except urllib.error.HTTPError as e:
        data = json.loads(e.read() or b"{}"); sc = e.code; hd = e.headers
    assert sc == expect, f"{method} {path} -> {sc} {data} (wanted {expect})"
    return data, hd
def check(label, cond): print(("PASS " if cond else "FAIL "), label); assert cond

print("== accounts and keys")
d, hd = call("POST", "/web/signup", {"email": "dev@example.com", "password": "correct horse battery"})
urllib.request.urlopen(d["dev_link"])        # the emailed verification link (shown in the response on a local install)
cookie = hd["Set-Cookie"].split(";")[0]
call("POST", "/web/signup", {"email": "dev@example.com", "password": "correct horse battery"}, expect=409)
call("POST", "/web/signup", {"email": "x@example.com", "password": "short"}, expect=400)
call("POST", "/web/login", {"email": "dev@example.com", "password": "wrong password!"}, expect=401)
call("GET", "/v1/keys", expect=401)
key = call("POST", "/v1/keys", {"name": "ci"}, cookie=cookie)[0]["key"]
check("key looks right", key.startswith("wf_live_"))
check("key works", call("GET", "/v1/keys", key=key)[0][0]["name"] == "ci")
call("GET", "/v1/keys", key="wf_live_nope", expect=401)

print("== providers / presets")
prov = call("GET", "/v1/providers", key=key)[0]
check("providers", [p["id"] for p in prov["providers"]] == ["gmail", "m365", "imap", "sandbox"] and len(prov["coming_soon"]) == 4)
check("gmail available, microsoft needs server setup", prov["providers"][0]["available"] and not prov["providers"][1]["available"])
gl = call("POST", "/v1/connect-links", {"provider": "gmail", "state": "s1", "app_name": "Demo"}, key=key)[0]
check("gmail connect link is a hosted page", "/connect?t=" in gl["url"])
call("POST", "/v1/connect-links", {"provider": "m365"}, key=key, expect=503)   # not configured on this test server
check("6 presets, no model name", len(call("GET", "/v1/presets", key=key)[0]) == 6)

print("== sandbox mailbox + watch + signed webhook")
cn = call("POST", "/v1/connections", {"provider": "sandbox", "metadata": {"customer_id": "c7"}}, key=key)[0]
w = call("POST", "/v1/watches", {"connection_id": cn["id"], "what": ["enquiry", "invoice"], "webhook_url": "http://127.0.0.1:9101/hook"}, key=key)[0]
check("webhook secret returned once", w["webhook_secret"].startswith("whsec_"))
check("secret not returned on GET", "webhook_secret" not in call("GET", f"/v1/watches/{w['id']}", key=key)[0])
call("POST", "/v1/watches", {"connection_id": "cn_missing"}, key=key, expect=404)
call("POST", "/v1/watches", {"connection_id": cn["id"], "what": "nope"}, key=key, expect=400)
for sub, body, frm in [("Pricing for 40 licences", "Hi, could you send us a quote for 40 licences with implementation? Need it by Friday.", "priya@acme.com"),
                       ("Invoice INV-2291", "Please find attached invoice INV-2291 for $2,450 due in 30 days.", "billing@supplier.com"),
                       ("Weekly digest", "Here is your weekly newsletter with product updates. Unsubscribe any time.", "news@vendor.com")]:
    call("POST", f"/v1/connections/{cn['id']}/sandbox-emails", {"subject": sub, "body": body, "sender": frm}, key=key)
t0 = time.time()
while time.time() - t0 < 60 and len(received) < 2: time.sleep(1)
check("2 webhooks received (enquiry + invoice, not the newsletter)", len(received) == 2)
for hdrs, body in received:
    ev = json.loads(body); ts, sig = [p.split("=", 1)[1] for p in hdrs["X-WatchFox-Signature"].split(",")]
    good = hmac.new(w["webhook_secret"].encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    check(f"signature valid for {ev['email']['subject'][:24]!r} -> {[m['label'] for m in ev['matches']]}", hmac.compare_digest(sig, good) and ev["type"] == "email.matched")
check("metadata echoed", json.loads(received[0][1])["metadata"] == {"customer_id": "c7"})
check("payload has no model/provider details", all(k not in received[0][1].lower() for k in ("laya", "typed", "modernbert")))
time.sleep(2)
ms = call("GET", "/v1/matches", key=key)[0]; em = call("GET", "/v1/emails", key=key)[0]
check("matches=2 emails=3", len(ms) == 2 and len(em) == 3)
dl = call("GET", f"/v1/watches/{w['id']}/deliveries", key=key)[0]
check("deliveries recorded", len(dl) == 2 and all(x["status"] == "delivered" for x in dl))
t = call("POST", f"/v1/watches/{w['id']}/test", key=key)[0]
check("test webhook", t["delivered"] and t["status_code"] == 204)

print("== isolation between accounts")
d2, hd2 = call("POST", "/web/signup", {"email": "other@example.com", "password": "another long password"})
urllib.request.urlopen(d2["dev_link"])
key2 = call("POST", "/v1/keys", {"name": "k"}, cookie=hd2["Set-Cookie"].split(";")[0])[0]["key"]
check("other account sees nothing", call("GET", "/v1/matches", key=key2)[0] == [] and call("GET", "/v1/watches", key=key2)[0] == [] and call("GET", "/v1/connections", key=key2)[0] == [])
call("GET", f"/v1/watches/{w['id']}", key=key2, expect=404)
call("DELETE", f"/v1/watches/{w['id']}", key=key2, expect=404)
call("POST", "/v1/watches", {"connection_id": cn["id"]}, key=key2, expect=404)
call("POST", f"/v1/connections/{cn['id']}/sandbox-emails", {"body": "x"}, key=key2, expect=404)

print("== classify and demo")
r = call("POST", "/v1/classify", {"subject": "RFQ", "body": "Please quote 12 door operators, spec attached.", "what": "enquiry"}, key=key)[0]
check("classify returns verdict, no engine names", r["matched"] == ["Enquiries"] and "model" not in r)
dm = call("POST", "/v1/demo", {"text": "Can you send us pricing for 40 licences?", "what": "enquiry"})[0]
check("demo works unauthenticated", dm["matched"])
call("POST", "/v1/demo", {"text": "x", "what": "bogus"}, expect=400)
print("== IMAP with bad host is rejected cleanly")
call("POST", "/v1/connections", {"provider": "imap", "host": "127.0.0.1", "port": 1, "user": "a@b.com", "password": "x"}, key=key, expect=400)
print("== cleanup")
call("DELETE", f"/v1/watches/{w['id']}", key=key); call("DELETE", f"/v1/connections/{cn['id']}", key=key)
check("deleted", call("GET", "/v1/watches", key=key)[0] == [])
print("ALL PASSED")
