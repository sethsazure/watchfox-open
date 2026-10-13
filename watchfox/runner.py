"""Runs the mailboxes: check each one, score every new email for every watch on it, deliver matches to webhooks,
and tell the developer when a mailbox needs the person to sign in again."""
import base64
import hashlib
import hmac
import json
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Tuple
from urllib.parse import quote

import httpx

from . import abuse, billing, config, mailfetch, presets, store, vault
from .engine import ModelUnavailable, engine
from .items import Item
from .sources import AuthError, make

RETRY_DELAYS = [0, 10, 60, 300]   # seconds before each delivery attempt
PUSH_SWEEP = 600                  # safety-net check for mailboxes that get push notifications
FAILS_BEFORE_ATTENTION = 5        # consecutive failed checks (not sign-in problems) before we raise it


def sign(secret: str, ts: int, body: str) -> str:
    return hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()


def window_start(w: Dict[str, Any]) -> float:
    """Only mail received after this moment is checked by the watch (its creation time, minus any backfill)."""
    return w["created"] - w["backfill_days"] * 86400


class Runner:
    def __init__(self):
        self.stops: Dict[str, threading.Event] = {}        # connection id -> stop flag
        self.listeners: List[Callable[[str, Dict[str, Any]], None]] = []
        self.ingest_q: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self.workers_started = False

    # ---------------------------------------------------------------- events
    def emit(self, user_id: str, event: Dict[str, Any]) -> None:
        for fn in list(self.listeners):
            try:
                fn(user_id, event)
            except Exception:
                pass

    def metadata_for(self, w: Dict[str, Any]) -> Dict[str, Any]:
        conn = store.connection(w["connection_id"]) or {}
        return {**json.loads(conn.get("metadata") or "{}"), **(w.get("metadata") or {})}

    def build_event(self, w: Dict[str, Any], item: Item, results: Dict[str, Any]) -> Dict[str, Any]:
        matches = [{"what": k, "label": r["label"], "score": r["score"], "confidence": r.get("confidence"),
                    "confidence_level": r.get("confidence_level")} for k, r in results.items() if r["matched"]]
        pub = item.public()
        return {
            "id": vault.new_id("evt"),
            "type": "email.matched" if matches else "email.checked",
            "created": int(time.time()),
            "watch_id": w["id"],
            "connection_id": w["connection_id"],
            "metadata": self.metadata_for(w),
            "email": {"id": item.id, "subject": item.title, "from": item.sender, "received": int(item.received),
                      "snippet": pub["snippet"], "attachments": [{"filename": n} for n in item.attachments]},
            "matches": matches,
            "results": results,
        }

    def process(self, w: Dict[str, Any], item: Item) -> Dict[str, Any]:
        results = engine.score(item, presets.resolve(w["what"], w.get("user_id", "")), wait=True)
        event = self.build_event(w, item, results)
        store.save_item(w, item, results, event["id"])
        self.emit(w["user_id"], event)
        if event["matches"] and w.get("webhook_url"):
            threading.Thread(target=self.deliver, args=(w, event, None, item), daemon=True).start()
        return event

    def ingest(self, cid: str, item: Item) -> None:
        """An email arrived on a mailbox (found by a check or pushed): give it to every watch that has not seen it."""
        for w in store.watches_for_connection(cid):
            if store.seen(w["id"], item.id):
                continue
            if item.received < window_start(w) - 60:
                store.save_item(w, item, {})      # older than this watch: remember it, do not check it
                continue
            if (store.org(w["user_id"]) or {}).get("suspended"):
                continue                                         # an organization the admin has suspended is not checked
            if not billing.allow(w["user_id"]):
                store.set_status(w["id"], "Monthly run limit reached. Upgrade the plan, or checking resumes on the 1st.")
                continue                                 # not saved, so the email is checked once runs are available again
            conn = store.connection(w["connection_id"])
            if not abuse.mailbox_allows(w["user_id"], conn):
                store.set_status(w["id"], "This mailbox has used its free runs for the month. Upgrade the plan, or checking resumes on the 1st.")
                continue
            billing.add_run(w["user_id"])
            abuse.count_mailbox(w["user_id"], conn)
            self.process(w, item)

    # ---------------------------------------------------------------- webhook delivery
    def enrich(self, w: Dict[str, Any], event: Dict[str, Any], item: Item) -> Dict[str, Any]:
        """Add what the developer asked for to a copy of the event: links to the original message and attachments,
        and, if the watch's `include` says so, the full text, the .eml and the files. All of it lives in memory only
        for the length of the delivery; none of it is stored."""
        ev = json.loads(json.dumps(event))
        em, cid = ev["email"], w["connection_id"]
        include = set(w.get("include") or [])
        base = f"{config.BASE_URL}/v1/connections/{cid}/emails/{quote(item.id, safe='')}"
        em["links"] = {"raw": f"{base}/raw", "attachments": f"{base}/attachments"}
        if "body" in include:
            em["body"] = item.text[:262144]
        raw = item.raw
        if raw is None and (include & {"eml", "attachments"} or item.attachments):
            try:
                raw = mailfetch.fetch_raw(store.connection(cid), item.id)
            except mailfetch.TooLarge:
                raw = None
                em["note"] = f"The message is larger than the {config.MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB download limit, so it cannot be included or downloaded."
            except Exception:
                raw = None
                em["note"] = "The original message could not be read from the mailbox just now; use links.raw to try again."
        if raw is not None:
            budget = config.INLINE_MAX_BYTES
            if "eml" in include:
                if len(raw) <= budget:
                    em["eml_base64"], budget = base64.b64encode(raw).decode(), budget - len(raw)
                else:
                    em["eml_omitted"] = "Too large to include. Download it from links.raw."
            files = []
            for a in mailfetch.parse_attachments(raw):
                f = {"id": a["id"], "filename": a["filename"], "content_type": a["content_type"], "size": a["size"],
                     "url": f"{base}/attachments/{a['id']}"}
                if "attachments" in include:
                    if a["size"] <= budget:
                        f["content_base64"], budget = base64.b64encode(a["data"]).decode(), budget - a["size"]
                    else:
                        f["omitted"] = "Too large to include. Download it from url."
                files.append(f)
            em["attachments"] = files
        return ev

    def deliver(self, w: Dict[str, Any], event: Dict[str, Any], delays: List[int] = None, item: Item = None) -> Tuple[bool, int]:
        """POST the event, signed, retrying with backoff. Returns (delivered, last status code)."""
        url, delays = w["webhook_url"], delays or RETRY_DELAYS
        if item is not None:
            try:
                event = self.enrich(w, event, item)
            except Exception:
                pass
        secret = vault.decrypt(w["webhook_secret"]) if w.get("webhook_secret") else ""
        did = store.add_delivery(w["id"], event["id"], url)
        body = json.dumps(event, separators=(",", ":"))
        code, err = 0, ""
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                store.update_delivery(did, "retrying", code or None, attempt - 1, err)
                time.sleep(delay)
            ts = int(time.time())
            headers = {"Content-Type": "application/json", "User-Agent": "WatchFox-Webhook/1",
                       "X-WatchFox-Event": event["type"], "X-WatchFox-Delivery": did}
            if secret:
                headers["X-WatchFox-Signature"] = f"t={ts},v1={sign(secret, ts, body)}"
            try:
                if not config.ALLOW_INSECURE_WEBHOOKS:
                    from .server import public_host_ok

                    if not public_host_ok(url):
                        raise RuntimeError("webhook host no longer resolves to a public address")
                r = httpx.post(url, content=body, headers=headers, timeout=10, follow_redirects=False)
                code, err = r.status_code, ""
                if 200 <= code < 300:
                    store.update_delivery(did, "delivered", code, attempt)
                    return True, code
                err = f"HTTP {code}"
            except Exception as e:
                code, err = 0, f"{type(e).__name__}: {e}"
        store.update_delivery(did, "failed", code or None, len(delays), err)
        return False, code

    # ---------------------------------------------------------------- mailbox needs the person
    def flag(self, conn: Dict[str, Any], reason: str) -> None:
        """Mark the mailbox as needing attention (once) and tell the developer through every watch webhook."""
        if conn.get("status") == "needs_attention":
            return
        store.set_connection_status(conn["id"], "needs_attention", reason)
        event = {"id": vault.new_id("evt"), "type": "connection.needs_attention", "created": int(time.time()),
                 "connection_id": conn["id"], "provider": conn["provider"], "email": conn["email"], "reason": reason,
                 "metadata": json.loads(conn.get("metadata") or "{}"),
                 "action": "Create a connect link with this connection_id to let the person reconnect."}
        self.emit(conn["user_id"], event)
        for w in store.watches_for_connection(conn["id"]):
            if w.get("webhook_url"):
                threading.Thread(target=self.deliver, args=(w, event), daemon=True).start()

    # ---------------------------------------------------------------- the mailbox loops
    def start(self, w: Dict[str, Any]) -> None:
        self.start_connection(w["connection_id"])

    def start_connection(self, cid: str) -> None:
        self.start_workers()
        if cid in self.stops:
            return
        stop = threading.Event()
        self.stops[cid] = stop
        threading.Thread(target=self._loop, args=(cid, stop), daemon=True, name=f"mailbox-{cid}").start()

    def stop_connection(self, cid: str) -> None:
        ev = self.stops.pop(cid, None)
        if ev:
            ev.set()

    def start_saved(self) -> None:
        self.start_workers()
        for cid in {w["connection_id"] for w in store.watches()}:
            self.start_connection(cid)

    def active(self, wid: str) -> bool:
        w = store.watch(wid)
        return bool(w) and w["connection_id"] in self.stops

    def start_workers(self) -> None:
        if self.workers_started:
            return
        self.workers_started = True
        for i in range(2):
            threading.Thread(target=self._ingest_worker, daemon=True, name=f"ingest-{i}").start()

    def _ingest_worker(self) -> None:
        """Push notifications put (connection, message id) here; we fetch the email and hand it to the watches."""
        from . import graph

        while True:
            cid, message_id = self.ingest_q.get()
            try:
                conn = store.connection(cid)
                if not conn or conn["status"] != "active":
                    continue
                self.ingest(cid, graph.get_message(graph.token_for(conn), message_id))
            except AuthError as e:
                conn = store.connection(cid)
                if conn:
                    self.flag(conn, str(e))
            except Exception:
                pass       # the next safety-net check will find the email

    def _source(self, conn: Dict[str, Any], watches: List[Dict[str, Any]]):
        from . import vault as v

        secret = v.decrypt(conn["secret"]) if conn["secret"] else {}
        cfg = {"secret": secret, "since": min(window_start(w) for w in watches),
               "connection_id": conn["id"], "email": conn["email"]}
        return make(conn["provider"], cfg)

    def _loop(self, cid: str, stop: threading.Event) -> None:
        while not stop.is_set():
            conn = store.connection(cid)
            watches = store.watches_for_connection(cid) if conn else []
            if not conn or not watches:
                self.stops.pop(cid, None)
                return
            if conn["status"] == "needs_attention":      # never retry a rejected sign-in: it can lock the account
                stop.wait(30)
                continue
            interval = 60
            try:
                src = self._source(conn, watches)
                interval = src.interval or 60
                if conn["provider"] == "m365" and conn["delivery"] == "push":
                    interval = PUSH_SWEEP
                known = lambda i: all(store.seen(w["id"], i) for w in watches)  # noqa: E731
                for item in src.poll(known, 100):
                    if stop.is_set():
                        return
                    self.ingest(cid, item)
                for w in watches:
                    store.set_status(w["id"], None)
                store.reset_fail(cid)
            except AuthError as e:
                for w in watches:
                    store.set_status(w["id"], str(e))
                self.flag(conn, str(e))
            except ModelUnavailable as e:         # our model server, not the customer's mailbox: say so, do not count it against them
                for w in watches:
                    store.set_status(w["id"], "Waiting for the agent. Mail is kept and will be checked.")
                interval = 20
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                for w in watches:
                    store.set_status(w["id"], msg)
                if store.bump_fail(cid) >= FAILS_BEFORE_ATTENTION:
                    self.flag(conn, f"The mailbox could not be reached after several tries ({msg[:120]}).")
            stop.wait(interval)


runner = Runner()
