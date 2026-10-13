"""Local volume test (not part of the normal suite). Runs the real server on PostgreSQL with the checking agent replaced by a
stub, many accounts at once, and reports delivery and latency.

    docker run -d --name wf-test-pg -e POSTGRES_PASSWORD=testpw -p 55432:5432 postgres:16-alpine
    .venv/bin/python tests/load_local.py [accounts] [emails_each]
"""
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import psycopg

ACCOUNTS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
EMAILS = int(sys.argv[2]) if len(sys.argv) > 2 else 30
base = "postgresql://postgres:testpw@localhost:55432"
name = "wf_load_" + uuid.uuid4().hex[:6]
with psycopg.connect(base + "/postgres", autocommit=True) as c:
    c.execute(f"CREATE DATABASE {name}")
os.environ.update({"DATABASE_URL": f"{base}/{name}", "WATCHFOX_SECRET": "load-test-secret", "WATCHFOX_PASSWORD_LOGIN": "1",
                   "WATCHFOX_SIGNUPS_PER_HOUR": "100000", "WATCHFOX_NEW_ACCOUNTS_PER_IP_DAY": "100000", "WATCHFOX_NEW_ACCOUNTS_PER_HOUR": "100000",
                   "WATCHFOX_HOME": tempfile.mkdtemp(), "WATCHFOX_ENFORCE_PLANS": "0", "WATCHFOX_ALLOW_INSECURE_WEBHOOKS": "1",
                   "LAYA_URL": "", "LAYA_API_KEY": "", "LAYA_FALLBACK_KEY": "", "AZURE_AI_KEY": "", "OPENAI_API_KEY": ""})
import httpx  # noqa: E402
import uvicorn  # noqa: E402

from watchfox import config, engine as engine_mod  # noqa: E402
from watchfox.sources import imap as imap_mod  # noqa: E402

config.MAX_CONNECTIONS, config.MAX_WATCHES = 100000, 100000


def fake_score(item, whats, **kw):
    time.sleep(0.02)                                         # the remote agent takes about this long per email
    return {w["id"]: {"label": w["label"], "score": 0.9 if "quote" in (item.title + item.text).lower() else 0.1, "threshold": 0.5,
                      "exclude_score": None, "matched": "quote" in (item.title + item.text).lower(), "excluded": False} for w in whats}


engine_mod.engine.load = lambda: None
engine_mod.engine.score = fake_score
imap_mod.Imap.poll = lambda self, seen, limit: iter(())
from watchfox.server import app  # noqa: E402

got, lock = [], threading.Lock()


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        with lock:
            got.append(time.time())
        self.send_response(204); self.end_headers()

    def log_message(self, *a): pass


ThreadingHTTPServer(("127.0.0.1", 9190), Hook).__class__  # noqa
threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", 9190), Hook).serve_forever, daemon=True).start()
threading.Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=8791, log_level="error"), daemon=True).start()
for _ in range(60):
    try:
        if httpx.get("http://127.0.0.1:8791/v1/health", timeout=2).status_code == 200: break
    except Exception: time.sleep(0.5)

B = "http://127.0.0.1:8791"
lat, errors = [], []


def timed(fn):
    t = time.time()
    try:
        r = fn()
        lat.append(time.time() - t)
        if r.status_code >= 500: errors.append(r.status_code)
        return r
    except Exception as e:
        errors.append(repr(e)[:80]); lat.append(time.time() - t)


def account(i):
    c = httpx.Client(base_url=B, timeout=30)
    r = c.post("/web/signup", json={"email": f"load{i}@example.com", "password": "load test password 123"})
    import sqlite3  # noqa
    return c, r


t0 = time.time()
clients = []
for i in range(ACCOUNTS):
    c, r = account(i)
    assert r.status_code == 200, r.text
    clients.append(c)
with psycopg.connect(f"{base}/{name}", autocommit=True) as conn:
    conn.execute("UPDATE users SET verified=1")
heads = []
for c in clients:
    k = c.post("/v1/keys", json={"name": "load"}).json()["key"]
    h = {"Authorization": f"Bearer {k}"}
    cn = c.post("/v1/connections", headers=h, json={"provider": "sandbox"}).json()["id"]
    c.post("/v1/watches", headers=h, json={"connection_id": cn, "what": ["enquiry", "invoice"], "webhook_url": "http://127.0.0.1:9190/h"})
    heads.append((c, h, cn))
print(f"setup of {ACCOUNTS} accounts: {time.time() - t0:.1f}s")

sent = [0]


def push(c, h, cn):
    for j in range(EMAILS):
        r = timed(lambda: c.post(f"/v1/connections/{cn}/sandbox-emails", headers=h, json={"subject": f"Quote request {j}", "body": "Please send a quote for 40 units " + "x" * 400, "sender": "p@acme.com"}))
        if r is not None and r.status_code == 200: sent[0] += 1


def reader(c, h, stop):
    while not stop.is_set():
        for path in ("/v1/matches?limit=20", "/v1/log?limit=50", "/v1/watches", "/v1/connections", "/v1/usage", "/v1/emails?limit=20"):
            timed(lambda: c.get(path, headers=h))


stop = threading.Event()
readers = [] if os.getenv('NOREAD') else [threading.Thread(target=reader, args=(c, h, stop)) for c, h, _ in heads[: max(4, ACCOUNTS // 2)]]
[t.start() for t in readers]
t1 = time.time()
pushers = [threading.Thread(target=push, args=a) for a in heads]
[t.start() for t in pushers]
[t.join() for t in pushers]
print(f"{sent[0]} emails accepted in {time.time() - t1:.1f}s")
want = ACCOUNTS * EMAILS
deadline = time.time() + int(os.getenv('WAIT', '240'))
while len(got) < want and time.time() < deadline:
    time.sleep(1)
done = time.time() - t1
stop.set(); [t.join() for t in readers]
ls = sorted(lat) or [0]
print(f"webhooks delivered: {len(got)} of {want} in {done:.1f}s  ({len(got) / done:.1f}/s)")
print(f"requests: {len(ls)}  median {statistics.median(ls) * 1000:.0f} ms  p95 {ls[int(len(ls) * .95)] * 1000:.0f} ms  p99 {ls[int(len(ls) * .99)] * 1000:.0f} ms  max {ls[-1] * 1000:.0f} ms  errors {len(errors)} {set(map(str, errors))}")
with psycopg.connect(f"{base}/{name}", autocommit=True) as conn:
    print("log rows:", conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], " deliveries:", conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0])
    conn.close()
if not os.getenv("DEBUG_DB"):
    with psycopg.connect(base + "/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE {name} WITH (FORCE)")
else:
    print("database kept:", name)
if os.getenv("DEBUG_DB"):
    pass
os._exit(0)
